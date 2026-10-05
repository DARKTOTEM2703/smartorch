/**
 * SmartOrch Sidebar — WebviewViewProvider
 * Chat con streaming, contexto automático del archivo, botón Ejecutar,
 * diagnósticos de VS Code, y detección automática de bugs/sugerencias.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { SmartOrchClient, ChatMessage } from './smartorch-client';

export class SmartOrchSidebarProvider implements vscode.WebviewViewProvider {
  static readonly viewId = 'smartorch.chatView';

  private _view?: vscode.WebviewView;
  private _history: ChatMessage[] = [];
  private _streaming = false;

  constructor(
    private readonly _ctx: vscode.ExtensionContext,
    private _client: SmartOrchClient
  ) {}

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ) {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._ctx.extensionUri],
    };
    webviewView.webview.html = this._getHtml();

    webviewView.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'chat':        await this._handleChat(msg); break;
        case 'clear':       this._clearHistory(); break;
        case 'runCommand':  this._runInTerminal(msg.command); break;
        case 'getContext':  this._sendContext(); break;
        case 'getDiags':    this._sendDiagnostics(); break;
        case 'stop':        this._streaming = false; break;
      }
    });

    // Notificar al webview cuando cambia el archivo activo
    vscode.window.onDidChangeActiveTextEditor(() => {
      this._sendContext();
    }, null, this._ctx.subscriptions);
  }

  updateClient(client: SmartOrchClient) { this._client = client; }

  /** Abre sidebar y envía con contexto de código. */
  async sendWithCode(prompt: string, code?: string) {
    await vscode.commands.executeCommand('workbench.view.extension.smartorch');
    const text = code ? `${prompt}\n\n\`\`\`\n${code}\n\`\`\`` : prompt;
    await this._chat(text);
  }

  /** Ejecuta un slash command: abre el sidebar con código adjunto. */
  async sendSlash(prompt: string, code: string, cmdName: string) {
    await vscode.commands.executeCommand('workbench.view.extension.smartorch');
    await this._chat(`/${cmdName}\n\n${prompt}\n\n\`\`\`\n${code}\n\`\`\``);
  }

  // ── Privados ────────────────────────────────────────────────────────────────

  private async _handleChat(msg: {
    text: string;
    includeFile: boolean;
    includeDiags: boolean;
  }) {
    if (this._streaming) return;

    let finalText = msg.text;

    // Agregar contexto del archivo activo
    if (msg.includeFile) {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        const doc  = editor.document;
        const sel  = editor.selection;
        const code = sel.isEmpty
          ? doc.getText().slice(0, 8000)
          : doc.getText(sel);
        const fname = path.basename(doc.fileName);
        finalText = `Archivo: \`${fname}\`\n\`\`\`${doc.languageId}\n${code}\n\`\`\`\n\n${finalText}`;
      }
    }

    // Agregar diagnósticos si hay errores
    if (msg.includeDiags) {
      const diags = this._getActiveDiagnostics();
      if (diags.length) {
        const diagText = diags.slice(0, 10).map(d =>
          `Línea ${d.range.start.line + 1}: [${d.severity}] ${d.message}`
        ).join('\n');
        finalText = `**Errores del editor:**\n${diagText}\n\n${finalText}`;
      }
    }

    await this._chat(finalText);
  }

  private async _chat(text: string) {
    this._history.push({ role: 'user', content: text });
    this._view?.webview.postMessage({ type: 'userMsg', text });
    this._view?.webview.postMessage({ type: 'thinkingStart' });
    this._streaming = true;

    let fullResponse = '';

    this._client.chatStream(
      this._history,
      (chunk: string) => {
        if (!this._streaming) return;
        fullResponse += chunk;
        this._view?.webview.postMessage({ type: 'chunk', text: chunk });
      },
      (stats: any) => {
        this._streaming = false;
        this._history.push({ role: 'assistant', content: fullResponse });
        this._view?.webview.postMessage({
          type:   'done',
          ptok:   stats.promptTokens,
          ctok:   stats.completionTokens,
          tps:    stats.tokensPerSec,
        });
      },
      (step: string) => {
        this._view?.webview.postMessage({ type: 'status', step });
      },
    );
  }

  private _clearHistory() {
    this._history = [];
    this._streaming = false;
    this._view?.webview.postMessage({ type: 'cleared' });
  }

  private _runInTerminal(command: string) {
    const term = vscode.window.terminals.find(t => t.name === 'SmartOrch')
      ?? vscode.window.createTerminal('SmartOrch');
    term.show(true);
    term.sendText(command);
    this._view?.webview.postMessage({ type: 'commandSent', command });
  }

  private _sendContext() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      this._view?.webview.postMessage({ type: 'context', file: null });
      return;
    }
    const doc   = editor.document;
    const diags = vscode.languages.getDiagnostics(doc.uri);
    const errors = diags.filter(d => d.severity === vscode.DiagnosticSeverity.Error).length;
    const warns  = diags.filter(d => d.severity === vscode.DiagnosticSeverity.Warning).length;
    this._view?.webview.postMessage({
      type:     'context',
      file:     path.basename(doc.fileName),
      lang:     doc.languageId,
      lines:    doc.lineCount,
      errors,
      warns,
    });
  }

  private _sendDiagnostics() {
    const diags = this._getActiveDiagnostics();
    this._view?.webview.postMessage({ type: 'diags', count: diags.length });
  }

  private _getActiveDiagnostics(): vscode.Diagnostic[] {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return [];
    return vscode.languages.getDiagnostics(editor.document.uri);
  }

  // ── HTML del webview ────────────────────────────────────────────────────────

  private _getHtml(): string {
    return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartOrch</title>
<style>
:root{
  --bg:     var(--vscode-sideBar-background,#1e1e2e);
  --fg:     var(--vscode-foreground,#cdd6f4);
  --input:  var(--vscode-input-background,#2a2a3e);
  --brd:    var(--vscode-panel-border,#383860);
  --acc:    var(--vscode-button-background,#89b4fa);
  --acc-fg: var(--vscode-button-foreground,#1e1e2e);
  --user:   var(--vscode-inputValidation-infoBorder,#1e3a5f);
  --ai:     #181825;
  --code:   var(--vscode-textCodeBlock-background,#12121f);
  --dim:    var(--vscode-descriptionForeground,#6c7086);
  --err:    var(--vscode-errorForeground,#f38ba8);
  --warn:   var(--vscode-editorWarning-foreground,#f9e2af);
  --ok:     #a6e3a1;
  --radius: 6px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
  font-family:var(--vscode-font-family,-apple-system,'Segoe UI',sans-serif);
  font-size:var(--vscode-font-size,13px);
  display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* ── Header de contexto ── */
#ctx-bar{
  display:flex;align-items:center;gap:6px;
  padding:4px 8px;border-bottom:1px solid var(--brd);
  font-size:11px;color:var(--dim);min-height:28px;flex-shrink:0;
}
#ctx-file{font-weight:600;color:var(--fg);max-width:140px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{padding:1px 5px;border-radius:3px;font-size:10px;font-weight:600}
.badge-err{background:#3d1a1a;color:var(--err)}
.badge-warn{background:#3d2f0a;color:var(--warn)}
.badge-ok{background:#1a3d2a;color:var(--ok)}
#ctx-diags-btn{
  margin-left:auto;background:none;border:none;color:var(--dim);
  cursor:pointer;font-size:10px;padding:2px 4px;border-radius:3px;
}
#ctx-diags-btn:hover{background:var(--brd);color:var(--fg)}

/* ── Mensajes ── */
#msgs{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px}
.msg{border-radius:var(--radius);line-height:1.65;word-break:break-word}
.user{
  background:var(--user);padding:7px 10px;
  border-radius:var(--radius) var(--radius) 2px var(--radius);
  align-self:flex-end;max-width:95%;font-size:.88em;white-space:pre-wrap
}
.assistant{background:var(--ai);border:1px solid var(--brd);padding:8px 10px}
.meta{font-size:10px;color:var(--dim);margin-top:5px;display:flex;gap:8px;flex-wrap:wrap}

/* ── Código ── */
pre{background:var(--code);border:1px solid var(--brd);border-radius:4px;
    padding:10px;overflow-x:auto;margin:6px 0;font-size:11.5px;position:relative}
code{font-family:var(--vscode-editor-font-family,'Cascadia Code',Consolas,monospace)}
.code-header{display:flex;justify-content:space-between;align-items:center;
             margin-bottom:4px;font-size:10px;color:var(--dim)}
.code-lang{text-transform:uppercase;font-weight:600}
.code-btns{display:flex;gap:4px}
.btn-tiny{
  background:var(--brd);border:none;color:var(--fg);border-radius:3px;
  padding:1px 7px;font-size:10px;cursor:pointer
}
.btn-tiny:hover{background:var(--acc);color:var(--acc-fg)}
.btn-run{background:#1a3d1a;color:var(--ok)}
.btn-run:hover{background:var(--ok);color:#1a3d1a}

/* ── Markdown básico ── */
p{margin:3px 0}strong{color:var(--acc);font-weight:600}
em{opacity:.85}h1,h2,h3{color:var(--acc);margin:8px 0 4px;font-size:1em}
ul,ol{padding-left:18px;margin:4px 0}li{margin:2px 0}
hr{border:none;border-top:1px solid var(--brd);margin:8px 0}
a{color:var(--acc)}blockquote{border-left:3px solid var(--brd);padding-left:8px;color:var(--dim)}

/* ── Status / thinking ── */
#status-bar{
  font-size:10px;color:var(--dim);padding:2px 8px;height:18px;
  flex-shrink:0;display:flex;align-items:center;gap:4px
}
.dot{width:5px;height:5px;border-radius:50%;background:var(--dim);
     animation:blink 1s infinite}
.dot:nth-child(2){animation-delay:.15s}.dot:nth-child(3){animation-delay:.3s}
@keyframes blink{0%,80%,100%{transform:scale(1)}40%{transform:scale(1.5)}}

/* ── Footer ── */
#footer{padding:6px 8px;border-top:1px solid var(--brd);background:var(--bg);flex-shrink:0}
.opts{display:flex;gap:8px;margin-bottom:5px;flex-wrap:wrap;align-items:center}
label{display:flex;gap:4px;align-items:center;color:var(--dim);font-size:11px;cursor:pointer}
label input{accent-color:var(--acc)}
.row{display:flex;gap:4px;align-items:flex-end}
#inp{
  flex:1;background:var(--input);border:1px solid var(--brd);border-radius:4px;
  padding:6px 8px;color:var(--fg);font-size:12px;font-family:inherit;
  resize:none;min-height:34px;max-height:120px;outline:none;line-height:1.5
}
#inp:focus{border-color:var(--acc)}
#inp::placeholder{color:var(--dim)}
#send-btn{
  background:var(--acc);color:var(--acc-fg);border:none;border-radius:4px;
  padding:0 12px;cursor:pointer;font-weight:700;font-size:14px;min-height:34px;
  transition:opacity .15s
}
#send-btn:disabled{opacity:.4;cursor:not-allowed}
#stop-btn{
  display:none;background:#3d1a1a;color:var(--err);border:none;border-radius:4px;
  padding:0 10px;cursor:pointer;font-size:11px;min-height:34px
}
#clr-btn{
  background:none;border:1px solid var(--brd);color:var(--dim);border-radius:4px;
  padding:0 7px;cursor:pointer;font-size:10px;min-height:24px;align-self:center
}
#clr-btn:hover{color:var(--fg)}
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-thumb{background:var(--brd);border-radius:2px}
</style>
</head>
<body>

<!-- Barra de contexto del archivo activo -->
<div id="ctx-bar">
  <span id="ctx-icon">📄</span>
  <span id="ctx-file">Sin archivo</span>
  <span id="ctx-badges"></span>
  <button id="ctx-diags-btn" title="Incluir errores en el chat" onclick="includeDiags()">⚠ diags</button>
</div>

<!-- Lista de mensajes -->
<div id="msgs">
  <div class="msg assistant">
    <strong>⚡ SmartOrch</strong>
    <p style="color:var(--dim);font-size:11px;margin-top:4px">
      AI local · RAG automático · SOLID/DRY · YARA/Sigma<br>
      <kbd>Incluir archivo</kbd> para agregar el código activo al contexto.<br>
      Selecciona código + click derecho → <strong>⚡ SmartOrch</strong> para slash commands.
    </p>
  </div>
</div>

<!-- Status bar de pipeline -->
<div id="status-bar"></div>

<!-- Footer de entrada -->
<div id="footer">
  <div class="opts">
    <label><input type="checkbox" id="inc-file" checked> Incluir archivo</label>
    <label><input type="checkbox" id="inc-diags"> Incluir errores</label>
    <button class="btn-tiny" id="clr-btn" onclick="clr()">Limpiar</button>
  </div>
  <div class="row">
    <textarea id="inp" placeholder="Pregunta algo, pide un /refactor, o describe un bug..." rows="1"></textarea>
    <button id="send-btn" onclick="send()">▶</button>
    <button id="stop-btn" onclick="stop()">■ Stop</button>
  </div>
</div>

<script>
const vscode   = acquireVsCodeApi();
const msgs     = document.getElementById('msgs');
const inp      = document.getElementById('inp');
const sendBtn  = document.getElementById('send-btn');
const stopBtn  = document.getElementById('stop-btn');
const statusEl = document.getElementById('status-bar');
let   streaming = false;
let   curAiDiv  = null;
let   curText   = '';

// Solicitar contexto al cargar
vscode.postMessage({ type: 'getContext' });

// ── Markdown renderer ───────────────────────────────────────────────────────

function esc(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(raw) {
  let s = raw;

  // Bloques de código con encabezado y botones
  s = s.replace(/\`\`\`(\w*)\n?([\s\S]*?)\`\`\`/g, (_, lang, code) => {
    const l     = lang || 'code';
    const isCmd = /^(bash|sh|cmd|powershell|ps1|shell|zsh)$/i.test(l)
                  || /^[$>]/.test(code.trim());
    const safeLang = esc(l);
    const safeCode = esc(code.trim());
    const runBtn = isCmd
      ? \`<button class="btn-tiny btn-run" onclick="runCmd(this)">▶ Ejecutar</button>\`
      : '';
    return \`<div class="code-header">
      <span class="code-lang">\${safeLang}</span>
      <span class="code-btns">
        <button class="btn-tiny" onclick="copyCode(this)">⎘ Copiar</button>
        \${runBtn}
      </span>
    </div><pre><code data-cmd="\${isCmd ? safeCode : ''}">\${safeCode}</code></pre>\`;
  });

  // Código inline
  s = s.replace(/\`([^\`\n]+)\`/g, '<code>$1</code>');

  // Encabezados
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

  // Negritas e itálicas
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*(.+?)\*/g,     '<em>$1</em>');

  // Listas
  s = s.replace(/^\s*[-*] (.+)$/gm, '<li>$1</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  s = s.replace(/^\s*\d+\. (.+)$/gm, '<li>$1</li>');

  // Línea horizontal
  s = s.replace(/^---+$/gm, '<hr>');

  // Párrafos
  s = s.split(/\n\n+/)
    .map(p => p.startsWith('<') ? p : \`<p>\${p.replace(/\n/g,'<br>')}</p>\`)
    .join('\n');

  return s;
}

// ── Acciones de código ──────────────────────────────────────────────────────

function copyCode(btn) {
  const pre  = btn.closest('.code-header').nextElementSibling;
  const text = pre?.querySelector('code')?.innerText ?? '';
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓ Copiado';
    setTimeout(() => btn.textContent = '⎘ Copiar', 1800);
  }).catch(() => {});
}

function runCmd(btn) {
  const code = btn.closest('.code-header').nextElementSibling?.querySelector('code');
  const cmd  = code?.innerText?.trim();
  if (!cmd) return;
  vscode.postMessage({ type: 'runCommand', command: cmd });
  btn.textContent = '✓ Ejecutado';
  setTimeout(() => btn.textContent = '▶ Ejecutar', 2000);
}

function includeDiags() {
  document.getElementById('inc-diags').checked = true;
  vscode.postMessage({ type: 'getDiags' });
}

// ── Chat ────────────────────────────────────────────────────────────────────

function send() {
  const text = inp.value.trim();
  if (!text || streaming) return;
  inp.value = '';
  inp.style.height = 'auto';
  vscode.postMessage({
    type:        'chat',
    text,
    includeFile: document.getElementById('inc-file').checked,
    includeDiags: document.getElementById('inc-diags').checked,
  });
}

function stop() {
  streaming = false;
  vscode.postMessage({ type: 'stop' });
  setStreaming(false);
  if (curAiDiv) {
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = 'Detenido por el usuario';
    curAiDiv.appendChild(meta);
    curAiDiv = null;
  }
}

function clr() {
  vscode.postMessage({ type: 'clear' });
}

function setStreaming(on) {
  streaming = on;
  sendBtn.disabled = on;
  stopBtn.style.display = on ? 'block' : 'none';
}

// ── Mensajes del extension host ──────────────────────────────────────────────

window.addEventListener('message', e => {
  const m = e.data;

  if (m.type === 'userMsg') {
    const d = document.createElement('div');
    d.className = 'msg user';
    d.textContent = m.text;
    msgs.appendChild(d);
    scroll();
    return;
  }

  if (m.type === 'thinkingStart') {
    setStreaming(true);
    statusEl.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>&nbsp;SmartOrch procesando...';
    curAiDiv  = document.createElement('div');
    curAiDiv.className = 'msg assistant';
    curText   = '';
    msgs.appendChild(curAiDiv);
    scroll();
    return;
  }

  if (m.type === 'status') {
    const labels = {
      cache:    '🔵 Cache hit',
      route:    '🔍 Clasificando tarea',
      rag:      '📚 RAG — buscando contexto',
      cot:      '🧠 Preparando respuesta',
      thinking: '💭 Razonando...',
      llm:      '⚡ Generando',
      verify:   '✅ Verificando',
    };
    statusEl.textContent = labels[m.step] ?? m.step;
    return;
  }

  if (m.type === 'chunk') {
    statusEl.innerHTML = '';
    if (curAiDiv) {
      curText += m.text;
      curAiDiv.innerHTML = renderMarkdown(curText);
      scroll();
    }
    return;
  }

  if (m.type === 'done') {
    setStreaming(false);
    statusEl.innerHTML = '';
    if (curAiDiv) {
      curAiDiv.innerHTML = renderMarkdown(curText);
      const meta = document.createElement('div');
      meta.className = 'meta';
      const parts = [];
      if (m.ptok) parts.push(\`\${m.ptok}↑\`);
      if (m.ctok) parts.push(\`\${m.ctok}↓\`);
      if (m.tps)  parts.push(\`\${m.tps.toFixed(1)} tok/s\`);
      meta.textContent = parts.join(' · ');
      curAiDiv.appendChild(meta);
      curAiDiv = null;
      curText  = '';
    }
    scroll();
    return;
  }

  if (m.type === 'cleared') {
    msgs.innerHTML = '<div class="msg assistant"><p>Historial limpiado. Listo para empezar.</p></div>';
    statusEl.innerHTML = '';
    setStreaming(false);
    return;
  }

  if (m.type === 'context') {
    const fileEl   = document.getElementById('ctx-file');
    const badgeEl  = document.getElementById('ctx-badges');
    const iconEl   = document.getElementById('ctx-icon');
    if (!m.file) {
      fileEl.textContent  = 'Sin archivo';
      badgeEl.innerHTML   = '';
      iconEl.textContent  = '📄';
      return;
    }
    const ext = m.file.split('.').pop() ?? '';
    const icons = { py:'🐍', ts:'🔷', js:'🟡', rs:'🦀', go:'🐹',
                    cpp:'⚙', c:'⚙', java:'☕', dart:'🎯', kt:'🟣' };
    iconEl.textContent  = icons[ext] ?? '📄';
    fileEl.textContent  = m.file;
    fileEl.title        = m.file + ' (' + (m.lines ?? 0) + ' líneas)';
    let badges = '';
    if (m.errors) badges += \`<span class="badge badge-err" title="Errores">\${m.errors}✗</span>\`;
    if (m.warns)  badges += \`<span class="badge badge-warn" title="Warnings">\${m.warns}⚠</span>\`;
    if (!m.errors && !m.warns && m.file) badges = '<span class="badge badge-ok">✓ OK</span>';
    badgeEl.innerHTML = badges;
    return;
  }

  if (m.type === 'commandSent') {
    const n = document.createElement('div');
    n.className = 'msg assistant';
    n.innerHTML = \`<p style="color:var(--ok);font-size:11px">▶ Ejecutado en terminal: <code>\${esc(m.command)}</code></p>\`;
    msgs.appendChild(n);
    scroll();
    return;
  }
});

// ── Input auto-resize y keybindings ─────────────────────────────────────────

inp.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
inp.addEventListener('input', () => {
  inp.style.height = 'auto';
  inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
});

function scroll() {
  msgs.scrollTop = msgs.scrollHeight;
}
</script>
</body>
</html>`;
  }
}
