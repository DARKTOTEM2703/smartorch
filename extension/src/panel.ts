/**
 * Chat nativo de SmartOrch en VS Code.
 *
 * La interfaz (la misma que usa la web, una sola fuente) se carga como webview propio:
 * no es un iframe. Todas las peticiones al servidor las hace la extension y el webview
 * solo recibe resultados, asi la API key nunca llega al HTML y los dialogos (renombrar,
 * borrar) usan los de VS Code.
 */
import * as fs from "fs";
import * as vscode from "vscode";
import { isServerRunning, startSmartOrchServer } from "./runtime";
import { serverRoot } from "./bridge";
import { bridgeScript } from "./bridge-script";

const MAX_TEXT = 200_000;
const ALLOWED_PATHS = [/^\/v1\//, /^\/smartorch\//, /^\/health$/];

class ChatSurface {
  private streams = new Map<number, AbortController>();
  private queue: object[] = [];
  private poll?: NodeJS.Timeout;
  private ready = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly webview: vscode.Webview,
  ) {
    webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
    };
    webview.onDidReceiveMessage((m) => void this.onMessage(m));
  }

  dispose() {
    this.stopPolling();
    this.streams.forEach((c) => c.abort());
    this.streams.clear();
  }

  async render() {
    this.stopPolling();
    this.ready = false;
    if (await isServerRunning()) {
      this.webview.html = this.chatHtml();
    } else {
      this.webview.html = this.offlineHtml();
      this.poll = setInterval(async () => {
        if (await isServerRunning()) void this.render();
      }, 2500);
    }
  }

  /** Envia un mensaje a la interfaz; si aun no esta lista lo guarda. */
  post(message: object) {
    if (this.ready) void this.webview.postMessage(message);
    else this.queue.push(message);
  }

  private stopPolling() {
    if (this.poll) clearInterval(this.poll);
    this.poll = undefined;
  }

  private nonce() {
    return Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
  }

  private uri(...parts: string[]) {
    return this.webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", ...parts));
  }

  private chatHtml(): string {
    const file = vscode.Uri.joinPath(this.context.extensionUri, "media", "chat", "index.html").fsPath;
    if (!fs.existsSync(file)) {
      return this.messageHtml("No encuentro la interfaz del chat. Reinstala la extensión.");
    }
    const nonce = this.nonce();
    const csp = `default-src 'none'; style-src ${this.webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; img-src ${this.webview.cspSource} data:; font-src ${this.webview.cspSource};`;
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
    const bridge = `<script nonce="${nonce}">${bridgeScript(workspace)}</script>`;

    return fs
      .readFileSync(file, "utf8")
      .replace("<head>", `<head>\n<meta http-equiv="Content-Security-Policy" content="${csp}">`)
      .replace(
        '<link rel="stylesheet" href="/static/app.css">',
        `<link rel="stylesheet" href="${this.uri("chat", "app.css")}">\n<link rel="stylesheet" href="${this.uri("vscode-theme.css")}">`,
      )
      .replace(
        '<script src="/static/app.js"></script>',
        `${bridge}\n<script nonce="${nonce}" src="${this.uri("chat", "app.js")}"></script>`,
      );
  }

  private messageHtml(text: string): string {
    return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);color:var(--vscode-foreground);padding:20px">${text}</body></html>`;
  }

  private offlineHtml(): string {
    const nonce = this.nonce();
    return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  body{font-family:var(--vscode-font-family);color:var(--vscode-foreground);padding:20px;text-align:center}
  h2{margin:.4rem 0}.bolt{font-size:34px}
  p{color:var(--vscode-descriptionForeground);font-size:13px;line-height:1.5}
  button{display:block;width:100%;margin:8px 0;padding:8px;border:0;border-radius:4px;cursor:pointer;
    background:var(--vscode-button-background);color:var(--vscode-button-foreground);font-size:13px}
  button.sec{background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground)}
</style></head><body>
<div class="bolt">⚡</div><h2>SmartOrch</h2>
<p>El servidor local no responde. Inícialo para chatear con tus modelos, sin enviar código a la nube.</p>
<button id="start">▶ Iniciar SmartOrch</button>
<button id="setup" class="sec">⚙ Configuración inicial</button>
<p id="msg"></p>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  document.getElementById('start').onclick = () => { vscode.postMessage({smartorch:true, type:'start'}); document.getElementById('msg').textContent = 'Iniciando… esto puede tardar unos segundos.'; };
  document.getElementById('setup').onclick = () => vscode.postMessage({smartorch:true, type:'setup'});
</script></body></html>`;
  }

  // ── Mensajes del webview ──────────────────────────────────────────────────
  private async onMessage(m: any) {
    if (!m || m.smartorch !== true) return;
    switch (m.type) {
      case "ready":
        this.ready = true;
        this.queue.splice(0).forEach((q) => void this.webview.postMessage(q));
        break;
      case "http":
        await this.http(m);
        break;
      case "stream":
        await this.stream(m);
        break;
      case "abort":
        this.streams.get(m.id)?.abort();
        break;
      case "dialog":
        await this.dialog(m);
        break;
      case "start":
        await startSmartOrchServer(this.context, true);
        void this.render();
        break;
      case "setup":
        await vscode.commands.executeCommand("smartorch.onboarding");
        break;
      case "getContext":
        this.post({ type: "context", ...editorContext() });
        break;
      case "insert":
        await insertText(String(m.text ?? "").slice(0, MAX_TEXT));
        break;
      case "newFile":
        await newFile(String(m.text ?? "").slice(0, MAX_TEXT), typeof m.language === "string" ? m.language : undefined);
        break;
      case "openFile":
        await openFile(String(m.path ?? ""));
        break;
    }
  }

  private headers() {
    const key = vscode.workspace.getConfiguration("smartorch").get<string>("apiKey", "smartorch-local-key");
    return { Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
  }

  private allowed(path: unknown): path is string {
    return typeof path === "string" && ALLOWED_PATHS.some((re) => re.test(path.split("?")[0]));
  }

  private async http(m: any) {
    const reply = (extra: object) => void this.webview.postMessage({ type: "http-result", id: m.id, ...extra });
    if (!this.allowed(m.path)) return reply({ ok: false, status: 403, error: "ruta no permitida" });
    try {
      const res = await fetch(`${serverRoot()}${m.path}`, {
        method: m.method || "GET",
        headers: this.headers(),
        body: typeof m.body === "string" ? m.body : undefined,
        signal: AbortSignal.timeout(60_000),
      });
      const isJson = (res.headers.get("content-type") || "").includes("json");
      let data: any = isJson ? await res.json() : await res.text();
      if (m.path.split("?")[0] === "/smartorch/ui-config" && data && typeof data === "object") {
        data = { ...data, api_key: "", auth: false }; // la key nunca llega al webview
      }
      if (!res.ok) return reply({ ok: false, status: res.status, error: `${res.status} ${res.statusText}` });
      reply({ ok: true, status: res.status, data });
    } catch (e: any) {
      reply({ ok: false, status: 0, error: e?.message ?? String(e) });
    }
  }

  private async stream(m: any) {
    const send = (extra: object) => void this.webview.postMessage({ id: m.id, ...extra });
    if (!this.allowed(m.path)) return send({ type: "stream-error", message: "ruta no permitida" });
    const controller = new AbortController();
    this.streams.set(m.id, controller);
    try {
      const res = await fetch(`${serverRoot()}${m.path}`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(m.body ?? {}),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let i: number;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 2);
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            send({ type: "stream-event", event: JSON.parse(payload) });
          } catch {
            /* evento incompleto */
          }
        }
      }
      send({ type: "stream-end" });
    } catch (e: any) {
      if (controller.signal.aborted) send({ type: "stream-end" });
      else send({ type: "stream-error", message: e?.message ?? String(e) });
    } finally {
      this.streams.delete(m.id);
    }
  }

  private async dialog(m: any) {
    let value: string | boolean | undefined;
    if (m.kind === "prompt") {
      value = await vscode.window.showInputBox({ prompt: String(m.text ?? ""), value: String(m.value ?? "") });
    } else {
      const pick = await vscode.window.showWarningMessage(String(m.text ?? ""), { modal: true }, "Aceptar");
      value = pick === "Aceptar";
    }
    void this.webview.postMessage({ type: "dialogResult", id: m.id, value });
  }
}

// ── Acciones sobre el editor ────────────────────────────────────────────────

function editorContext() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return { file: "", language: "", selection: "", text: "" };
  const sel = editor.selection;
  return {
    file: vscode.workspace.asRelativePath(editor.document.uri),
    language: editor.document.languageId,
    selection: sel.isEmpty ? "" : editor.document.getText(sel),
    text: sel.isEmpty ? editor.document.getText().slice(0, 12_000) : "",
  };
}

async function insertText(text: string) {
  const editor = vscode.window.activeTextEditor ?? vscode.window.visibleTextEditors[0];
  if (!editor) {
    void vscode.window.showWarningMessage("SmartOrch: abre un archivo para insertar el código.");
    return;
  }
  await editor.edit((b) => {
    for (const sel of editor.selections) {
      if (sel.isEmpty) b.insert(sel.start, text);
      else b.replace(sel, text);
    }
  });
  await vscode.window.showTextDocument(editor.document, editor.viewColumn);
}

async function newFile(text: string, language?: string) {
  const doc = await vscode.workspace.openTextDocument({ content: text, language });
  await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside });
}

async function openFile(rel: string) {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder || !rel) return;
  const uri = vscode.Uri.joinPath(folder.uri, rel);
  if (!uri.fsPath.toLowerCase().startsWith(folder.uri.fsPath.toLowerCase())) return;
  try {
    await vscode.window.showTextDocument(uri, { preview: true });
  } catch {
    void vscode.window.showWarningMessage(`SmartOrch: no pude abrir ${rel}`);
  }
}

// ── Vista lateral + pestaña ────────────────────────────────────────────────

export class ChatPanel implements vscode.WebviewViewProvider {
  static readonly viewId = "smartorch.chatView";

  private side?: ChatSurface;
  private sideView?: vscode.WebviewView;
  private tab?: { panel: vscode.WebviewPanel; surface: ChatSurface };

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView) {
    this.sideView = view;
    this.side = new ChatSurface(this.context, view.webview);
    view.onDidChangeVisibility(() => view.visible && void this.side?.render());
    view.onDidDispose(() => {
      this.side?.dispose();
      this.side = undefined;
      this.sideView = undefined;
    });
    void this.side.render();
  }

  /** Abre el chat en una pestaña del editor (ancho: muestra el historial al lado). */
  openTab() {
    if (this.tab) {
      this.tab.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel("smartorch.chatTab", "SmartOrch", vscode.ViewColumn.Beside, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    panel.iconPath = vscode.Uri.joinPath(this.context.extensionUri, "media", "icons", "smartorch-activity.svg");
    const surface = new ChatSurface(this.context, panel.webview);
    this.tab = { panel, surface };
    panel.onDidDispose(() => {
      surface.dispose();
      this.tab = undefined;
    });
    void surface.render();
  }

  /** Superficie activa: la pestaña si existe, si no la barra lateral. */
  private target(): ChatSurface | undefined {
    return this.tab?.surface ?? this.side;
  }

  async sendPrompt(text: string, autoSend = true) {
    if (!this.tab) await vscode.commands.executeCommand("workbench.view.extension.smartorch");
    this.target()?.post({ type: "prompt", text, send: autoSend });
  }

  async openConversation(id: string) {
    if (!this.tab) await vscode.commands.executeCommand("workbench.view.extension.smartorch");
    this.target()?.post({ type: "open", conv: id });
  }

  refresh() {
    void this.side?.render();
    void this.tab?.surface.render();
  }
}
