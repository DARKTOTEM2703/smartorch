/**
 * Panel de chat de SmartOrch: embebe la web de SmartOrch (misma UI que en el navegador)
 * y la conecta con el editor (archivo activo, insertar codigo, abrir conversaciones).
 */
import * as vscode from "vscode";
import { isServerRunning, startSmartOrchServer } from "./runtime";
import { serverRoot } from "./bridge";

type WebMessage =
  | { type: "getContext" }
  | { type: "insert"; text: string }
  | { type: "newFile"; text: string; language?: string }
  | { type: "openFile"; path: string };

const MAX_TEXT = 200_000;

export class ChatPanel implements vscode.WebviewViewProvider {
  static readonly viewId = "smartorch.chatView";

  private view?: vscode.WebviewView;
  private queue: object[] = [];
  private poll?: NodeJS.Timeout;

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view;
    const origin = new URL(serverRoot());
    const port = Number(origin.port || (origin.protocol === "https:" ? 443 : 80));
    view.webview.options = {
      enableScripts: true,
      portMapping: [{ webviewPort: port, extensionHostPort: port }],
    };
    view.webview.onDidReceiveMessage((m) => void this.onMessage(m));
    view.onDidChangeVisibility(() => view.visible && void this.render());
    view.onDidDispose(() => {
      this.view = undefined;
      this.stopPolling();
    });
    void this.render();
  }

  /** Muestra el panel y envia un prompt (p. ej. desde el menu contextual). */
  async sendPrompt(text: string, autoSend = true) {
    await vscode.commands.executeCommand("workbench.view.extension.smartorch");
    this.post({ type: "prompt", text, send: autoSend });
  }

  async openConversation(id: string) {
    await vscode.commands.executeCommand("workbench.view.extension.smartorch");
    this.post({ type: "open", conv: id });
  }

  refresh() {
    void this.render();
  }

  // ── Render ────────────────────────────────────────────────────────────────
  private async render() {
    const view = this.view;
    if (!view) return;
    this.stopPolling();
    if (await isServerRunning()) {
      view.webview.html = this.frameHtml(view.webview);
      this.flushQueue();
    } else {
      view.webview.html = this.offlineHtml(view.webview);
      this.poll = setInterval(async () => {
        if (await isServerRunning()) void this.render();
      }, 2500);
    }
  }

  private stopPolling() {
    if (this.poll) clearInterval(this.poll);
    this.poll = undefined;
  }

  private nonce() {
    return Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
  }

  private frameHtml(webview: vscode.Webview): string {
    const origin = new URL(serverRoot()).origin;
    const nonce = this.nonce();
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
    const src = `${origin}/?embed=vscode&workspace=${encodeURIComponent(root)}`;
    return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>html,body,iframe{margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;background:var(--vscode-sideBar-background)}</style>
</head><body>
<iframe id="f" src="${src}" allow="clipboard-read; clipboard-write"></iframe>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const frame = document.getElementById('f');
  const ORIGIN = ${JSON.stringify(origin)};
  window.addEventListener('message', (e) => {
    if (e.source === frame.contentWindow) {
      if (e.origin === ORIGIN && e.data && e.data.smartorch) vscode.postMessage(e.data);
    } else {
      frame.contentWindow.postMessage(e.data, ORIGIN);
    }
  });
</script></body></html>`;
  }

  private offlineHtml(webview: vscode.Webview): string {
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

  // ── Mensajes ──────────────────────────────────────────────────────────────
  private post(message: object) {
    if (this.view?.visible && this.view.webview) {
      void this.view.webview.postMessage(message);
    } else {
      this.queue.push(message);
    }
  }

  private flushQueue() {
    const pending = this.queue.splice(0);
    // el iframe tarda en cargar: reintentar un instante despues
    setTimeout(() => pending.forEach((m) => void this.view?.webview.postMessage(m)), 1200);
  }

  private async onMessage(m: any) {
    if (!m || m.smartorch !== true) return;
    switch (m.type) {
      case "start":
        await startSmartOrchServer(this.context, true);
        void this.render();
        break;
      case "setup":
        await vscode.commands.executeCommand("smartorch.onboarding");
        break;
      case "getContext":
        this.post({ type: "context", ...this.editorContext() });
        break;
      case "insert":
        await this.insertText(String(m.text ?? "").slice(0, MAX_TEXT));
        break;
      case "newFile":
        await this.newFile(String(m.text ?? "").slice(0, MAX_TEXT), typeof m.language === "string" ? m.language : undefined);
        break;
      case "openFile":
        await this.openFile(String(m.path ?? ""));
        break;
    }
  }

  private editorContext() {
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

  private async insertText(text: string) {
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

  private async newFile(text: string, language?: string) {
    const doc = await vscode.workspace.openTextDocument({ content: text, language });
    await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside });
  }

  private async openFile(rel: string) {
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
}
