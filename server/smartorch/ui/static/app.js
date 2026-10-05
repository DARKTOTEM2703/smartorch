(() => {
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

const state = { key: "", convs: [], current: null, messages: [], streaming: false, abort: null, lastUpdated: 0 };

/* ── Modo embebido (panel de VS Code) ───────────────────────── */
const params = new URLSearchParams(location.search);
const EMBED = params.get("embed") === "vscode";
const WORKSPACE = params.get("workspace") || "";
const host = { pending: null };
function toHost(msg) { if (EMBED && parent !== window) parent.postMessage({ smartorch: true, ...msg }, "*"); }
function hostContext() {
  return new Promise((resolve) => {
    host.pending = resolve;
    toHost({ type: "getContext" });
    setTimeout(() => { if (host.pending === resolve) { host.pending = null; resolve(null); } }, 1500);
  });
}

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
  ["Explicar código", "/explain ", "Pega código y te lo explico"],
  ["Generar tests", "/test ", "Casos normales, borde y de error"],
  ["Regla YARA", "/yara ", "Para threat hunting"],
  ["Mi proyecto", "¿Cómo está organizado este proyecto y por dónde debería empezar a leerlo?", "Usa el RAG del workspace"],
];

/* ── API ────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Authorization": `Bearer ${state.key}`, "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

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
    ren.onclick = async (e) => { e.stopPropagation(); const t = prompt("Nuevo título", c.title); if (t) { await api(`/smartorch/conversations/${c.id}`, { method: "PATCH", body: JSON.stringify({ title: t }) }); loadConversations(); } };
    del.onclick = async (e) => { e.stopPropagation(); if (confirm("¿Eliminar esta conversación?")) { await api(`/smartorch/conversations/${c.id}`, { method: "DELETE" }); if (state.current === c.id) newChat(); loadConversations(); } };
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

/* ── Envío con streaming ────────────────────────────────────── */
function expandSlash(text) {
  const m = text.match(/^\/(\w+)\s*([\s\S]*)$/);
  if (!m || !SLASH[m[1]]) return text;
  return m[2].trim() ? `${SLASH[m[1]][1]}\n\n${m[2].trim()}` : SLASH[m[1]][1];
}

function setBusy(busy) {
  state.streaming = busy;
  $("sendBtn").hidden = busy; $("stopBtn").hidden = !busy;
  $("pipeline").hidden = !busy;
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

async function send(raw) {
  const text = (raw ?? $("input").value).trim();
  if (!text || state.streaming) return;
  $("input").value = ""; autosize(); hideSlash();
  if (!state.current) state.current = (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : String(Date.now())).slice(0, 12);

  let content = expandSlash(text), display = text;
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
  const paint = () => { raf = 0; bubble.innerHTML = renderMarkdown(answer); box.scrollTop = box.scrollHeight; };
  state.abort = new AbortController();
  setBusy(true);
  try {
    const resp = await fetch("/v1/chat/completions", {
      method: "POST", signal: state.abort.signal,
      headers: { "Authorization": `Bearer ${state.key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        stream: true, max_tokens: 2048, conversation_id: state.current, source: EMBED ? "vscode" : "web",
        workspace: WORKSPACE || undefined,
        messages: state.messages.map((m) => ({ role: m.role, content: m.content })),
      }),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;
        let ev; try { ev = JSON.parse(payload); } catch { continue; }
        if (ev.type === "status") markStep(ev.step);
        else if (ev.type === "done") stats = ev;
        else if (ev.choices?.[0]?.delta?.content) { answer += ev.choices[0].delta.content; if (!raf) raf = requestAnimationFrame(paint); }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") answer += `\n\n**Error:** ${e.message}`;
  } finally {
    bubble.classList.remove("typing");
    state.abort = null; setBusy(false);
    if (raf) cancelAnimationFrame(raf);
    bubble.innerHTML = renderMarkdown(answer || "_(sin respuesta)_");
    const meta = stats ? [stats.model, stats.tokens_per_sec ? `${stats.tokens_per_sec.toFixed(1)} tok/s` : "", `${stats.elapsed}s`].filter(Boolean).join(" · ") : "";
    if (meta) bubble.append(el("div", "msg-meta", meta));
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
function slashMatches() { const v = $("input").value; if (!v.startsWith("/") || v.includes(" ")) return []; return Object.entries(SLASH).filter(([k]) => k.startsWith(v.slice(1).toLowerCase())); }
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
    const r = await fetch("/health", { signal: AbortSignal.timeout(6000) });
    const h = await r.json();
    box.className = "status ok";
    $("statusText").textContent = `Conectado · ${h.rag_mode === "semantic" ? "RAG semántico" : "RAG TF-IDF"}`;
  } catch { box.className = "status bad"; $("statusText").textContent = "Sin conexión con el servidor"; }
}

function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; }

async function init() {
  try { applyTheme(localStorage.getItem("smartorch_theme")); } catch {}
  try {
    const cfg = await (await fetch("/smartorch/ui-config")).json();
    state.key = cfg.api_key || "";
    state.auth = cfg.auth !== false;
    if (cfg.donate_url) { const d = $("donate"); d.href = cfg.donate_url; d.hidden = false; }
  } catch {}
  if (!state.key && state.auth !== false) {
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
    addEventListener("message", (e) => {
      if (e.source !== parent || !e.data) return;
      const m = e.data;
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
}

init();
})();
