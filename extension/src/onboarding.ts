/**
 * SmartOrch Onboarding Panel
 * Muestra análisis del PC y permite hacer ollama pull de los modelos recomendados.
 */
import * as vscode from "vscode";
import {
  detect,
  recommendStack,
  SystemInfo,
  ModelStack,
} from "./system-detector";
import {
  DataInfo,
  fetchDataInfo,
  pickDataFolder,
  resetDataFolder,
  freeGb,
  isCustomModelsPath,
  modelsPath,
  ollamaExe,
  ollamaTerminal,
  pickModelsFolder,
  pickOllamaExecutable,
  resetStorage,
  withOllamaExe,
} from "./storage";
import {
  currentRuntime,
  detectRuntimes,
  RuntimeId,
  RuntimeOption,
  selectRuntime,
  startSmartOrchServer,
} from "./runtime";

export class OnboardingPanel {
  static currentPanel: OnboardingPanel | undefined;
  private readonly _panel: vscode.WebviewPanel;
  private _disposables: vscode.Disposable[] = [];

  static async show(context: vscode.ExtensionContext) {
    if (OnboardingPanel.currentPanel) {
      OnboardingPanel.currentPanel._panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "smartorchOnboarding",
      "SmartOrch — Configuración inicial",
      vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    OnboardingPanel.currentPanel = new OnboardingPanel(panel, context);
  }

  private constructor(
    panel: vscode.WebviewPanel,
    context: vscode.ExtensionContext,
  ) {
    this._panel = panel;

    this._panel.webview.html = _loadingHtml();
    this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

    this._panel.webview.onDidReceiveMessage(
      async (msg) => {
        if (msg.command === "pull") {
          const term = ollamaTerminal("SmartOrch — Ollama Pull");
          term.sendText(withOllamaExe(msg.pullCmd));
          term.show();
        }
        if (msg.command === "pickModels" && (await pickModelsFolder())) {
          await this._loadData(context);
        }
        if (msg.command === "pickOllama" && (await pickOllamaExecutable())) {
          await this._loadData(context);
        }
        if (msg.command === "pickData" && (await pickDataFolder())) {
          await this._loadData(context);
        }
        if (msg.command === "resetData" && (await resetDataFolder())) {
          await this._loadData(context);
        }
        if (msg.command === "resetStorage") {
          await resetStorage();
          await this._loadData(context);
        }
        if (msg.command === "openOllamaDownload") {
          void vscode.env.openExternal(
            vscode.Uri.parse("https://ollama.com/download"),
          );
        }
        if (msg.command === "serve") {
          await startSmartOrchServer(undefined, true);
        }
        if (msg.command === "runtime") {
          await selectRuntime(msg.id as RuntimeId, msg.url as string);
          await this._loadData(context);
        }
        if (msg.command === "pullAll") {
          const term = ollamaTerminal("SmartOrch — Pull modelos");
          for (const cmd of msg.cmds as string[]) {
            term.sendText(withOllamaExe(cmd));
          }
          term.show();
        }
      },
      null,
      this._disposables,
    );

    this._loadData(context);
  }

  private async _loadData(context: vscode.ExtensionContext) {
    const wsPaths = (vscode.workspace.workspaceFolders ?? []).map(
      (f) => f.uri.fsPath,
    );
    let sys: SystemInfo;
    try {
      sys = await detect(wsPaths);
    } catch (e: any) {
      this._panel.webview.html = _errorHtml(e.message);
      return;
    }
    const stack = recommendStack(sys);
    const dataInfo = await fetchDataInfo();
    this._panel.webview.html = _buildHtml(
      sys,
      stack,
      detectRuntimes(),
      currentRuntime(),
      vscode.workspace
        .getConfiguration("smartorch")
        .get<string>("apiUrl", "http://localhost:8080/v1"),
      dataInfo,
    );
  }

  dispose() {
    OnboardingPanel.currentPanel = undefined;
    this._panel.dispose();
    for (const d of this._disposables) d.dispose();
    this._disposables = [];
  }
}

// ── HTML builders ─────────────────────────────────────────────────────────────

function _loadingHtml(): string {
  return `<!DOCTYPE html><html><body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
    <div style="text-align:center">
      <div style="font-size:2rem;margin-bottom:1rem">⚡</div>
      <div style="color:#00d4ff;font-size:1.1rem">Analizando tu sistema...</div>
    </div>
  </body></html>`;
}

function _errorHtml(msg: string): string {
  return `<!DOCTYPE html><html><body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;padding:2rem">
    <h2 style="color:#ff6b6b">Error al detectar sistema</h2>
    <pre style="background:#161b22;padding:1rem;border-radius:6px;color:#f85149">${_esc(msg)}</pre>
  </body></html>`;
}

function _runtimeCards(
  runtimes: RuntimeOption[],
  selected: RuntimeId,
  apiUrl: string,
): string {
  return runtimes
    .map((r) => {
      const isSel = r.id === selected;
      const badge = r.available
        ? `<span style="color:#3fb950">● disponible</span>`
        : `<span style="color:#f0883e">● no detectado</span>`;
      const remoteInput =
        r.id === "remote"
          ? `<input id="remoteUrl" value="${_esc(apiUrl.startsWith("http://localhost") ? "" : apiUrl)}" placeholder="https://mi-servidor:8080" style="width:100%;margin-top:.5rem;padding:.4rem;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px">`
          : "";
      return `
      <div class="rt ${isSel ? "rt-sel" : ""}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <strong>${_esc(r.label)}</strong><span style="font-size:.75rem">${badge}</span>
        </div>
        <p style="color:#8b949e;font-size:.8rem;margin:.35rem 0">${_esc(r.description)}</p>
        <p style="color:#6e7681;font-size:.75rem">${_esc(r.detail)}</p>
        ${remoteInput}
        <button onclick="useRuntime('${r.id}')" style="${BTN_STYLE}background:${isSel ? "#238636" : "#1f6feb"};margin-top:.6rem">${isSel ? "✔ En uso — reiniciar" : "Usar esta opción"}</button>
      </div>`;
    })
    .join("");
}

function _buildHtml(
  sys: SystemInfo,
  stack: ModelStack,
  runtimes: RuntimeOption[],
  selectedRuntime: RuntimeId,
  apiUrl: string,
  dataInfo: DataInfo | undefined,
): string {
  const ollama = sys.ollama;
  const gpu = sys.gpu;
  const storageFree = freeGb(modelsPath());
  const pulled = new Set(ollama?.pulledModels ?? []);
  const allCmds = stack.models.map((m) => m.pull);

  const modelRows = stack.models
    .map((m) => {
      const isPulled = [...pulled].some((p) =>
        p.startsWith(m.name.split(":")[0]),
      );
      const badge = isPulled
        ? `<span style="color:#3fb950;font-size:.75rem">✔ instalado</span>`
        : `<button onclick="pull('${_esc(m.pull)}','${_esc(m.name)}')" style="${BTN_STYLE}background:#1f6feb">Pull ${_esc(m.name)}</button>`;
      return `
      <tr>
        <td style="padding:.5rem .75rem;color:#e6edf3">${_esc(m.name)}</td>
        <td style="padding:.5rem .75rem;color:#8b949e">${_esc(m.role)}</td>
        <td style="padding:.5rem .75rem;color:#8b949e">${m.sizeGb} GB</td>
        <td style="padding:.5rem .75rem">${badge}</td>
      </tr>`;
    })
    .join("");

  const ollamaStatus = ollama?.running
    ? `<span style="color:#3fb950">● corriendo</span> v${ollama.version}`
    : ollama
      ? `<span style="color:#f0883e">● instalado pero no corre</span> v${ollama.version}`
      : `<span style="color:#f85149">● no encontrado</span>`;

  const gpuStr = gpu
    ? `${gpu.name} · ${(gpu.vramMb / 1024).toFixed(1)} GB VRAM`
    : "No detectada (se usará RAM)";

  const tierColors: Record<string, string> = {
    minimal: "#f0883e",
    standard: "#d29922",
    full: "#3fb950",
    pro: "#00d4ff",
  };
  const tierColor = tierColors[stack.tier] ?? "#e6edf3";

  return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartOrch Setup</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:2rem;max-width:860px;margin:0 auto}
  h1{font-size:1.6rem;color:#00d4ff;margin-bottom:.25rem}
  .sub{color:#8b949e;font-size:.9rem;margin-bottom:2rem}
  .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:1.25rem;margin-bottom:1.25rem}
  .card-title{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:#8b949e;margin-bottom:.75rem}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
  .kv{display:flex;flex-direction:column;gap:.15rem}
  .kv label{font-size:.72rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}
  .kv span{color:#e6edf3;font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  tr:nth-child(even) td{background:#0d1117}
  .tier-badge{display:inline-block;padding:.2rem .65rem;border-radius:20px;font-size:.8rem;font-weight:600;border:1px solid currentColor}
  .actions{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:1.25rem}
  .rts{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:.75rem}
  .rt{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:.9rem}
  .rt-sel{border-color:#238636}
</style>
</head>
<body>
<h1>⚡ SmartOrch — Configuración inicial</h1>
<p class="sub">Análisis de hardware completado · ${sys.os}</p>

<div class="card">
  <div class="card-title">¿Dónde quieres correr SmartOrch?</div>
  <div class="rts">${_runtimeCards(runtimes, selectedRuntime, apiUrl)}</div>
</div>

<div class="card">
  <div class="card-title">Almacenamiento — dónde viven Ollama y los modelos</div>
  <div class="grid2">
    <div class="kv"><label>Carpeta de modelos ${isCustomModelsPath() ? "(personalizada)" : "(por defecto)"}</label><span style="word-break:break-all">${_esc(modelsPath())}</span></div>
    <div class="kv"><label>Espacio libre en ese disco</label><span style="color:${storageFree !== undefined && storageFree < stack.totalDiskGb ? "#f85149" : "#3fb950"}">${storageFree !== undefined ? storageFree + " GB" : "desconocido"} <span style="color:#8b949e">· el stack necesita ${stack.totalDiskGb} GB</span></span></div>
    <div class="kv"><label>Ejecutable de Ollama</label><span style="word-break:break-all">${ollamaExe() === "ollama" ? "Del PATH del sistema" : _esc(ollamaExe())}</span></div>
  </div>
  ${storageFree !== undefined && storageFree < stack.totalDiskGb ? `<p style="color:#f0883e;font-size:.8rem;margin-top:.6rem">⚠ Poco espacio para este stack. Elige otro disco con "Cambiar carpeta de modelos".</p>` : ""}
  <div class="actions">
    <button onclick="send('pickModels')" style="${BTN_STYLE}background:#1f6feb">📁 Cambiar carpeta de modelos</button>
    <button onclick="send('pickOllama')" style="${BTN_STYLE}background:#1f6feb">📂 Elegir Ollama…</button>
    <button onclick="send('openOllamaDownload')" style="${BTN_STYLE}background:#30363d">⬇ Descargar Ollama</button>
    <button onclick="send('resetStorage')" style="${BTN_STYLE}background:#30363d">↺ Restablecer</button>
  </div>
</div>

<div class="card">
  <div class="card-title">Datos de SmartOrch — historial, índice y RAG</div>
  ${
    dataInfo
      ? `<div class="grid2">
    <div class="kv"><label>Carpeta ${dataInfo.custom ? "(personalizada)" : "(por defecto)"}</label><span style="word-break:break-all">${_esc(dataInfo.data_dir)}</span></div>
    <div class="kv"><label>Espacio libre en ese disco</label><span>${dataInfo.free_gb ?? "?"} GB <span style="color:#8b949e">· ocupa ${dataInfo.total_mb} MB</span></span></div>
    ${dataInfo.items.map((i) => `<div class="kv"><label>${_esc(i.label)}</label><span style="color:${i.exists ? "#3fb950" : "#8b949e"}">${i.exists ? i.mb + " MB" : "aún no existe"}</span></div>`).join("")}
  </div>`
      : `<p style="color:#8b949e;font-size:.85rem">El servidor no responde, así que no puedo leer el estado. Inicia SmartOrch y vuelve a abrir este panel.</p>`
  }
  <div class="actions">
    <button onclick="send('pickData')" style="${BTN_STYLE}background:#1f6feb">📁 Cambiar carpeta de datos</button>
    <button onclick="send('resetData')" style="${BTN_STYLE}background:#30363d">↺ Volver a la carpeta por defecto</button>
  </div>
  <p style="color:#6e7681;font-size:.75rem;margin-top:.6rem">Al cambiarla, SmartOrch copia lo existente, reinicia el servidor y no borra lo anterior.</p>
</div>

<div class="card">
  <div class="card-title">Hardware</div>
  <div class="grid2">
    <div class="kv"><label>CPU</label><span>${_esc(sys.cpuModel)} · ${sys.cpuCores} núcleos</span></div>
    <div class="kv"><label>RAM</label><span>${sys.ramGb} GB</span></div>
    <div class="kv"><label>GPU</label><span>${_esc(gpuStr)}</span></div>
    <div class="kv"><label>Disco libre</label><span>${sys.diskFreeGb} GB</span></div>
    <div class="kv"><label>Python</label><span>${sys.python ?? "No encontrado"}</span></div>
    <div class="kv"><label>Ollama</label><span>${ollamaStatus}</span></div>
  </div>
</div>

${
  sys.workspace.fileCount > 0
    ? `
<div class="card">
  <div class="card-title">Workspace detectado</div>
  <div class="grid2">
    <div class="kv"><label>Lenguajes</label><span>${sys.workspace.languages.join(", ") || "Ninguno"}</span></div>
    <div class="kv"><label>Frameworks</label><span>${sys.workspace.frameworks.join(", ") || "Ninguno"}</span></div>
    <div class="kv"><label>Archivos</label><span>${sys.workspace.fileCount}</span></div>
  </div>
</div>`
    : ""
}

<div class="card">
  <div class="card-title">
    Stack recomendado &nbsp;
    <span class="tier-badge" style="color:${tierColor};border-color:${tierColor}">${stack.tier.toUpperCase()}</span>
  </div>
  <p style="color:#8b949e;font-size:.85rem;margin-bottom:1rem">${_esc(stack.description)} · Disco total: ${stack.totalDiskGb} GB</p>
  <table>
    <thead>
      <tr style="border-bottom:1px solid #30363d">
        <th style="padding:.4rem .75rem;text-align:left;color:#8b949e;font-size:.75rem;font-weight:500">Modelo</th>
        <th style="padding:.4rem .75rem;text-align:left;color:#8b949e;font-size:.75rem;font-weight:500">Rol</th>
        <th style="padding:.4rem .75rem;text-align:left;color:#8b949e;font-size:.75rem;font-weight:500">Tamaño</th>
        <th style="padding:.4rem .75rem;text-align:left;color:#8b949e;font-size:.75rem;font-weight:500">Acción</th>
      </tr>
    </thead>
    <tbody>${modelRows}</tbody>
  </table>
  <div class="actions">
    <button onclick="pullAll()" style="${BTN_STYLE}background:#238636">⬇ Instalar todos los modelos</button>
    ${!ollama?.running ? `<button onclick="serve()" style="${BTN_STYLE}background:#1f6feb">▶ Iniciar SmartOrch</button>` : ""}
  </div>
</div>

<div style="color:#8b949e;font-size:.8rem;margin-top:1rem">
  Una vez instalados los modelos, inicia el servidor con <code style="background:#161b22;padding:.1rem .3rem;border-radius:3px">smartorch serve</code> en cualquier terminal.
</div>

<script>
  const vscode = acquireVsCodeApi();
  function pull(cmd, name) {
    vscode.postMessage({ command: 'pull', pullCmd: cmd, model: name });
  }
  function send(command) {
    vscode.postMessage({ command: command });
  }
  function serve() {
    vscode.postMessage({ command: 'serve' });
  }
  function useRuntime(id) {
    const el = document.getElementById('remoteUrl');
    vscode.postMessage({ command: 'runtime', id: id, url: id === 'remote' && el ? el.value : undefined });
  }
  function pullAll() {
    vscode.postMessage({ command: 'pullAll', cmds: ${JSON.stringify(allCmds)} });
  }
</script>
</body>
</html>`;
}

const BTN_STYLE =
  "color:#e6edf3;border:none;border-radius:6px;padding:.4rem .9rem;cursor:pointer;font-size:.82rem;";

function _esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
