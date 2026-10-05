(() => {
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

const state = { key: "", convs: [], current: null, messages: [], streaming: false, abort: null, lastUpdated: 0, mode: "ask", effort: "normal", web: false, caps: null, workspace: "" };

/* ── Modo nativo (webview de VS Code) o embebido (iframe) ───── */
const params = new URLSearchParams(location.search);
const NATIVE = !!window.SmartOrchNative;                 // lo define la extension de VS Code
const EMBED = NATIVE || params.get("embed") === "vscode";
const WORKSPACE = (NATIVE && window.SmartOrchNative.workspace) || params.get("workspace") || "";
const host = { pending: null };
function toHost(msg) {
  if (NATIVE) window.SmartOrchNative.post({ smartorch: true, ...msg });
  else if (EMBED && parent !== window) parent.postMessage({ smartorch: true, ...msg }, "*");
}
function hostContext() {
  return new Promise((resolve) => {
    host.pending = resolve;
    toHost({ type: "getContext" });
    setTimeout(() => { if (host.pending === resolve) { host.pending = null; resolve(null); } }, 1500);
  });
}

/* prompt()/confirm() no existen en los webviews de VS Code: se delegan a sus dialogos nativos */
const dialogs = new Map();
let dialogSeq = 0;
function askHost(kind, text, value) {
  return new Promise((resolve) => {
    const id = ++dialogSeq;
    dialogs.set(id, resolve);
    toHost({ type: "dialog", id, kind, text, value });
  });
}
const dialog = {
  prompt: (text, value) => (NATIVE ? askHost("prompt", text, value) : Promise.resolve(window.prompt(text, value))),
  confirm: (text) => (NATIVE ? askHost("confirm", text) : Promise.resolve(window.confirm(text))),
};

/* ── Slash commands ─────────────────────────────────────────── */
const SLASH = {
  explain:  ["Explicar código", "Explícame este código paso a paso. ¿Qué hace, cómo funciona internamente y hay algo no obvio?"],
  fix:      ["Corregir bugs", "Encuentra todos los bugs y corrígelos. Muestra el código corregido completo y explica cada cambio:"],
  refactor: ["Refactorizar", "Refactoriza este código aplicando SOLID, DRY y clean architecture. Devuelve el resultado completo:"],
  solid:    ["SOLID / DRY", "Aplica los principios SOLID y DRY a este código. Para cada violación: qué viola → por qué → cómo lo arreglas. Devuelve el código refactorizado completo:"],
  review:   ["Code review", "Haz un code review exhaustivo. Identifica bugs, violaciones de SOLID/DRY, problemas de seguridad y rendimiento:"],
  test:     ["Generar tests", "Escribe tests unitarios completos. Cubre happy path, casos borde y casos de error:"],
  doc:      ["Documentar", "Genera documentación completa (docstrings/JSDoc) con descripción, parámetros, retorno, excepciones y ejemplo:"],
  optimize: ["Optimizar", "Analiza la complejidad actual O(?) e identifica cuellos de botella. Devuelve la versión optimizada con la nueva complejidad:"],
  yara:     ["Regla YARA", "Analiza este código/muestra y genera una regla YARA precisa con strings únicos, condición robusta y metadata completa:"],
  sigma:    ["Regla Sigma", "Genera una regla Sigma para detectar este comportamiento/IOC con logsource correcto y filtros de falsos positivos:"],
  c2:       ["Analizar C2", "Analiza este código/tráfico C2 (contexto educativo/defensivo). Mapea a MITRE ATT&CK, extrae IoCs y documenta mecanismos:"],
  pentest:  ["Guía de pentest", "Analiza este objetivo desde perspectiva de pentest (entorno controlado/educativo). Superficie de ataque, vectores y mitigaciones:"],
  web:      ["Feature web", "Implementa el siguiente requerimiento web aplicando arquitectura limpia, SOLID/DRY y separación de capas:"],
  mobile:   ["Feature mobile", "Implementa el siguiente requerimiento para aplicación móvil (Flutter/React Native) aplicando SOLID y buenas prácticas:"],
};
const STARTERS = [
  ["Analizar mi proyecto", "/analizar", "Estructura, módulos, dependencias y pendientes"],
  ["Planear un cambio", "/plan ", "Investigo y te propongo un plan, sin tocar nada"],
  ["Explicar código", "/explain ", "Pega código y te lo explico"],
  ["Generar tests", "/test ", "Casos normales, borde y de error"],
];
const CONTROL_MENU = {
  plan: ["Modo Plan", ""], agente: ["Modo Agente", ""], preguntar: ["Modo Preguntar", ""],
  rapido: ["Esfuerzo Rápido", ""], normal: ["Esfuerzo Normal", ""], maximo: ["Esfuerzo Máximo", ""],
  web: ["Activar o desactivar la búsqueda web", ""], init: ["Crear SMARTORCH.md con las reglas del proyecto", ""],
  analizar: ["Analizar el proyecto", ""],
};
const INIT_PROMPT = "Crea un archivo SMARTORCH.md en la raíz del proyecto con las instrucciones y convenciones para trabajar en él: estructura, cómo correr los tests, estilo de código y cosas a evitar. Primero lee el README y los archivos principales para basarte en lo que realmente hay.";

/* ── Transporte ─────────────────────────────────────────────── */
function parseSse(onEvent) {
  let buf = "";
  const dec = new TextDecoder();
  return (chunk) => {
    buf += dec.decode(chunk, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") continue;
      try { onEvent(JSON.parse(payload)); } catch { /* evento incompleto */ }
    }
  };
}

const webTransport = {
  async request(path, opts = {}) {
    const r = await fetch(path, { ...opts, headers: { "Authorization": `Bearer ${state.key}`, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
  },
  async stream(path, body, onEvent, signal) {
    const r = await fetch(path, { method: "POST", signal, headers: { "Authorization": `Bearer ${state.key}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const reader = r.body.getReader();
    const feed = parseSse(onEvent);
    for (;;) { const { value, done } = await reader.read(); if (done) break; feed(value); }
  },
  async config() { return (await fetch("/smartorch/ui-config")).json(); },
  async health() { const r = await fetch("/health", { signal: AbortSignal.timeout(6000) }); return r.json(); },
};

// La extension de VS Code define window.SmartOrchNative.transport (hace las peticiones por nosotros)
const transport = (NATIVE && window.SmartOrchNative.transport) || webTransport;
const api = (path, opts) => transport.request(path, opts);

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2200);
}

/* ── Markdown seguro ────────────────────────────────────────── */
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

function renderMarkdown(src) {
  const blocks = [];
  // Bloques de código (incluye uno abierto durante el streaming)
  let text = src.replace(/```([\w+-]*)\n([\s\S]*?)(?:```|$)/g, (_, lang, code) => {
    blocks.push({ lang, code: code.replace(/\n$/, "") });
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const out = [];
  let list = null;
  const flush = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of text.split("\n")) {
    const code = line.match(/^\u0000(\d+)\u0000$/);
    if (code) { flush(); out.push(`\u0000${code[1]}\u0000`); continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { flush(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { flush(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^>\s?(.*)/))) { flush(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    flush();
    if (line.trim() === "") { out.push(""); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }
  flush();
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const b = blocks[+i];
    const editorBtns = EMBED ? '<button type="button" data-insert>Insertar</button><button type="button" data-newfile>Nuevo archivo</button>' : "";
    return `<div class="codeblock"><div class="code-head"><span>${esc(b.lang || "código")}</span><span class="code-btns">${editorBtns}<button type="button" data-copy>Copiar</button></span></div><pre><code>${esc(b.code)}</code></pre></div>`;
  });
}

/* ── Render de mensajes ─────────────────────────────────────── */
function messageNode(role, content, metaText) {
  const wrap = el("div", `msg ${role}`);
  wrap.append(el("div", "avatar", role === "user" ? "Tú" : "⚡"));
  const bubble = el("div", "bubble");
  bubble.innerHTML = renderMarkdown(content);
  if (metaText) bubble.append(el("div", "msg-meta", metaText));
  wrap.append(bubble);
  return wrap;
}

let emptyNode = null;

/* El servidor guarda el prompt completo (contexto adjunto, plantilla del comando);
   al mostrarlo se compacta para que la burbuja del usuario siga siendo legible. */
function compactUser(content) {
  const attached = content.match(/^Archivo `([^`]+)` \(([^)]*)\):\n```[\s\S]*?```\n\n([\s\S]*)$/);
  if (attached) return `📎 ${attached[1]}\n\n${compactUser(attached[3])}`;
  for (const [cmd, [, prompt]] of Object.entries(SLASH)) {
    if (content.startsWith(prompt)) return `/${cmd}${content.slice(prompt.length).trim() ? "\n\n" + content.slice(prompt.length).trim() : ""}`;
  }
  return content;
}

function renderMessages() {
  const box = $("messages");
  emptyNode = emptyNode || $("empty");
  box.textContent = "";
  if (!state.messages.length) {
    emptyNode.hidden = false;
    box.append(emptyNode);
    return;
  }
  emptyNode.hidden = true;
  for (const m of state.messages) box.append(messageNode(m.role, m.display ?? (m.role === "user" ? compactUser(m.content) : m.content), m.meta));
  box.scrollTop = box.scrollHeight;
}

/* ── Conversaciones ─────────────────────────────────────────── */
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString("es", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

async function loadConversations() {
  const q = $("search").value.trim();
  try {
    const data = await api(`/smartorch/conversations?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    state.convs = data.conversations;
    renderList();
    const cur = state.convs.find((c) => c.id === state.current);
    if (cur && !state.streaming && cur.updated_at > state.lastUpdated) await openConversation(cur.id, true);
  } catch (e) { /* se reintenta en el siguiente ciclo */ }
}

function renderList() {
  const list = $("convList");
  list.textContent = "";
  if (!state.convs.length) { list.append(el("div", "list-empty", $("search").value ? "Sin resultados" : "Aún no hay conversaciones")); return; }
  for (const c of state.convs) {
    const row = el("div", "conv" + (c.id === state.current ? " active" : ""));
    row.setAttribute("role", "listitem");
    const main = el("div", "conv-main");
    main.append(el("div", "conv-title", c.title));
    const meta = el("div", "conv-meta");
    meta.append(el("span", `badge ${c.source}`, c.source), el("span", "", `${c.message_count} msj · ${fmtDate(c.updated_at)}`));
    main.append(meta);
    const actions = el("div", "conv-actions");
    const ren = el("button", "icon-btn", "✎"); ren.title = "Renombrar";
    const del = el("button", "icon-btn", "🗑"); del.title = "Eliminar";
    ren.onclick = async (e) => { e.stopPropagation(); const t = await dialog.prompt("Nuevo título", c.title); if (t) { await api(`/smartorch/conversations/${c.id}`, { method: "PATCH", body: JSON.stringify({ title: t }) }); loadConversations(); } };
    del.onclick = async (e) => { e.stopPropagation(); if (await dialog.confirm("¿Eliminar esta conversación?")) { await api(`/smartorch/conversations/${c.id}`, { method: "DELETE" }); if (state.current === c.id) newChat(); loadConversations(); } };
    actions.append(ren, del);
    row.append(main, actions);
    row.onclick = () => { openConversation(c.id); $("sidebar").classList.remove("open"); };
    list.append(row);
  }
}

async function openConversation(id, silent) {
  const conv = await api(`/smartorch/conversations/${id}`);
  state.current = conv.id;
  state.lastUpdated = conv.updated_at;
  state.messages = conv.messages.map((m) => ({ role: m.role, content: m.content }));
  setHeader(conv.title, conv.source);
  renderMessages();
  renderList();
  history.replaceState(null, "", `/?c=${conv.id}`);
  if (!silent) $("input").focus();
}

function newChat() {
  if (state.streaming) return;
  state.current = null; state.messages = []; state.lastUpdated = 0;
  setHeader("Nueva conversación", null);
  renderMessages(); renderList();
  history.replaceState(null, "", "/");
  $("input").focus();
}

function setHeader(title, source) {
  $("topTitle").textContent = title;
  const chip = $("sourceChip");
  chip.hidden = !source; chip.textContent = source ? `iniciada en ${source}` : "";
  $("vscodeBtn").hidden = $("copyMdBtn").hidden = !state.current;
}

/* ── Agente: tarjetas de herramientas y aprobaciones ────────── */
const pendingApprovals = new Set();
const TOOL_ICONS = { list_files: "📂", glob: "📂", read_file: "📖", search_text: "🔎", write_file: "📝", edit_file: "✏️", run_command: "⚙️", run_tests: "🧪", explore: "🧭", web_search: "🌐", web_fetch: "🌐" };
const HIDDEN_TOOLS = new Set(["todo_write", "ask_user"]); // tienen su propia tarjeta

function renderDiff(text) {
  const pre = el("pre", "diff");
  for (const line of text.split("\n")) {
    const cls = line.startsWith("+++") || line.startsWith("---") ? "" : line.startsWith("+") ? "d-add" : line.startsWith("-") ? "d-del" : line.startsWith("@@") ? "d-hunk" : "";
    const span = el("span", cls, line); pre.append(span, "\n");
  }
  return pre;
}

async function decide(id, ok, card) {
  pendingApprovals.delete(id);
  card.querySelector(".tool-actions")?.remove();
  card.querySelector(".tool-state").textContent = ok ? "aprobado, ejecutando…" : "rechazado";
  try { await api("/smartorch/agent/approve", { method: "POST", body: JSON.stringify({ call_id: id, approve: ok }) }); }
  catch (e) { card.querySelector(".tool-state").textContent = "no se pudo enviar la decisión"; }
}

function toolCard(ev) {
  const card = el("div", "tool" + (ev.needs_approval ? " asks" : ""));
  const arg = ev.args.path || ev.args.pattern || ev.args.command || ev.args.query || ev.args.url || ev.args.question || "";
  const head = el("div", "tool-head");
  head.append(el("span", "", TOOL_ICONS[ev.name] || "🔧"), el("b", "", ev.name), el("span", "tool-arg", String(arg)),
              el("span", "tool-state", ev.needs_approval ? "espera tu aprobación" : "ejecutando…"));
  card.append(head);
  if (ev.preview) {
    const body = el("div", "tool-body");
    body.append(["write_file", "edit_file"].includes(ev.name) ? renderDiff(ev.preview) : el("pre", "diff", ev.preview));
    card.append(body);
  }
  if (ev.needs_approval) {
    pendingApprovals.add(ev.id);
    const actions = el("div", "tool-actions");
    const label = ["run_command", "run_tests"].includes(ev.name) ? "Ejecutar" : ev.network ? "Permitir" : "Aprobar";
    const yes = el("button", "btn primary", label);
    const no = el("button", "btn", "Rechazar");
    yes.onclick = () => decide(ev.id, true, card);
    no.onclick = () => decide(ev.id, false, card);
    actions.append(yes, no);
    if (NATIVE && ev.proposed) {
      const view = el("button", "btn", "Ver diff en VS Code");
      view.onclick = () => toHost({ type: "diff", path: ev.proposed.path, content: ev.proposed.content });
      actions.append(view);
    }
    card.append(actions);
  }
  return card;
}

function todoCard(todos) {
  const card = el("div", "todo");
  card.append(el("div", "todo-title", `🗒️ Tareas (${todos.filter((t) => t.done).length}/${todos.length})`));
  for (const t of todos) card.append(el("div", "todo-item" + (t.done ? " done" : ""), (t.done ? "☑ " : "☐ ") + t.text));
  return card;
}

function askCard(ev) {
  const card = el("div", "ask");
  card.append(el("div", "ask-q", "❓ " + ev.question));
  const opts = el("div", "ask-opts");
  const free = el("div", "ask-free");
  const reply = async (answer) => {
    if (!answer.trim()) return;
    pendingApprovals.delete(ev.id);
    opts.remove(); free.remove();
    card.append(el("div", "ask-a", "Tú: " + answer));
    try { await api("/smartorch/agent/answer", { method: "POST", body: JSON.stringify({ call_id: ev.id, answer }) }); } catch { /* sin respuesta */ }
  };
  for (const o of ev.options || []) { const b = el("button", "btn", o); b.onclick = () => reply(o); opts.append(b); }
  const input = el("input"); input.placeholder = "Escribe tu respuesta…";
  const go = el("button", "btn primary", "Responder");
  input.onkeydown = (e) => { if (e.key === "Enter") reply(input.value); };
  go.onclick = () => reply(input.value);
  free.append(input, go);
  card.append(opts, free);
  pendingApprovals.add(ev.id);
  return card;
}

function agentEvent(ev, ctx) {
  const cards = ctx.cards;
  const target = ev.agent && ctx.subBox ? ctx.subBox : ctx.logBox;
  if (ev.type === "text") target.append(el("div", "agent-note", ev.content));
  else if (ev.type === "todo") {
    const card = todoCard(ev.todos);
    if (ctx.todo) ctx.todo.replaceWith(card); else ctx.logBox.append(card);
    ctx.todo = card;
  }
  else if (ev.type === "ask") ctx.logBox.append(askCard(ev));
  else if (ev.type === "compact") target.append(el("div", "agent-note", "Contexto compactado para ahorrar memoria."));
  else if (ev.type === "candidate") target.append(el("div", "agent-note", "↺ Los tests siguen fallando: deshice los cambios y pruebo otro enfoque (intento " + ev.attempt + " de " + ev.of + ")."));
  else if (ev.type === "tool_call") {
    if (HIDDEN_TOOLS.has(ev.name)) cards.set(ev.id, null);
    else {
      const c = toolCard(ev); cards.set(ev.id, c); target.append(c);
      if (ev.name === "explore") { const sub = el("div", "agent-sub"); c.append(sub); ctx.subBox = sub; }
    }
  }
  else if (ev.type === "tool_result") {
    const c = cards.get(ev.id); if (!c) return;
    c.classList.remove("asks"); c.classList.add(ev.ok ? "ok" : "bad");
    c.querySelector(".tool-state").textContent = ev.ok ? "listo" : "falló";
    if (ev.output) {
      const d = el("details", "tool-out"); d.append(el("summary", "", "Resultado"), el("pre", "", ev.output));
      c.append(d);
    }
    if (ev.name === "explore") ctx.subBox = null;
  }
  else if (ev.type === "final") { if (!ev.agent) { ctx.set(ev.content); if (ev.plan) ctx.plan = ev.content; ctx.paint(); } }
  else if (ev.type === "error") { ctx.set(ctx.get() + `**Error:** ${ev.message}`); ctx.paint(); }
  else if (ev.type === "done") ctx.stats(ev);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function denyPending() {
  for (const id of [...pendingApprovals]) { pendingApprovals.delete(id); api("/smartorch/agent/approve", { method: "POST", body: JSON.stringify({ call_id: id, approve: false }) }).catch(() => {}); }
}

function addPlanActions(bubble, plan) {
  const bar = el("div", "plan-actions");
  const go = el("button", "btn primary", "▶ Ejecutar este plan");
  const edit = el("button", "btn", "✎ Ajustar el plan");
  go.onclick = () => { bar.remove(); setMode("agent"); send("Ejecuta el siguiente plan aprobado, paso a paso, y verifica al terminar:\n\n" + plan, { display: "▶ Ejecutar el plan" }); };
  edit.onclick = () => { bar.remove(); $("input").placeholder = "Dime qué quieres cambiar del plan…"; $("input").focus(); };
  bar.append(go, edit);
  bubble.append(bar);
}

/* ── Modo, esfuerzo y web ──────────────────────────────────── */
function savePref(k, v) { try { localStorage.setItem("smartorch_" + k, v); } catch { /* sin almacenamiento */ } }
function loadPref(k, d) { try { return localStorage.getItem("smartorch_" + k) ?? d; } catch { return d; } }

const PLACEHOLDERS = {
  ask: "Pregunta sobre tu proyecto o escribe / para comandos…  (Enter envía · Shift+Enter nueva línea)",
  plan: "Describe lo que quieres lograr; investigaré y te propondré un plan, sin modificar nada…",
  agent: "Pide un cambio o una tarea; leeré, editaré y verificaré con tu aprobación…",
};

function setMode(mode) {
  state.mode = mode; savePref("mode", mode);
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  const acting = mode !== "ask";
  $("approvalMode").hidden = mode !== "agent";
  $("webLabel").hidden = !acting || !state.caps?.web;
  $("wsChip").hidden = !acting || !state.workspace;
  $("input").placeholder = PLACEHOLDERS[mode];
}

function setEffort(effort) {
  state.effort = effort; savePref("effort", effort);
  document.querySelectorAll("#effortSeg button").forEach((b) => b.classList.toggle("on", b.dataset.effort === effort));
}

function setWeb(on) {
  state.web = on; savePref("web", on ? "1" : "0");
  $("webToggle").checked = on;
}

/* Comandos que cambian la configuracion en vez de ir al modelo */
function handleControl(text) {
  const m = text.match(/^\/(plan|agente|preguntar|rapido|normal|maximo|web|init)\b\s*([\s\S]*)$/i);
  if (!m) return false;
  const cmd = m[1].toLowerCase(), rest = m[2].trim();
  $("input").value = ""; autosize(); hideSlash();
  if (cmd === "plan" || cmd === "agente" || cmd === "preguntar") {
    setMode(cmd === "plan" ? "plan" : cmd === "agente" ? "agent" : "ask");
    if (rest) send(rest); else toast(`Modo ${cmd}`);
  } else if (cmd === "init") {
    setMode("agent"); send(INIT_PROMPT, { display: "/init" });
  } else if (cmd === "web") {
    if (!state.caps?.web) toast("La búsqueda web está desactivada en este servidor");
    else { setWeb(!state.web); toast(state.web ? "Búsqueda web activada: cada consulta te la mostraré" : "Búsqueda web desactivada"); }
  } else {
    setEffort(cmd); toast(`Esfuerzo ${cmd}`);
    if (rest) send(rest);
  }
  return true;
}

async function runAnalysis(text) {
  if (!state.current) state.current = (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : String(Date.now())).slice(0, 12);
  $("input").value = ""; autosize(); hideSlash();
  state.messages.push({ role: "user", content: text });
  state.messages.push({ role: "assistant", content: "_Analizando el proyecto…_" });
  renderMessages();
  let report;
  try {
    const q = state.workspace ? `?root=${encodeURIComponent(state.workspace)}&refresh=true` : "?refresh=true";
    report = await api(`/smartorch/analysis/report${q}`);
  } catch (e) { report = `No pude analizar el proyecto: ${e.message}. Indexa un workspace primero.`; }
  state.messages[state.messages.length - 1].content = report;
  renderMessages();
  try {
    await api(`/smartorch/conversations/${state.current}/messages`, { method: "POST", body: JSON.stringify({ role: "user", content: text, source: EMBED ? "vscode" : "web" }) });
    await api(`/smartorch/conversations/${state.current}/messages`, { method: "POST", body: JSON.stringify({ role: "assistant", content: report, source: EMBED ? "vscode" : "web" }) });
    await loadConversations();
  } catch { /* el informe ya esta en pantalla */ }
}

/* ── Envío con streaming ────────────────────────────────────── */
function expandSlash(text) {
  const m = text.match(/^\/(\w+)\s*([\s\S]*)$/);
  if (!m || !SLASH[m[1]]) return text;
  return m[2].trim() ? `${SLASH[m[1]][1]}\n\n${m[2].trim()}` : SLASH[m[1]][1];
}

function setBusy(busy) {
  state.streaming = busy;
  $("sendBtn").hidden = busy; $("stopBtn").hidden = !busy;
  $("pipeline").hidden = !busy || state.mode !== "ask";
  if (!busy) document.querySelectorAll(".pipeline span").forEach((s) => (s.className = ""));
}

function markStep(step) {
  const order = ["router", "rag", "cot", "thinking", "llm"];
  const key = step === "llm_start" ? "llm" : step;
  const idx = order.indexOf(key);
  document.querySelectorAll(".pipeline span").forEach((s) => {
    const i = order.indexOf(s.dataset.step);
    s.className = i < idx ? "done" : i === idx ? "on" : s.className;
  });
}

async function send(raw, opts = {}) {
  const text = (raw ?? $("input").value).trim();
  if (!text || state.streaming) return;
  if (/^\/analizar\b/i.test(text)) return runAnalysis(text);
  if (handleControl(text)) return;
  $("input").value = ""; autosize(); hideSlash();
  if (!state.current) state.current = (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : String(Date.now())).slice(0, 12);

  let content = expandSlash(text), display = opts.display ?? text;
  if (EMBED && $("ctxToggle")?.checked) {
    const ctx = await hostContext();
    const body = ctx && (ctx.selection || ctx.text);
    if (body) {
      content = `Archivo \`${ctx.file}\` (${ctx.language}):\n\`\`\`${ctx.language}\n${body}\n\`\`\`\n\n${content}`;
      display = `📎 ${ctx.file}${ctx.selection ? " (selección)" : ""}\n\n${text}`;
    }
  }
  state.messages.push({ role: "user", content, display });
  renderMessages();
  setHeader($("topTitle").textContent, null);

  const box = $("messages");
  const live = messageNode("assistant", "");
  const bubble = live.querySelector(".bubble");
  bubble.classList.add("typing");
  box.append(live); box.scrollTop = box.scrollHeight;

  let answer = "", stats = null, raf = 0;
  const useAgent = state.mode !== "ask";
  let logBox = null, answerBox = bubble;
  if (useAgent) { logBox = el("div", "agent-log"); answerBox = el("div", "agent-answer"); bubble.append(logBox, answerBox); }
  const paint = () => { raf = 0; answerBox.innerHTML = renderMarkdown(answer); box.scrollTop = box.scrollHeight; };
  state.abort = new AbortController();
  setBusy(true);
  // un unico contexto por turno: las tarjetas de herramientas se actualizan entre eventos
  const agentCtx = { logBox, paint, cards: new Map(), set: (a) => { answer = a; }, get: () => answer, stats: (s) => { stats = s; } };
  try {
    if (useAgent) {
      await transport.stream("/smartorch/agent/run", {
        mode: state.mode, approval: $("approvalMode").value, effort: state.effort, web: state.web,
        conversation_id: state.current, source: EMBED ? "vscode" : "web",
        workspace: state.workspace || undefined, title: opts.display ?? text,
        messages: state.messages.map((m) => ({ role: m.role, content: m.content })),
      }, (ev) => agentEvent(ev, agentCtx), state.abort.signal);
    } else await transport.stream("/v1/chat/completions", {
      stream: true, max_tokens: 2048, effort: state.effort, conversation_id: state.current, source: EMBED ? "vscode" : "web",
      workspace: WORKSPACE || undefined, title: opts.display ?? text,
      messages: state.messages.map((m) => ({ role: m.role, content: m.content })),
    }, (ev) => {
      if (ev.type === "status") markStep(ev.step);
      else if (ev.type === "done") stats = ev;
      else if (ev.choices?.[0]?.delta?.content) { answer += ev.choices[0].delta.content; if (!raf) raf = requestAnimationFrame(paint); }
    }, state.abort.signal);
  } catch (e) {
    if (e.name !== "AbortError") answer += `\n\n**Error:** ${e.message}`;
  } finally {
    bubble.classList.remove("typing");
    state.abort = null; setBusy(false); denyPending();
    if (raf) cancelAnimationFrame(raf);
    answerBox.innerHTML = renderMarkdown(answer || "_(sin respuesta)_");
    const meta = stats ? [stats.model, stats.steps ? `${stats.steps} pasos` : "", stats.tokens_per_sec ? `${stats.tokens_per_sec.toFixed(1)} tok/s` : "", `${stats.elapsed}s`].filter(Boolean).join(" · ") : "";
    if (meta) bubble.append(el("div", "msg-meta", meta));
    if (agentCtx.plan) addPlanActions(bubble, agentCtx.plan);
    state.messages.push({ role: "assistant", content: answer });
    state.lastUpdated = Infinity; // este turno ya esta en pantalla: evita recargarlo desde el servidor
    await loadConversations();
    const cur = state.convs.find((c) => c.id === state.current);
    if (cur) { state.lastUpdated = cur.updated_at; setHeader(cur.title, cur.source); }
  }
}

/* ── Compositor: autosize + menú de slash ───────────────────── */
function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }
let slashSel = 0;
function slashMatches() { const v = $("input").value; if (!v.startsWith("/") || v.includes(" ")) return []; return Object.entries({ ...CONTROL_MENU, ...SLASH }).filter(([k]) => k.startsWith(v.slice(1).toLowerCase())); }
function hideSlash() { $("slashMenu").hidden = true; }
function showSlash() {
  const items = slashMatches(); const menu = $("slashMenu");
  if (!items.length) return hideSlash();
  slashSel = Math.min(slashSel, items.length - 1);
  menu.textContent = "";
  items.forEach(([k, [label]], i) => {
    const row = el("div", "slash-item" + (i === slashSel ? " sel" : ""));
    row.append(el("b", "", "/" + k), el("span", "", label));
    row.onmousedown = (e) => { e.preventDefault(); pickSlash(k); };
    menu.append(row);
  });
  menu.hidden = false;
}
function pickSlash(k) { $("input").value = `/${k} `; hideSlash(); $("input").focus(); autosize(); }

/* ── Estado del servidor, tema y config ─────────────────────── */
async function checkHealth() {
  const box = $("status");
  try {
    const h = await transport.health();
    box.className = "status ok";
    $("statusText").textContent = `Conectado · ${h.rag_mode === "semantic" ? "RAG semántico" : "RAG TF-IDF"}`;
  } catch { box.className = "status bad"; $("statusText").textContent = "Sin conexión con el servidor"; }
}

function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; }

async function init() {
  try { applyTheme(localStorage.getItem("smartorch_theme")); } catch {}
  try {
    const cfg = await transport.config();
    state.key = cfg.api_key || "";
    state.auth = cfg.auth !== false;
    state.workspace = WORKSPACE || cfg.workspace || "";
    if (cfg.donate_url) { const d = $("donate"); d.href = cfg.donate_url; d.hidden = false; }
  } catch {}
  if (!state.key && state.auth !== false && !NATIVE) {
    try { state.key = localStorage.getItem("smartorch_key") || ""; } catch {}
    if (!state.key) {
      state.key = (prompt("Este servidor pide una API key (SMARTORCH_API_KEY):") || "").trim();
      try { localStorage.setItem("smartorch_key", state.key); } catch {}
    }
  }

  for (const [title, text, sub] of STARTERS) {
    const b = el("button", "starter"); b.type = "button";
    b.append(el("b", "", title), el("span", "", sub));
    b.onclick = () => { $("input").value = text; $("input").focus(); autosize(); showSlash(); };
    $("starters").append(b);
  }

  $("newChat").onclick = newChat;
  if (!state.workspace) state.workspace = WORKSPACE;
  $("wsChip").textContent = "📁 " + (state.workspace.split(/[\\/]/).filter(Boolean).pop() || "");
  try { state.caps = await api("/smartorch/capabilities"); } catch { state.caps = { web: false }; }
  document.querySelectorAll("#modeSeg button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  document.querySelectorAll("#effortSeg button").forEach((b) => (b.onclick = () => setEffort(b.dataset.effort)));
  $("webToggle").onchange = (e) => setWeb(e.target.checked);
  setWeb(loadPref("web", "0") === "1");
  setEffort(loadPref("effort", "normal"));
  setMode(loadPref("mode", NATIVE ? "agent" : "ask"));
  $("menuBtn").onclick = () => $("sidebar").classList.toggle("open");
  $("themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "dark" ? "light" : "dark"; applyTheme(next);
    try { localStorage.setItem("smartorch_theme", next); } catch {}
  };
  $("sendBtn").onclick = () => send();
  $("stopBtn").onclick = () => state.abort?.abort();
  $("vscodeBtn").onclick = () => { location.href = `vscode://smartorch.smartorch/open?conv=${state.current}`; };
  $("copyMdBtn").onclick = async () => { try { await navigator.clipboard.writeText(await api(`/smartorch/conversations/${state.current}/export`)); toast("Conversación copiada como Markdown"); } catch { toast("No se pudo copiar"); } };
  $("messages").addEventListener("click", async (e) => {
    const block = e.target.closest(".codeblock");
    if (!block) return;
    const code = block.querySelector("code").textContent;
    const lang = block.querySelector(".code-head span").textContent;
    if (e.target.closest("[data-insert]")) return toHost({ type: "insert", text: code });
    if (e.target.closest("[data-newfile]")) return toHost({ type: "newFile", text: code, language: lang === "código" ? undefined : lang });
    const b = e.target.closest("[data-copy]"); if (!b) return;
    try { await navigator.clipboard.writeText(code); b.textContent = "Copiado ✓"; setTimeout(() => (b.textContent = "Copiar"), 1400); } catch {}
  });

  if (EMBED) {
    document.body.classList.add("embed");
    $("ctxLabel").hidden = false;
    $("vscodeBtn").style.display = "none";
    if (NATIVE) $("themeBtn").style.display = "none";
    addEventListener("message", (e) => {
      if ((!NATIVE && e.source !== parent) || !e.data) return;
      const m = e.data;
      if (m.type === "dialogResult") { const r = dialogs.get(m.id); dialogs.delete(m.id); if (r) r(m.value); return; }
      if (m.type === "context" && host.pending) { const r = host.pending; host.pending = null; r(m); }
      else if (m.type === "prompt" && typeof m.text === "string") { newChat(); if (m.send) send(m.text); else { $("input").value = m.text; autosize(); $("input").focus(); } }
      else if (m.type === "open" && typeof m.conv === "string") openConversation(m.conv).catch(() => toast("No encontré esa conversación"));
    });
  }
  let st; $("search").addEventListener("input", () => { clearTimeout(st); st = setTimeout(loadConversations, 250); });

  const input = $("input");
  input.addEventListener("input", () => { autosize(); showSlash(); });
  input.addEventListener("keydown", (e) => {
    const items = slashMatches();
    if (items.length && !$("slashMenu").hidden) {
      if (e.key === "ArrowDown") { e.preventDefault(); slashSel = (slashSel + 1) % items.length; return showSlash(); }
      if (e.key === "ArrowUp") { e.preventDefault(); slashSel = (slashSel - 1 + items.length) % items.length; return showSlash(); }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); return pickSlash(items[slashSel][0]); }
      if (e.key === "Escape") return hideSlash();
    }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); } });

  await checkHealth();
  await loadConversations();
  const want = new URLSearchParams(location.search).get("c");
  if (want) { try { await openConversation(want, true); } catch { toast("No encontré esa conversación"); } }
  setInterval(checkHealth, 15000);
  setInterval(loadConversations, 10000);
  input.focus();
  toHost({ type: "ready" }); // el editor ya puede enviarnos prompts y conversaciones
}

init();
})();
