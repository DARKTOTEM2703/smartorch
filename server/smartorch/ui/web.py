"""SmartOrch Web UI v3 — streaming + pipeline visualization"""

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SmartOrch</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #1c2128;
  --border: #30363d; --border2: #21262d;
  --accent: #58a6ff; --accent2: #1f6feb;
  --text: #e6edf3; --text-dim: #8b949e; --text-muted: #484f58;
  --user-bg: #1f2937; --ai-bg: #161b22;
  --code-bg: #0d1117;
  --green: #3fb950; --red: #f85149; --yellow: #d29922;
  --purple: #bc8cff; --orange: #ffa657;
  --tag-code: #1f6feb; --tag-agent: #6e40c9; --tag-security: #b91c1c; --tag-chat: #166534;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; flex-direction: column; font-size: 14px; }

/* ── Header ── */
header {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 10px 20px; display: flex; align-items: center; gap: 12px;
  flex-shrink: 0; position: sticky; top: 0; z-index: 10;
}
.logo { font-size: 16px; font-weight: 700; color: var(--accent); letter-spacing: -0.3px; }
.logo span { color: var(--text-dim); font-weight: 400; }
.status-pill {
  display: flex; align-items: center; gap: 6px;
  background: var(--border2); border-radius: 20px; padding: 3px 10px;
  font-size: 12px; color: var(--text-dim);
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--red); transition: background .3s; }
.dot.ok { background: var(--green); }
.dot.pulse { animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
.rag-info { font-size: 11px; color: var(--text-muted); margin-left: auto; display: flex; align-items: center; gap: 6px; }
.rag-badge { background: var(--accent2); color: white; border-radius: 10px; padding: 2px 8px; font-size: 11px; }
.token-counter {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--text-muted);
  background: var(--border2); border-radius: 10px; padding: 3px 10px;
  font-variant-numeric: tabular-nums;
}
.token-counter .tok-in  { color: #58a6ff; }
.token-counter .tok-out { color: #3fb950; }
.token-counter .tok-sep { color: var(--text-muted); }

/* ── Pipeline bar ── */
#pipeline-bar {
  background: var(--surface2); border-bottom: 1px solid var(--border2);
  padding: 6px 20px; display: flex; align-items: center; gap: 0;
  flex-shrink: 0; overflow-x: auto; min-height: 36px;
  transition: opacity .3s;
}
#pipeline-bar.idle { opacity: .4; }
.pipe-step {
  display: flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: 4px; font-size: 12px;
  color: var(--text-muted); transition: all .2s; white-space: nowrap;
}
.pipe-step.done { color: var(--green); }
.pipe-step.active { color: var(--accent); font-weight: 600; }
.pipe-step .icon { font-size: 13px; }
.pipe-sep { color: var(--border); font-size: 16px; padding: 0 2px; }
.pipe-detail { font-size: 10px; color: var(--text-muted); margin-top: 1px; }

/* ── Chat ── */
#chat {
  flex: 1; overflow-y: auto; padding: 24px 20px;
  display: flex; flex-direction: column; gap: 20px;
}

.msg-group { display: flex; gap: 12px; max-width: 880px; width: 100%; }
.msg-group.user { align-self: flex-end; flex-direction: row-reverse; }
.msg-group.assistant { align-self: flex-start; }

.avatar {
  width: 30px; height: 30px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; flex-shrink: 0; margin-top: 2px;
}
.user .avatar { background: var(--accent2); }
.assistant .avatar { background: var(--surface); border: 1px solid var(--border); }

.msg-body { flex: 1; min-width: 0; }

.bubble {
  padding: 12px 16px; border-radius: 10px;
  line-height: 1.65; font-size: 14px;
}
.user .bubble {
  background: var(--user-bg); border: 1px solid var(--border2);
  border-radius: 10px 4px 10px 10px; color: var(--text);
}
.assistant .bubble {
  background: var(--ai-bg); border: 1px solid var(--border);
  border-radius: 4px 10px 10px 10px;
}

/* markdown */
.bubble p { margin: 6px 0; }
.bubble p:first-child { margin-top: 0; }
.bubble p:last-child { margin-bottom: 0; }
.bubble h1,.bubble h2,.bubble h3 { margin: 12px 0 6px; color: var(--text); }
.bubble h2 { font-size: 16px; border-bottom: 1px solid var(--border2); padding-bottom: 4px; }
.bubble h3 { font-size: 14px; }
.bubble ul,.bubble ol { padding-left: 20px; margin: 6px 0; }
.bubble li { margin: 3px 0; }
.bubble strong { color: var(--text); }
.bubble em { color: var(--text-dim); }
.bubble a { color: var(--accent); }
.bubble blockquote { border-left: 3px solid var(--border); padding-left: 12px; color: var(--text-dim); margin: 8px 0; }
.bubble hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }

/* code blocks */
.code-wrapper { position: relative; margin: 10px 0; }
.code-header {
  background: #161b22; border: 1px solid var(--border2); border-bottom: none;
  border-radius: 8px 8px 0 0; padding: 6px 12px;
  display: flex; align-items: center; justify-content: space-between;
}
.code-lang { font-size: 11px; color: var(--text-muted); font-family: monospace; }
.copy-btn {
  background: none; border: 1px solid var(--border); border-radius: 4px;
  color: var(--text-dim); font-size: 11px; padding: 2px 8px; cursor: pointer;
  transition: all .15s;
}
.copy-btn:hover { background: var(--border); color: var(--text); }
.copy-btn.copied { color: var(--green); border-color: var(--green); }
.code-wrapper pre {
  margin: 0; border-radius: 0 0 8px 8px;
  border: 1px solid var(--border2); border-top: none;
  overflow-x: auto;
}
.code-wrapper pre code { font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 13px; }
.bubble code:not([class]) {
  background: var(--code-bg); border: 1px solid var(--border2);
  border-radius: 4px; padding: 1px 5px;
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 13px;
  color: var(--orange);
}

/* meta bar */
.msg-meta {
  display: flex; align-items: center; gap: 6px; margin-top: 6px;
  flex-wrap: wrap;
}
.tag {
  border-radius: 4px; padding: 1px 7px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .4px;
}
.tag.code { background: #1d3251; color: #58a6ff; }
.tag.agent { background: #2d1f4e; color: #bc8cff; }
.tag.security { background: #3b1010; color: #f85149; }
.tag.chat { background: #0f2a1a; color: #3fb950; }
.tag.model { background: var(--border2); color: var(--text-dim); font-weight: 400; text-transform: none; letter-spacing: 0; }
.tag.rag { background: #1a2a3a; color: #58a6ff; font-weight: 400; text-transform: none; letter-spacing: 0; }
.elapsed { font-size: 11px; color: var(--text-muted); }
.tok-meta {
  font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 3px;
  font-variant-numeric: tabular-nums;
}
.tok-meta .tin  { color: #58a6ff; }
.tok-meta .tout { color: #3fb950; }
.tok-meta .tps  { color: var(--yellow); }

/* thinking / streaming */
.thinking-dots { display: flex; gap: 4px; padding: 8px 0; }
.thinking-dots span { width: 6px; height: 6px; border-radius: 50%; background: var(--text-muted); animation: bounce 1.2s infinite; }
.thinking-dots span:nth-child(2) { animation-delay: .2s; }
.thinking-dots span:nth-child(3) { animation-delay: .4s; }
@keyframes bounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-6px)} }

.stream-cursor::after { content: '▋'; animation: blink .7s step-end infinite; color: var(--accent); }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

/* Thinking block — colapsable */
.thinking-block {
  margin-bottom: 10px; border: 1px solid var(--border2);
  border-radius: 8px; overflow: hidden;
}
.thinking-block summary {
  padding: 7px 12px; cursor: pointer; user-select: none;
  font-size: 12px; color: var(--text-muted);
  background: var(--surface2); list-style: none;
  display: flex; align-items: center; gap: 6px;
}
.thinking-block summary::-webkit-details-marker { display: none; }
.thinking-block summary:hover { color: var(--text-dim); }
.thinking-block[open] summary { border-bottom: 1px solid var(--border2); }
.thinking-content {
  padding: 10px 14px; font-size: 12px; color: var(--text-dim);
  line-height: 1.6; font-family: 'Cascadia Code','Fira Code',monospace;
  white-space: pre-wrap; background: var(--bg);
  max-height: 300px; overflow-y: auto;
}
/* Confidence badge */
.conf-badge {
  font-size: 10px; padding: 1px 6px; border-radius: 10px;
  font-weight: 600; margin-left: 2px;
}
.conf-high  { background: #0f2a1a; color: #3fb950; }
.conf-med   { background: #2a1f00; color: #d29922; }
.conf-low   { background: #3b1010; color: #f85149; }

/* ── Context bar ── */
#ctx-bar {
  height: 3px; background: var(--border2); flex-shrink: 0; position: relative; overflow: hidden;
}
#ctx-fill {
  height: 100%; background: var(--green); transition: width .5s ease, background .3s;
  position: absolute; left: 0; top: 0;
}

/* ── Sidebar de conversaciones ── */
#sidebar {
  position: fixed; left: 0; top: 0; bottom: 0; width: 240px;
  background: var(--surface); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; z-index: 20;
  transform: translateX(-100%); transition: transform .2s ease;
}
#sidebar.open { transform: translateX(0); }
#sidebar-header {
  padding: 12px 14px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
}
#sidebar-header h3 { font-size: 13px; font-weight: 600; }
.sidebar-btn {
  background: var(--accent2); color: white; border: none; border-radius: 6px;
  padding: 4px 10px; font-size: 12px; cursor: pointer; font-weight: 600;
}
.sidebar-btn:hover { background: var(--accent); }
#conv-list { flex: 1; overflow-y: auto; padding: 8px 0; }
.conv-item {
  padding: 8px 14px; cursor: pointer; font-size: 12px; color: var(--text-dim);
  border-left: 3px solid transparent; transition: all .15s; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.conv-item:hover { background: var(--surface2); color: var(--text); }
.conv-item.active { border-left-color: var(--accent); background: var(--surface2); color: var(--text); }
.conv-date { font-size: 10px; color: var(--text-muted); margin-top: 1px; }
.conv-del { float: right; color: var(--text-muted); font-size: 11px; padding: 0 4px; }
.conv-del:hover { color: var(--red); }
#sidebar-toggle {
  position: fixed; left: 12px; top: 12px; z-index: 21;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 5px 9px; cursor: pointer; font-size: 14px;
  color: var(--text-dim); transition: all .15s;
}
#sidebar-toggle:hover { color: var(--text); background: var(--surface2); }
#sidebar-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 19;
}
#sidebar-overlay.open { display: block; }

/* ── Input ── */
#input-area {
  background: var(--surface); border-top: 1px solid var(--border);
  padding: 14px 20px; flex-shrink: 0;
}
.input-row { display: flex; gap: 8px; max-width: 880px; margin: 0 auto; }
#msg-input {
  flex: 1; background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; color: var(--text); font-size: 14px;
  resize: none; min-height: 44px; max-height: 180px; outline: none; font-family: inherit;
  transition: border-color .15s;
}
#msg-input:focus { border-color: var(--accent); }
#send-btn {
  background: var(--accent2); color: white; border: none; border-radius: 8px;
  padding: 10px 20px; cursor: pointer; font-weight: 600; font-size: 14px;
  white-space: nowrap; transition: background .15s; display: flex; align-items: center; gap: 6px;
}
#send-btn:hover:not(:disabled) { background: var(--accent); }
#send-btn:disabled { opacity: .5; cursor: not-allowed; }
.hint { text-align: center; font-size: 11px; color: var(--text-muted); margin-top: 8px; }

/* scrollbar */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<button id="sidebar-toggle" onclick="toggleSidebar()" title="Conversaciones">☰</button>
<div id="sidebar-overlay" onclick="toggleSidebar()"></div>

<div id="sidebar">
  <div id="sidebar-header">
    <h3>Conversaciones</h3>
    <button class="sidebar-btn" onclick="newConversation()">+ Nueva</button>
  </div>
  <div id="conv-list"></div>
</div>

<header>
  <div class="logo">⚡ SmartOrch <span>v2</span></div>
  <div class="status-pill">
    <div class="dot" id="dot"></div>
    <span id="server-label">Conectando...</span>
  </div>
  <div class="token-counter" id="session-tokens" title="Tokens de sesión: entrada / salida">
    <span class="tok-in" id="sess-in">0↑</span>
    <span class="tok-sep">/</span>
    <span class="tok-out" id="sess-out">0↓</span>
    <span class="tok-sep">tok</span>
  </div>
  <div class="rag-info">
    <span id="rag-label">RAG —</span>
    <span class="rag-badge" id="rag-badge" style="display:none"></span>
  </div>
</header>

<div id="ctx-bar"><div id="ctx-fill" style="width:0%"></div></div>

<div id="pipeline-bar" class="idle">
  <div class="pipe-step" id="p-router"><span class="icon">🔀</span><div><div>Router</div><div class="pipe-detail" id="pd-router">—</div></div></div>
  <span class="pipe-sep">›</span>
  <div class="pipe-step" id="p-rag"><span class="icon">🔍</span><div><div>RAG</div><div class="pipe-detail" id="pd-rag">—</div></div></div>
  <span class="pipe-sep">›</span>
  <div class="pipe-step" id="p-cot"><span class="icon">🧠</span><div><div>CoT</div><div class="pipe-detail" id="pd-cot">—</div></div></div>
  <span class="pipe-sep">›</span>
  <div class="pipe-step" id="p-thinking"><span class="icon">💭</span><div><div>Think</div><div class="pipe-detail" id="pd-thinking">—</div></div></div>
  <span class="pipe-sep">›</span>
  <div class="pipe-step" id="p-llm"><span class="icon">⚙️</span><div><div>LLM</div><div class="pipe-detail" id="pd-llm">—</div></div></div>
  <span class="pipe-sep">›</span>
  <div class="pipe-step" id="p-verify"><span class="icon">✅</span><div><div>Verify</div><div class="pipe-detail" id="pd-verify">—</div></div></div>
</div>

<div id="chat">
  <div class="msg-group assistant">
    <div class="avatar">⚡</div>
    <div class="msg-body">
      <div class="bubble">
        <p>Hola, soy <strong>SmartOrch</strong> — enruto cada request al modelo correcto, aplico razonamiento encadenado, RAG semántico sobre tu proyecto y verificación automática.</p>
        <p>Comandos de ciberseguridad disponibles: <code>/yara</code> <code>/sigma</code> <code>/c2</code> <code>/pentest</code></p>
      </div>
    </div>
  </div>
</div>

<div id="input-area">
  <div class="input-row">
    <textarea id="msg-input" placeholder="Escribe tu mensaje... (Enter envía, Shift+Enter nueva línea)" rows="1"></textarea>
    <button id="send-btn">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      Enviar
    </button>
  </div>
  <p class="hint">SmartOrch · Modelos locales · Privado · Sin límites</p>
</div>

<script>
const API_KEY  = 'smartorch-local-key';
const BASE     = window.location.origin;
const STORE_KEY = 'smartorch_conversations';
const MAX_SAVED = 20;

let history       = [];
let isLoading     = false;
let sessPromptTok = 0;
let sessComplTok  = 0;
let currentConvId = null;

const chatEl    = document.getElementById('chat');
const inputEl   = document.getElementById('msg-input');
const btnEl     = document.getElementById('send-btn');
const dotEl     = document.getElementById('dot');
const srvLbl    = document.getElementById('server-label');
const ragLbl    = document.getElementById('rag-label');
const ragBadge  = document.getElementById('rag-badge');
const pipeBar   = document.getElementById('pipeline-bar');
const sessInEl  = document.getElementById('sess-in');
const sessOutEl = document.getElementById('sess-out');
const ctxFill   = document.getElementById('ctx-fill');
const convListEl = document.getElementById('conv-list');

// ── Helpers ──────────────────────────────────────────────────────────────────
function fmtNum(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function updateSessionTokens(promptTok, complTok) {
  sessPromptTok += promptTok;
  sessComplTok  += complTok;
  sessInEl.textContent  = fmtNum(sessPromptTok) + '↑';
  sessOutEl.textContent = fmtNum(sessComplTok)  + '↓';
}

function updateCtxBar(pct) {
  const p = Math.min(pct || 0, 100);
  ctxFill.style.width = p + '%';
  ctxFill.style.background = p >= 85 ? '#f85149' : p >= 60 ? '#d29922' : '#3fb950';
}

// ── Sidebar / Conversaciones ──────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('open');
}

function loadConversations() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch { return []; }
}

function saveConversations(convs) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(convs.slice(0, MAX_SAVED))); } catch {}
}

function renderConvList() {
  const convs = loadConversations();
  convListEl.innerHTML = '';
  if (!convs.length) {
    convListEl.innerHTML = '<div style="padding:12px 14px;font-size:12px;color:var(--text-muted)">Sin conversaciones guardadas</div>';
    return;
  }
  convs.forEach(c => {
    const div = document.createElement('div');
    div.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
    const d  = new Date(c.ts);
    const ds = d.toLocaleDateString('es',{day:'2-digit',month:'short'}) + ' ' + d.toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit'});
    div.innerHTML = `${escHtml(c.title||'Conversación')}<span class="conv-del" data-id="${c.id}" title="Eliminar">✕</span><div class="conv-date">${ds}</div>`;
    div.addEventListener('click', e => {
      if (e.target.classList.contains('conv-del')) { deleteConversation(c.id); return; }
      loadConversation(c.id);
    });
    convListEl.appendChild(div);
  });
}

function saveCurrentConversation() {
  if (!history.length) return;
  const convs  = loadConversations();
  const title  = history[0]?.content?.slice(0, 40) || 'Conversación';
  const idx    = currentConvId ? convs.findIndex(c => c.id === currentConvId) : -1;
  const entry  = { id: currentConvId || String(Date.now()), ts: Date.now(), title, messages: history };
  if (!currentConvId) currentConvId = entry.id;
  if (idx >= 0) convs[idx] = entry; else convs.unshift(entry);
  saveConversations(convs);
  renderConvList();
}

function loadConversation(id) {
  const convs = loadConversations();
  const c = convs.find(x => x.id === id);
  if (!c) return;
  currentConvId = id;
  history = c.messages || [];
  chatEl.innerHTML = '';
  history.forEach(m => {
    if (m.role === 'user') {
      const g = document.createElement('div');
      g.className = 'msg-group user';
      g.innerHTML = `<div class="avatar">👤</div><div class="msg-body"><div class="bubble"><p>${escHtml(m.content)}</p></div></div>`;
      chatEl.appendChild(g);
    } else {
      const g = document.createElement('div');
      g.className = 'msg-group assistant';
      g.innerHTML = `<div class="avatar">⚡</div><div class="msg-body"><div class="bubble">${renderMarkdown(m.content)}</div></div>`;
      chatEl.appendChild(g);
    }
  });
  scrollDown();
  renderConvList();
  toggleSidebar();
}

function deleteConversation(id) {
  saveConversations(loadConversations().filter(c => c.id !== id));
  if (currentConvId === id) newConversation(); else renderConvList();
}

function newConversation() {
  currentConvId = null; history = [];
  sessPromptTok = 0; sessComplTok = 0;
  sessInEl.textContent = '0↑'; sessOutEl.textContent = '0↓';
  updateCtxBar(0);
  chatEl.innerHTML = '';
  const g = document.createElement('div');
  g.className = 'msg-group assistant';
  g.innerHTML = `<div class="avatar">⚡</div><div class="msg-body"><div class="bubble"><p>Nueva conversación lista. Comandos: <code>/yara</code> <code>/sigma</code> <code>/c2</code> <code>/pentest</code></p></div></div>`;
  chatEl.appendChild(g);
  renderConvList();
  if (document.getElementById('sidebar').classList.contains('open')) toggleSidebar();
  inputEl.focus();
}

// Inicializar lista al cargar
renderConvList();

const steps = ['router','rag','cot','thinking','llm','verify'];

// ── Pipeline UI ──────────────────────────────────────────────────────────────
function pipeReset() {
  pipeBar.classList.add('idle');
  steps.forEach(s => {
    document.getElementById('p-'+s).className = 'pipe-step';
    document.getElementById('pd-'+s).textContent = '—';
  });
}

function pipeActivate(step, detail='') {
  pipeBar.classList.remove('idle');
  // mark previous steps done
  let found = false;
  steps.forEach(s => {
    const el = document.getElementById('p-'+s);
    const det = document.getElementById('pd-'+s);
    if (s === step) { el.className = 'pipe-step active'; det.textContent = detail || '...'; found = true; }
    else if (!found) { el.className = 'pipe-step done'; }
  });
}

function pipeDone(step, detail='') {
  const el = document.getElementById('p-'+step);
  const det = document.getElementById('pd-'+step);
  el.className = 'pipe-step done';
  det.textContent = detail;
}

// ── Server status ────────────────────────────────────────────────────────────
async function checkStatus() {
  try {
    const r = await fetch(`${BASE}/health`, {signal: AbortSignal.timeout(5000)});
    const d = await r.json();
    dotEl.className = 'dot ok';
    const rag = d.rag_chunks > 0 ? d.rag_chunks : d.index_chunks;
    const mode = d.rag_mode || 'tfidf';
    srvLbl.textContent = 'online';
    ragLbl.textContent = `RAG · ${mode}`;
    if (rag > 0) {
      ragBadge.style.display = '';
      ragBadge.textContent = `${rag} chunks`;
    }
  } catch {
    dotEl.className = 'dot';
    srvLbl.textContent = 'offline';
  }
}
checkStatus();
setInterval(checkStatus, 30000);

// ── Markdown / Code rendering ────────────────────────────────────────────────
function escHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(text) {
  // code blocks with syntax highlighting
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const trimmed = code.trim();
    let highlighted = trimmed;
    try {
      highlighted = lang
        ? hljs.highlight(trimmed, {language: lang, ignoreIllegals: true}).value
        : hljs.highlightAuto(trimmed).value;
    } catch {}
    return `<div class="code-wrapper">
      <div class="code-header">
        <span class="code-lang">${escHtml(lang || 'text')}</span>
        <button class="copy-btn" onclick="copyCode(this)">Copiar</button>
      </div>
      <pre><code class="hljs language-${escHtml(lang)}">${highlighted}</code></pre>
    </div>`;
  });
  // inline code
  text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  // headers
  text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  text = text.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  text = text.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // bold / italic
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // horizontal rule
  text = text.replace(/^---+$/gm, '<hr>');
  // blockquote
  text = text.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
  // unordered list
  text = text.replace(/((?:^[-*•] .+\n?)+)/gm, m => {
    const items = m.trim().split('\n').map(l => `<li>${l.replace(/^[-*•] /,'')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });
  // ordered list
  text = text.replace(/((?:^\d+\. .+\n?)+)/gm, m => {
    const items = m.trim().split('\n').map(l => `<li>${l.replace(/^\d+\. /,'')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });
  // paragraphs (split on double newline)
  const parts = text.split(/\n\n+/);
  text = parts.map(p => {
    p = p.trim();
    if (!p) return '';
    if (p.match(/^<(h[1-3]|ul|ol|blockquote|pre|div|hr)/)) return p;
    return `<p>${p.replace(/\n/g,'<br>')}</p>`;
  }).filter(Boolean).join('');
  return text;
}

function copyCode(btn) {
  const code = btn.closest('.code-wrapper').querySelector('code').innerText;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '✓ Copiado';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copiar'; btn.classList.remove('copied'); }, 2000);
  });
}

// ── Task type tag ────────────────────────────────────────────────────────────
const TASK_ICONS = { code:'💻', agent:'🤖', security:'🔐', chat:'💬' };
function taskTag(type) {
  if (!type) return '';
  const icon = TASK_ICONS[type] || '●';
  return `<span class="tag ${type}">${icon} ${type}</span>`;
}

// ── Add messages ─────────────────────────────────────────────────────────────
function addUserMsg(text) {
  const g = document.createElement('div');
  g.className = 'msg-group user';
  g.innerHTML = `<div class="avatar">👤</div><div class="msg-body"><div class="bubble"><p>${escHtml(text)}</p></div></div>`;
  chatEl.appendChild(g);
  scrollDown();
}

function addAssistantShell() {
  const g = document.createElement('div');
  g.className = 'msg-group assistant';
  g.id = 'active-msg';
  g.innerHTML = `
    <div class="avatar">⚡</div>
    <div class="msg-body">
      <div class="bubble" id="active-bubble">
        <div class="thinking-dots"><span></span><span></span><span></span></div>
      </div>
      <div class="msg-meta" id="active-meta"></div>
    </div>`;
  chatEl.appendChild(g);
  scrollDown();
  return g;
}

function renderThinking(thinkingText) {
  if (!thinkingText) return '';
  return `<details class="thinking-block">
    <summary>🧠 Razonamiento interno <span style="color:var(--text-muted);font-size:10px;margin-left:4px">(clic para ver)</span></summary>
    <div class="thinking-content">${escHtml(thinkingText)}</div>
  </details>`;
}

function confBadge(conf) {
  if (!conf && conf !== 0) return '';
  const cls  = conf >= 0.8 ? 'conf-high' : conf >= 0.6 ? 'conf-med' : 'conf-low';
  const pct  = Math.round(conf * 100);
  const label = conf >= 0.8 ? 'alta confianza' : conf >= 0.6 ? 'confianza media' : 'verificar';
  return `<span class="conf-badge ${cls}" title="Confidence: ${pct}%">${label}</span>`;
}

function finalizeAssistant(fullText, meta) {
  const bubble = document.getElementById('active-bubble');
  const metaEl = document.getElementById('active-meta');
  if (!bubble) return;
  bubble.innerHTML = renderThinking(meta.thinking) + renderMarkdown(fullText);
  const parts = [];
  if (meta.task_type) parts.push(taskTag(meta.task_type));
  if (meta.model)     parts.push(`<span class="tag model">${escHtml(meta.model)}</span>`);
  if (meta.rag_chunks > 0) parts.push(`<span class="tag rag">RAG ${meta.rag_chunks}</span>`);
  if (meta.cache_hit)  parts.push(`<span class="tag" style="background:#0f2a1a;color:#3fb950">⚡ cache</span>`);
  if (meta.thinking)   parts.push(`<span class="tag" style="background:#1a1040;color:#bc8cff">🧠 thinking</span>`);
  if (meta.confidence !== undefined) parts.push(confBadge(meta.confidence));
  if (meta.elapsed)    parts.push(`<span class="elapsed">${meta.elapsed}s</span>`);

  // Token stats — como OpenAI/Anthropic
  if (meta.prompt_tokens || meta.completion_tokens) {
    const pt  = meta.prompt_tokens     || 0;
    const ct  = meta.completion_tokens  || 0;
    const tps = meta.tokens_per_sec     || 0;
    const pct = meta.context_used_pct   || 0;
    let tokHtml = `<span class="tok-meta"><span class="tin">${fmtNum(pt)}↑</span> <span class="tout">${fmtNum(ct)}↓</span> tok`;
    if (tps > 0) tokHtml += ` · <span class="tps">${tps} tok/s</span>`;
    if (pct > 0 && meta.context_window) tokHtml += ` · ctx ${pct}%`;
    tokHtml += '</span>';
    parts.push(tokHtml);
  }

  metaEl.innerHTML = parts.join('');
  const g = document.getElementById('active-msg');
  if (g) g.id = '';
}

function scrollDown() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

// ── Send ─────────────────────────────────────────────────────────────────────
async function send() {
  const text = inputEl.value.trim();
  if (!text || isLoading) return;

  isLoading = true;
  btnEl.disabled = true;
  inputEl.value = '';
  inputEl.style.height = 'auto';

  history.push({role:'user', content: text});
  addUserMsg(text);
  addAssistantShell();
  pipeReset();

  const t0 = Date.now();
  let fullText = '';
  let meta = {};
  let bubble = null;

  try {
    const resp = await fetch(`${BASE}/v1/chat/completions`, {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'Authorization':`Bearer ${API_KEY}`},
      body: JSON.stringify({messages: history, temperature: 0.3, max_tokens: 2048, stream: true})
    });

    const reader = resp.body.getReader();
    const dec    = new TextDecoder();
    let buf = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});

      const lines = buf.split('\n');
      buf = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') continue;

        let obj;
        try { obj = JSON.parse(raw); } catch { continue; }

        // Pipeline status events
        if (obj.type === 'status') {
          const step = obj.step;
          if (step === 'router') {
            meta.task_type = obj.task_type;
            meta.model     = obj.model;
            pipeActivate('router', obj.task_type || '');
            setTimeout(() => pipeDone('router', obj.task_type || ''), 200);
            pipeActivate('rag', '...');
          } else if (step === 'rag') {
            meta.rag_chunks = obj.chunks || 0;
            pipeDone('rag', obj.chunks > 0 ? `${obj.chunks} chunks` : 'vacío');
            pipeActivate('cot', '...');
          } else if (step === 'cot') {
            pipeDone('cot', 'listo');
            pipeActivate('llm', meta.model || '...');
          } else if (step === 'thinking') {
            pipeDone('cot', 'listo');
            pipeActivate('thinking', 'razonando...');
          } else if (step === 'llm_start') {
            pipeDone('thinking', 'listo');
            pipeDone('llm', 'generando...');
          } else if (step === 'verify') {
            pipeActivate('verify', '...');
          }
          continue;
        }

        if (obj.type === 'done') {
          meta.elapsed              = obj.elapsed;
          meta.prompt_tokens        = obj.prompt_tokens     || 0;
          meta.completion_tokens    = obj.completion_tokens  || 0;
          meta.tokens_per_sec       = obj.tokens_per_sec    || 0;
          meta.context_window       = obj.context_window    || 0;
          meta.context_used_pct     = obj.context_used_pct  || 0;
          meta.cache_hit            = obj.cache_hit         || false;
          pipeDone('verify', obj.verified ? 'ok' : 'skip');
          pipeBar.classList.add('idle');
          updateSessionTokens(meta.prompt_tokens, meta.completion_tokens);
          updateCtxBar(meta.context_used_pct);
          if (meta.tokens_per_sec > 0) {
            document.getElementById('pd-llm').textContent = `${meta.tokens_per_sec} tok/s`;
          }
          continue;
        }

        // Regular text chunk
        const token = obj.choices?.[0]?.delta?.content;
        if (token) {
          fullText += token;
          bubble = document.getElementById('active-bubble');
          if (bubble) {
            bubble.innerHTML = renderMarkdown(fullText) + '<span class="stream-cursor"></span>';
            scrollDown();
          }
        }
      }
    }
  } catch(e) {
    fullText = `Error de conexión: ${e.message}`;
    meta = {};
    pipeReset();
  }

  meta.elapsed = meta.elapsed || ((Date.now() - t0) / 1000).toFixed(1);
  history.push({role:'assistant', content: fullText});
  finalizeAssistant(fullText, meta);
  saveCurrentConversation();
  scrollDown();

  isLoading = false;
  btnEl.disabled = false;
  inputEl.focus();
}

btnEl.addEventListener('click', send);
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + 'px';
});
</script>
</body>
</html>"""
