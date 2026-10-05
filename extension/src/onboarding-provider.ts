/**
 * SmartOrch Onboarding Panel
 * Muestra análisis del PC y permite hacer ollama pull de los modelos recomendados.
 */
import * as vscode from 'vscode';
import { detect, recommendStack, SystemInfo, ModelStack } from './system-detector';

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
      'smartorchOnboarding',
      'SmartOrch — Configuración inicial',
      vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    OnboardingPanel.currentPanel = new OnboardingPanel(panel, context);
  }

  private constructor(panel: vscode.WebviewPanel, context: vscode.ExtensionContext) {
    this._panel = panel;

    this._panel.webview.html = _loadingHtml();
    this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

    this._panel.webview.onDidReceiveMessage(async msg => {
      if (msg.command === 'pull') {
        const term = vscode.window.createTerminal('SmartOrch — Ollama Pull');
        term.sendText(msg.pullCmd);
        term.show();
      }
      if (msg.command === 'serve') {
        const term = vscode.window.createTerminal('SmartOrch Server');
        term.sendText('smartorch serve');
        term.show();
      }
      if (msg.command === 'pullAll') {
        const term = vscode.window.createTerminal('SmartOrch — Pull modelos');
        for (const cmd of msg.cmds as string[]) {
          term.sendText(cmd);
        }
        term.show();
      }
    }, null, this._disposables);

    this._loadData(context);
  }

  private async _loadData(context: vscode.ExtensionContext) {
    const wsPaths = (vscode.workspace.workspaceFolders ?? []).map(f => f.uri.fsPath);
    let sys: SystemInfo;
    try {
      sys = await detect(wsPaths);
    } catch (e: any) {
      this._panel.webview.html = _errorHtml(e.message);
      return;
    }
    const stack = recommendStack(sys);
    this._panel.webview.html = _buildHtml(sys, stack);
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

function _buildHtml(sys: SystemInfo, stack: ModelStack): string {
  const ollama  = sys.ollama;
  const gpu     = sys.gpu;
  const pulled  = new Set(ollama?.pulledModels ?? []);
  const allCmds = stack.models.map(m => m.pull);

  const modelRows = stack.models.map(m => {
    const isPulled = [...pulled].some(p => p.startsWith(m.name.split(':')[0]));
    const badge    = isPulled
      ? `<span style="color:#3fb950;font-size:.75rem">✔ instalado</span>`
      : `<button onclick="pull('${_esc(m.pull)}','${_esc(m.name)}')" style="${BTN_STYLE}background:#1f6feb">Pull ${_esc(m.name)}</button>`;
    return `
      <tr>
        <td style="padding:.5rem .75rem;color:#e6edf3">${_esc(m.name)}</td>
        <td style="padding:.5rem .75rem;color:#8b949e">${_esc(m.role)}</td>
        <td style="padding:.5rem .75rem;color:#8b949e">${m.sizeGb} GB</td>
        <td style="padding:.5rem .75rem">${badge}</td>
      </tr>`;
  }).join('');

  const ollamaStatus = ollama?.running
    ? `<span style="color:#3fb950">● corriendo</span> v${ollama.version}`
    : ollama
      ? `<span style="color:#f0883e">● instalado pero no corre</span> v${ollama.version}`
      : `<span style="color:#f85149">● no encontrado</span>`;

  const gpuStr = gpu
    ? `${gpu.name} · ${(gpu.vramMb / 1024).toFixed(1)} GB VRAM`
    : 'No detectada (se usará RAM)';

  const tierColors: Record<string, string> = {
    minimal: '#f0883e', standard: '#d29922', full: '#3fb950', pro: '#00d4ff'
  };
  const tierColor = tierColors[stack.tier] ?? '#e6edf3';

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
</style>
</head>
<body>
<h1>⚡ SmartOrch — Configuración inicial</h1>
<p class="sub">Análisis de hardware completado · ${sys.os}</p>

<div class="card">
  <div class="card-title">Hardware</div>
  <div class="grid2">
    <div class="kv"><label>CPU</label><span>${_esc(sys.cpuModel)} · ${sys.cpuCores} núcleos</span></div>
    <div class="kv"><label>RAM</label><span>${sys.ramGb} GB</span></div>
    <div class="kv"><label>GPU</label><span>${_esc(gpuStr)}</span></div>
    <div class="kv"><label>Disco libre</label><span>${sys.diskFreeGb} GB</span></div>
    <div class="kv"><label>Python</label><span>${sys.python ?? 'No encontrado'}</span></div>
    <div class="kv"><label>Ollama</label><span>${ollamaStatus}</span></div>
  </div>
</div>

${sys.workspace.fileCount > 0 ? `
<div class="card">
  <div class="card-title">Workspace detectado</div>
  <div class="grid2">
    <div class="kv"><label>Lenguajes</label><span>${sys.workspace.languages.join(', ') || 'Ninguno'}</span></div>
    <div class="kv"><label>Frameworks</label><span>${sys.workspace.frameworks.join(', ') || 'Ninguno'}</span></div>
    <div class="kv"><label>Archivos</label><span>${sys.workspace.fileCount}</span></div>
  </div>
</div>` : ''}

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
    ${!ollama?.running ? `<button onclick="serve()" style="${BTN_STYLE}background:#1f6feb">▶ Iniciar SmartOrch</button>` : ''}
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
  function serve() {
    vscode.postMessage({ command: 'serve' });
  }
  function pullAll() {
    vscode.postMessage({ command: 'pullAll', cmds: ${JSON.stringify(allCmds)} });
  }
</script>
</body>
</html>`;
}

const BTN_STYLE = 'color:#e6edf3;border:none;border-radius:6px;padding:.4rem .9rem;cursor:pointer;font-size:.82rem;';

function _esc(s: string): string {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
