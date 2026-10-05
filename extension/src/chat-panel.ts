/**
 * Chat Panel — Webview con historial de conversación
 */
import * as vscode from 'vscode';
import { SmartOrchClient, ChatMessage } from './smartorch-client';

export class ChatPanel {
  static readonly viewType = 'smartorch.chatView';
  private _panel: vscode.WebviewPanel | undefined;
  private _history: ChatMessage[] = [];

  constructor(
    private readonly _context: vscode.ExtensionContext,
    private readonly _client: SmartOrchClient
  ) {}

  open() {
    if (this._panel) {
      this._panel.reveal(vscode.ViewColumn.Two);
      return;
    }

    this._panel = vscode.window.createWebviewPanel(
      ChatPanel.viewType,
      'SmartOrch Chat',
      vscode.ViewColumn.Two,
      { enableScripts: true, retainContextWhenHidden: true }
    );

    this._panel.webview.html = this._getHtml();

    this._panel.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === 'chat') {
        await this._handleChat(msg.text, msg.includeFile);
      } else if (msg.type === 'clear') {
        this._history = [];
        this._panel?.webview.postMessage({ type: 'cleared' });
      }
    });

    this._panel.onDidDispose(() => { this._panel = undefined; });
  }

  async sendWithContext(prompt: string, code?: string) {
    this.open();
    const fullPrompt = code
      ? `${prompt}\n\n\`\`\`\n${code}\n\`\`\``
      : prompt;
    await this._handleChat(fullPrompt, false);
  }

  private async _handleChat(text: string, includeActiveFile: boolean) {
    // Agregar contexto del archivo activo si se solicita
    let finalText = text;
    if (includeActiveFile) {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        const doc  = editor.document;
        const lang = doc.languageId;
        const code = doc.getText().slice(0, 4000);
        finalText  = `Archivo: ${doc.fileName}\n\n\`\`\`${lang}\n${code}\n\`\`\`\n\n${text}`;
      }
    }

    this._history.push({ role: 'user', content: finalText });
    this._panel?.webview.postMessage({ type: 'thinking' });

    try {
      const resp = await this._client.chat(this._history);
      this._history.push({ role: 'assistant', content: resp.content });
      this._panel?.webview.postMessage({
        type: 'response',
        content: resp.content,
        model: resp.model,
        taskType: resp.taskType,
      });
    } catch (e: any) {
      this._panel?.webview.postMessage({
        type: 'error',
        content: `Error: ${e.message}\n\n¿Está corriendo SmartOrch? → python run.py`,
      });
    }
  }

  private _getHtml(): string {
    return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartOrch</title>
<style>
  :root { --bg:#1e1e2e; --surface:#2a2a3e; --border:#383860; --accent:#89b4fa; --text:#cdd6f4; --dim:#6c7086; --user:#1e3a5f; --ai:#1e2a1e; --code:#181825; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; display:flex; flex-direction:column; height:100vh; font-size:13px; }
  #messages { flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:10px; }
  .msg { padding:10px 14px; border-radius:8px; line-height:1.6; max-width:100%; }
  .user { background:var(--user); border:1px solid #2a5080; border-radius:8px 8px 2px 8px; align-self:flex-end; max-width:85%; }
  .assistant { background:var(--ai); border:1px solid var(--border); border-radius:2px 8px 8px 8px; }
  .meta { font-size:10px; color:var(--dim); margin-top:4px; }
  pre { background:var(--code); border:1px solid var(--border); border-radius:6px; padding:10px; overflow-x:auto; margin:6px 0; font-size:12px; }
  code { font-family:'Cascadia Code','Fira Code',monospace; }
  p { margin:4px 0; }
  .thinking { display:flex; gap:4px; padding:10px; }
  .thinking span { width:6px;height:6px;border-radius:50%;background:var(--dim);animation:b 1.2s infinite; }
  .thinking span:nth-child(2){animation-delay:.2s}.thinking span:nth-child(3){animation-delay:.4s}
  @keyframes b{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-5px)}}
  #footer { padding:8px; border-top:1px solid var(--border); background:var(--surface); }
  .input-row { display:flex; gap:6px; }
  #input { flex:1; background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:7px 10px; color:var(--text); font-size:13px; font-family:inherit; resize:none; min-height:36px; max-height:150px; outline:none; }
  #input:focus { border-color:var(--accent); }
  button { background:var(--accent); color:#1e1e2e; border:none; border-radius:6px; padding:7px 14px; cursor:pointer; font-weight:600; font-size:12px; white-space:nowrap; }
  .btn-secondary { background:var(--surface); color:var(--text); border:1px solid var(--border); }
  .opts { display:flex; gap:8px; margin-bottom:6px; align-items:center; }
  label { display:flex; gap:4px; align-items:center; color:var(--dim); font-size:11px; cursor:pointer; }
  ::-webkit-scrollbar{width:4px} ::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
</style>
</head>
<body>
<div id="messages">
  <div class="msg assistant">
    <p><strong>⚡ SmartOrch</strong> — IA local con RAG automático</p>
    <p style="color:var(--dim);margin-top:4px">Escribe tu pregunta. Activa "Incluir archivo" para enviar el código abierto.</p>
  </div>
</div>
<div id="footer">
  <div class="opts">
    <label><input type="checkbox" id="includeFile"> Incluir archivo activo</label>
    <button class="btn-secondary" onclick="clear_()">Limpiar</button>
  </div>
  <div class="input-row">
    <textarea id="input" placeholder="Pregunta algo... (Enter para enviar)" rows="1"></textarea>
    <button onclick="send()">Enviar</button>
  </div>
</div>
<script>
const vscode = acquireVsCodeApi();
const msgs   = document.getElementById('messages');
const input  = document.getElementById('input');

function esc(t){ return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmt(text){
  text = text.replace(/\`\`\`(\\w*)\n?([\\s\\S]*?)\`\`\`/g,(_,l,c)=>\`<pre><code>\${esc(c.trim())}</code></pre>\`);
  text = text.replace(/\`([^\`]+)\`/g,'<code>$1</code>');
  text = text.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  text = text.split(/\\n\\n+/).map(p=>\`<p>\${p.replace(/\\n/g,'<br>')}</p>\`).join('');
  return text;
}
function addMsg(role, html, meta=''){
  const d=document.createElement('div'); d.className=\`msg \${role}\`;
  d.innerHTML=html+(meta?\`<div class="meta">\${meta}</div>\`:'');
  msgs.appendChild(d); msgs.scrollTop=msgs.scrollHeight;
}
function send(){
  const text=input.value.trim(); if(!text) return;
  addMsg('user',\`<p>\${esc(text)}</p>\`);
  input.value=''; input.style.height='auto';
  const thinking=document.createElement('div');
  thinking.className='msg assistant'; thinking.id='thinking';
  thinking.innerHTML='<div class="thinking"><span></span><span></span><span></span></div>';
  msgs.appendChild(thinking); msgs.scrollTop=msgs.scrollHeight;
  vscode.postMessage({type:'chat',text,includeFile:document.getElementById('includeFile').checked});
}
function clear_(){ vscode.postMessage({type:'clear'}); }
window.addEventListener('message',e=>{
  const m=e.data;
  document.getElementById('thinking')?.remove();
  if(m.type==='response'){
    addMsg('assistant',fmt(m.content),\`\${m.model} · \${m.taskType||'chat'}\`);
  } else if(m.type==='error'){
    addMsg('assistant',\`<p style="color:#f38ba8">\${esc(m.content)}</p>\`);
  } else if(m.type==='cleared'){
    msgs.innerHTML='<div class="msg assistant"><p>Historial limpiado.</p></div>';
  }
});
input.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();} });
input.addEventListener('input',()=>{ input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,150)+'px'; });
</script>
</body></html>`;
  }
}
