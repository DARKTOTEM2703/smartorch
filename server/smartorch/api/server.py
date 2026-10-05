"""
SmartOrch API — FastAPI, compatible con OpenAI
Endpoints:
  GET  /                       → Web UI
  GET  /health                 → status
  GET  /v1/models              → lista modelos
  POST /v1/chat/completions    → chat principal (con RAG + CoT)
  POST /v1/completions         → completions legacy (autocompletado)
  GET  /smartorch/status       → estado interno detallado
  POST /smartorch/index        → indexar/reindexar directorio
"""
import time
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import asyncio

from smartorch.config import HOST, PORT, API_KEY, MODELS, AGENT_WORK_DIR, model_context_window
from smartorch.core import ollama_client as ollama, indexer
from smartorch.core import orchestrator, history, gating
from smartorch.ui.web import HTML

logger = logging.getLogger(__name__)

# ── Conteo global de tokens (como OpenAI/Anthropic) ─────────────────────────
_usage = {
    "total_requests":        0,
    "total_prompt_tokens":   0,
    "total_completion_tokens": 0,
    "session_start":         time.time(),
}

def _record_usage(prompt_tokens: int, completion_tokens: int):
    _usage["total_requests"]          += 1
    _usage["total_prompt_tokens"]     += prompt_tokens
    _usage["total_completion_tokens"] += completion_tokens


# ── Lifespan: cargar índice + auto-index del workspace ──────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    idx = indexer.get_index()
    if idx.load():
        logger.info(f"[INDEX] Cargado: {idx.size} chunks (TF-IDF)")
    _try_load_rag()

    # Auto-index AGENT_WORK_DIR si aún no tiene chunks
    if os.path.isdir(AGENT_WORK_DIR) and idx.size == 0:
        asyncio.create_task(_auto_index_workspace(AGENT_WORK_DIR))

    yield


async def _auto_index_workspace(root: str):
    """Indexa AGENT_WORK_DIR en background al arrancar (TF-IDF + ChromaDB)."""
    try:
        n = await asyncio.to_thread(indexer.reindex, root)
        logger.info(f"[AUTO-INDEX] TF-IDF: {n} chunks desde {root}")
        try:
            m = await asyncio.to_thread(_build_rag_index, root)
            logger.info(f"[AUTO-INDEX] RAG: {m} chunks desde {root}")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[AUTO-INDEX] Falló: {e}")


def _try_load_rag():
    """Intenta cargar el módulo RAG semántico; si no hay deps, usa TF-IDF."""
    try:
        from smartorch.rag.store import collection_ready
        if collection_ready():
            logger.info("[RAG] ChromaDB listo — búsqueda semántica activa")
        else:
            logger.info("[RAG] ChromaDB vacío — indexa con: python index_project.py <ruta>")
    except ImportError:
        logger.info("[RAG] sentence-transformers/chromadb no instalados — usando TF-IDF")


app = FastAPI(title="SmartOrch", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ─────────────────────────────────────────────────────────────────────
async def verify_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        return
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida")


# ── Schemas ──────────────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    model:       Optional[str]   = None
    max_tokens:  Optional[int]   = 2048
    temperature: Optional[float] = 0.3
    stream:      Optional[bool]  = False
    # Extensiones SmartOrch: si viene conversation_id el intercambio se guarda
    # en el historial compartido (web, CLI y editor).
    conversation_id: Optional[str] = None
    source:          Optional[str] = "api"
    workspace:       Optional[str] = None


def _last_user_text(msgs: list[dict]) -> str:
    for m in reversed(msgs):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def _persist_turn(req: "ChatRequest", msgs: list[dict], answer: str) -> None:
    if not req.conversation_id:
        return
    try:
        history.save_turn(req.conversation_id, req.source or "api", _last_user_text(msgs), answer, req.workspace)
    except Exception as e:
        logger.warning(f"[HISTORY] no se pudo guardar: {e}")


class CompletionRequest(BaseModel):
    prompt:      str
    model:       Optional[str]   = None
    max_tokens:  Optional[int]   = 128
    temperature: Optional[float] = 0.1

class IndexRequest(BaseModel):
    root: str


# ── Rutas básicas ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_ui():
    return HTML


@app.get("/smartorch/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    return _DASHBOARD_HTML

@app.get("/health")
async def health():
    idx = indexer.get_index()
    rag_chunks = _rag_chunk_count()
    return {
        "status": "ok",
        "service": "SmartOrch",
        "version": "2.0.0",
        "index_chunks": idx.size,
        "rag_chunks": rag_chunks,
        "rag_mode": "semantic" if rag_chunks > 0 else "tfidf",
    }

@app.get("/v1/models", dependencies=[Depends(verify_key)])
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "smartorch"}
            for m in dict.fromkeys(MODELS.values())   # sin duplicados, orden preservado
        ],
    }

@app.get("/smartorch/cache")
async def cache_stats():
    """Estado del cache semántico de respuestas."""
    from smartorch.core import cache as resp_cache
    return resp_cache.stats()


@app.delete("/smartorch/cache")
async def cache_clear():
    """Limpia el cache semántico."""
    from smartorch.core import cache as resp_cache
    n = resp_cache.clear()
    return {"cleared": n}


@app.get("/smartorch/usage")
async def usage():
    """Consumo de tokens de esta sesión — como OpenAI/Anthropic usage endpoint."""
    from smartorch.core import cache as resp_cache
    uptime_s  = int(time.time() - _usage["session_start"])
    total_tok = _usage["total_prompt_tokens"] + _usage["total_completion_tokens"]
    reqs      = max(_usage["total_requests"], 1)
    cs        = resp_cache.stats()
    return {
        "session_uptime_s":        uptime_s,
        "total_requests":          _usage["total_requests"],
        "usage": {
            "prompt_tokens":       _usage["total_prompt_tokens"],
            "completion_tokens":   _usage["total_completion_tokens"],
            "total_tokens":        total_tok,
        },
        "averages": {
            "prompt_tokens_per_req":     round(_usage["total_prompt_tokens"] / reqs),
            "completion_tokens_per_req": round(_usage["total_completion_tokens"] / reqs),
        },
        "cache": {
            "entries":       cs["entries"],
            "max":           cs["max"],
            "total_hits":    cs["total_hits"],
            "hit_rate_pct":  round(cs["total_hits"] / reqs * 100, 1),
        },
        "note": "Tokens reales de Ollama (prompt_eval_count / eval_count). Sin costo - modelos locales.",
    }


@app.get("/smartorch/metrics")
async def metrics():
    """Dashboard de métricas en formato JSON — para monitoreo."""
    from smartorch.core import cache as resp_cache
    uptime_s = int(time.time() - _usage["session_start"])
    reqs     = max(_usage["total_requests"], 1)
    cs       = resp_cache.stats()
    idx      = indexer.get_index()
    return {
        "uptime_s":          uptime_s,
        "requests":          _usage["total_requests"],
        "tokens_in":         _usage["total_prompt_tokens"],
        "tokens_out":        _usage["total_completion_tokens"],
        "tokens_total":      _usage["total_prompt_tokens"] + _usage["total_completion_tokens"],
        "avg_tokens_in":     round(_usage["total_prompt_tokens"] / reqs),
        "avg_tokens_out":    round(_usage["total_completion_tokens"] / reqs),
        "cache_entries":     cs["entries"],
        "cache_hits":        cs["total_hits"],
        "cache_hit_pct":     round(cs["total_hits"] / reqs * 100, 1),
        "index_chunks":      idx.size,
        "rag_chunks":        _rag_chunk_count(),
        "rag_mode":          _current_rag_mode(),
        "models":            MODELS,
    }


@app.get("/smartorch/status", dependencies=[Depends(verify_key)])
async def status():
    available = ollama.list_models()
    idx = indexer.get_index()
    return {
        "status":      "running",
        "api_url":     f"http://{HOST}:{PORT}",
        "models":      MODELS,
        "ollama":      available,
        "index_chunks": idx.size,
        "rag_chunks":  _rag_chunk_count(),
    }


# ── Chat ─────────────────────────────────────────────────────────────────────
@app.post("/v1/chat/completions", dependencies=[Depends(verify_key)])
async def chat(req: ChatRequest):
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    if not msgs:
        raise HTTPException(status_code=400, detail="messages requeridos")

    t0 = time.time()

    if req.stream:
        return StreamingResponse(
            _stream_chat(msgs, req.max_tokens or 2048, req.temperature or 0.3, req),
            media_type="text/event-stream",
        )

    result = await asyncio.to_thread(
        orchestrator.run, msgs,
        temperature=req.temperature or 0.3,
        max_tokens=req.max_tokens or 2048,
    )
    _persist_turn(req, msgs, result["content"])

    prompt_tokens = result.get("prompt_tokens") or result.get("context_chars", 0) // 4
    compl_tokens  = result.get("completion_tokens") or len(result["content"]) // 4
    tok_per_sec   = result.get("tokens_per_sec", 0.0)
    ctx_window    = model_context_window(result["model"])
    ctx_used_pct  = round(prompt_tokens / ctx_window * 100, 1) if prompt_tokens > 0 else 0.0
    _record_usage(prompt_tokens, compl_tokens)

    return {
        "id":      f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object":  "chat.completion",
        "created": int(t0),
        "model":   result["model"],
        "choices": [{
            "index":   0,
            "message": {"role": "assistant", "content": result["content"]},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": compl_tokens,
            "total_tokens":      prompt_tokens + compl_tokens,
        },
        "smartorch": {
            "task_type":        result.get("task_type"),
            "context_chars":    result.get("context_chars"),
            "elapsed":          round(time.time() - t0, 2),
            "rag_mode":         _current_rag_mode(),
            "tokens_per_sec":   tok_per_sec,
            "context_window":   ctx_window,
            "context_used_pct": ctx_used_pct,
            "confidence":       result.get("confidence", 1.0),
            "thinking":         result.get("thinking", ""),
        },
    }


async def _stream_chat(msgs, max_tokens, temperature, req: Optional["ChatRequest"] = None):
    import json
    import time
    t0 = time.time()
    collected: list[str] = []
    try:
        from smartorch.core import router, chain, compressor

        # Step 1: Router
        task_type, model = router.route(msgs)
        yield f"data: {json.dumps({'type':'status','step':'router','task_type':task_type,'model':model})}\n\n"
        await asyncio.sleep(0)

        # Step 2: RAG
        rag_ctx   = _get_rag_context(msgs)
        rag_count = len(rag_ctx.split('\n')) if rag_ctx else 0
        enriched  = list(msgs)
        if rag_ctx:
            for i in range(len(enriched) - 1, -1, -1):
                if enriched[i].get("role") == "user":
                    enriched[i] = {**enriched[i], "content": enriched[i]["content"] + f"\n\n[CONTEXTO DEL PROYECTO]\n```\n{rag_ctx}\n```"}
                    break
        yield f"data: {json.dumps({'type':'status','step':'rag','chunks':rag_count,'mode':_current_rag_mode()})}\n\n"
        await asyncio.sleep(0)

        # Step 3: CoT
        enriched = chain.inject_cot(enriched, task_type)
        fitted   = compressor.fit_context(enriched)
        yield f"data: {json.dumps({'type':'status','step':'cot'})}\n\n"
        await asyncio.sleep(0)

        # Step 3.5: Thinking mode check
        from smartorch.core.chain import should_think
        if should_think(msgs, task_type):
            yield f"data: {json.dumps({'type':'status','step':'thinking','model':model})}\n\n"
            await asyncio.sleep(0)

        # Step 4: LLM stream — capturando token_stats al final
        yield f"data: {json.dumps({'type':'status','step':'llm_start','model':model})}\n\n"
        await asyncio.sleep(0)

        stream_prompt_tokens = 0
        stream_compl_tokens  = 0
        stream_tok_per_sec   = 0.0

        for chunk in ollama.chat_stream(model=model, messages=fitted, temperature=temperature, max_tokens=max_tokens):
            if isinstance(chunk, dict) and chunk.get("type") == "token_stats":
                # Último evento del stream: estadísticas de tokens reales
                stream_prompt_tokens = chunk.get("prompt_tokens", 0)
                stream_compl_tokens  = chunk.get("completion_tokens", 0)
                stream_tok_per_sec   = chunk.get("tokens_per_sec", 0.0)
            else:
                collected.append(chunk)
                data = json.dumps({"choices": [{"delta": {"content": chunk}, "finish_reason": None}]})
                yield f"data: {data}\n\n"

        if req is not None:
            _persist_turn(req, msgs, "".join(collected))
        _record_usage(stream_prompt_tokens, stream_compl_tokens)

        # context window usage
        ctx_window  = model_context_window(model)
        ctx_used_pct = round(stream_prompt_tokens / ctx_window * 100, 1) if stream_prompt_tokens > 0 else 0.0

        # Step 5: done con stats completas
        elapsed = round(time.time() - t0, 1)
        yield f"data: {json.dumps({'type':'done','elapsed':elapsed,'verified':False,'task_type':task_type,'model':model,'prompt_tokens':stream_prompt_tokens,'completion_tokens':stream_compl_tokens,'tokens_per_sec':stream_tok_per_sec,'context_window':ctx_window,'context_used_pct':ctx_used_pct})}\n\n"

    except Exception as e:
        err = json.dumps({"choices": [{"delta": {"content": f"\n[SmartOrch error: {e}]"}, "finish_reason": "stop"}]})
        yield f"data: {err}\n\n"
    yield "data: [DONE]\n\n"


# ── Completions (autocompletado) ─────────────────────────────────────────────
@app.post("/v1/completions", dependencies=[Depends(verify_key)])
async def complete(req: CompletionRequest):
    result = await asyncio.to_thread(
        ollama.generate,
        req.prompt,
        req.model or MODELS["code"],
        req.max_tokens or 128,
    )
    return {
        "id":      f"cmpl-{uuid.uuid4().hex[:8]}",
        "object":  "text_completion",
        "created": int(time.time()),
        "model":   req.model or MODELS["code"],
        "choices": [{"text": result, "index": 0, "finish_reason": "stop"}],
    }


# ── Indexar ──────────────────────────────────────────────────────────────────
@app.post("/smartorch/index", dependencies=[Depends(verify_key)])
async def index_workspace(req: IndexRequest):
    import os
    if not os.path.isdir(req.root):
        raise HTTPException(status_code=400, detail=f"Directorio inválido: {req.root}")

    # TF-IDF siempre
    tfidf_chunks = await asyncio.to_thread(indexer.reindex, req.root)

    # RAG semántico si está disponible
    rag_chunks = 0
    try:
        rag_chunks = await asyncio.to_thread(_build_rag_index, req.root)
    except ImportError:
        pass

    return {
        "status":      "ok",
        "root":        req.root,
        "tfidf_chunks": tfidf_chunks,
        "rag_chunks":  rag_chunks,
    }


# ── Historial compartido (web · CLI · editor) ────────────────────────────────
class ConversationCreate(BaseModel):
    title:     Optional[str] = ""
    source:    Optional[str] = "api"
    workspace: Optional[str] = None
    messages:  Optional[list[Message]] = None


class ConversationRename(BaseModel):
    title: str


class MessageCreate(BaseModel):
    role:    str
    content: str
    source:  Optional[str] = "api"


@app.get("/smartorch/conversations", dependencies=[Depends(verify_key)])
async def list_conversations(q: str = "", limit: int = 100):
    return {"conversations": await asyncio.to_thread(history.list_conversations, limit, q)}


@app.post("/smartorch/conversations", dependencies=[Depends(verify_key)])
async def create_conversation(body: ConversationCreate):
    def _create():
        conv = history.create_conversation(body.title or "", body.source or "api", body.workspace)
        for m in body.messages or []:
            history.add_message(conv["id"], m.role, m.content, body.source or "api")
        return history.get_conversation(conv["id"])
    return await asyncio.to_thread(_create)


@app.get("/smartorch/conversations/{conv_id}", dependencies=[Depends(verify_key)])
async def get_conversation(conv_id: str):
    conv = await asyncio.to_thread(history.get_conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return conv


@app.get("/smartorch/conversations/{conv_id}/export", dependencies=[Depends(verify_key)])
async def export_conversation(conv_id: str):
    from fastapi.responses import PlainTextResponse
    md = await asyncio.to_thread(history.export_markdown, conv_id)
    if md is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@app.patch("/smartorch/conversations/{conv_id}", dependencies=[Depends(verify_key)])
async def rename_conversation(conv_id: str, body: ConversationRename):
    if not await asyncio.to_thread(history.rename_conversation, conv_id, body.title):
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return {"status": "ok"}


@app.delete("/smartorch/conversations/{conv_id}", dependencies=[Depends(verify_key)])
async def delete_conversation(conv_id: str):
    if not await asyncio.to_thread(history.delete_conversation, conv_id):
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return {"status": "ok"}


@app.post("/smartorch/conversations/{conv_id}/messages", dependencies=[Depends(verify_key)])
async def add_conversation_message(conv_id: str, body: MessageCreate):
    def _add():
        history.create_conversation("", body.source or "api", None, conv_id)
        history.add_message(conv_id, body.role, body.content, body.source or "api")
    await asyncio.to_thread(_add)
    return {"status": "ok"}


# ── Helpers RAG ───────────────────────────────────────────────────────────────
def _rag_chunk_count() -> int:
    try:
        from smartorch.rag.store import chunk_count
        return chunk_count()
    except ImportError:
        return 0

def _current_rag_mode() -> str:
    return "semantic" if _rag_chunk_count() > 0 else "tfidf"

def _get_rag_context(msgs: list[dict]) -> str:
    """Intenta RAG semántico; fallback a TF-IDF."""
    user_text = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
    if not gating.wants_project_context(user_text):
        return ""
    try:
        from smartorch.rag.retriever import search_formatted
        return search_formatted(user_text)
    except ImportError:
        idx = indexer.get_index()
        if idx.size == 0:
            return ""
        return idx.search_formatted(user_text, top_k=4, max_chars=2500)

def _build_rag_index(root: str) -> int:
    from smartorch.rag.chunker import index_directory
    from smartorch.rag.store import upsert_chunks
    chunks = index_directory(root)
    if chunks:
        upsert_chunks(chunks)
    return len(chunks)


# ── Iniciar servidor ─────────────────────────────────────────────────────────
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SmartOrch · Métricas</title>
<style>
:root{--bg:#0d1117;--s:#161b22;--s2:#1c2128;--b:#30363d;--t:#e6edf3;--dim:#8b949e;--mut:#484f58;--a:#58a6ff;--g:#3fb950;--r:#f85149;--y:#d29922;--p:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;padding:24px 20px;max-width:900px;margin:0 auto}
h1{font-size:20px;font-weight:700;color:var(--a);margin-bottom:4px}
.sub{font-size:13px;color:var(--dim);margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--s);border:1px solid var(--b);border-radius:8px;padding:16px}
.card-label{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.card-value{font-size:28px;font-weight:700;color:var(--t);font-variant-numeric:tabular-nums;line-height:1}
.card-unit{font-size:12px;color:var(--mut);margin-top:4px}
.card.green .card-value{color:var(--g)}
.card.blue  .card-value{color:var(--a)}
.card.yellow .card-value{color:var(--y)}
.card.purple .card-value{color:var(--p)}
h2{font-size:14px;font-weight:600;margin:16px 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.model-table{background:var(--s);border:1px solid var(--b);border-radius:8px;overflow:hidden;margin-bottom:24px}
.model-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--b)}
.model-row:last-child{border-bottom:none}
.model-type{font-size:11px;color:var(--dim);text-transform:uppercase}
.model-name{font-size:13px;font-family:monospace;color:var(--a)}
.bar-wrap{background:var(--s2);border:1px solid var(--b);border-radius:8px;padding:16px;margin-bottom:24px}
.bar-label{font-size:12px;color:var(--dim);margin-bottom:6px;display:flex;justify-content:space-between}
.bar{height:10px;border-radius:5px;background:var(--b);margin-bottom:12px}
.bar-fill{height:100%;border-radius:5px;background:var(--g);transition:width 1s ease}
.bar-fill.yellow{background:var(--y)}
.bar-fill.red{background:var(--r)}
.btn{background:var(--b);color:var(--dim);border:1px solid var(--b);border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px;transition:all .15s}
.btn:hover{background:var(--s2);color:var(--t)}
.btn.danger{color:var(--r)}
.actions{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.refresh-note{font-size:11px;color:var(--mut);margin-top:16px}
.uptime{font-size:12px;color:var(--dim);margin-top:2px}
a{color:var(--a);text-decoration:none}a:hover{text-decoration:underline}
</style>
</head>
<body>
<h1>⚡ SmartOrch · Dashboard</h1>
<p class="sub">Métricas en tiempo real · <span id="uptime-str">—</span> · <a href="/">Chat</a> · <a href="/docs">API Docs</a></p>

<div class="actions">
  <button class="btn" onclick="loadMetrics()">↻ Refrescar</button>
  <button class="btn danger" onclick="clearCache()">🗑 Limpiar Cache</button>
</div>

<div class="grid" id="cards">
  <div class="card blue"><div class="card-label">Requests</div><div class="card-value" id="m-reqs">—</div><div class="card-unit">total sesión</div></div>
  <div class="card green"><div class="card-label">Tokens entrada</div><div class="card-value" id="m-tin">—</div><div class="card-unit">prompt tokens reales</div></div>
  <div class="card purple"><div class="card-label">Tokens salida</div><div class="card-value" id="m-tout">—</div><div class="card-unit">completion tokens</div></div>
  <div class="card yellow"><div class="card-label">Cache Hit Rate</div><div class="card-value" id="m-hit">—</div><div class="card-unit">% requests desde cache</div></div>
  <div class="card"><div class="card-label">Cache entries</div><div class="card-value" id="m-ce">—</div><div class="card-unit">respuestas guardadas</div></div>
  <div class="card"><div class="card-label">RAG chunks</div><div class="card-value" id="m-rag">—</div><div class="card-unit">documentos indexados</div></div>
</div>

<h2>Uso del contexto por modelo</h2>
<div class="bar-wrap" id="ctx-bars">
  <div class="bar-label"><span>cargando...</span></div>
</div>

<h2>Modelos activos</h2>
<div class="model-table" id="model-table">
  <div class="model-row"><span class="model-type">cargando...</span></div>
</div>

<p class="refresh-note">Auto-refresh cada 10s · Tokens reales de Ollama (prompt_eval_count/eval_count)</p>

<script>
function fmtNum(n){if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'k';return String(n)}
function fmtUptime(s){const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;return `${h}h ${m}m ${ss}s`}

async function loadMetrics(){
  try{
    const r=await fetch('/smartorch/metrics');
    const d=await r.json();
    document.getElementById('m-reqs').textContent=fmtNum(d.requests);
    document.getElementById('m-tin').textContent=fmtNum(d.tokens_in);
    document.getElementById('m-tout').textContent=fmtNum(d.tokens_out);
    document.getElementById('m-hit').textContent=d.cache_hit_pct+'%';
    document.getElementById('m-ce').textContent=d.cache_entries;
    document.getElementById('m-rag').textContent=fmtNum(d.rag_chunks||d.index_chunks);
    document.getElementById('uptime-str').textContent='uptime '+fmtUptime(d.uptime_s);

    // Context bars por modelo
    const ctxMap={'hermes3:8b':8192,'qwen2.5-coder:7b':32768,'qwen2.5-coder:1.5b':32768};
    const avgIn=d.avg_tokens_in||0;
    let barsHtml='';
    new Set(Object.values(d.models||{})).forEach(m=>{
      const ctx=ctxMap[m]||8192;
      const pct=Math.min(Math.round(avgIn/ctx*100),100);
      const cls=pct>=85?'red':pct>=60?'yellow':'';
      barsHtml+=`<div class="bar-label"><span>${m}</span><span>${avgIn} / ${ctx} tok (${pct}%)</span></div><div class="bar"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>`;
    });
    document.getElementById('ctx-bars').innerHTML=barsHtml||'<div class="bar-label"><span>Sin requests aún</span></div>';

    // Model table
    let tbl='';
    const types=d.models||{};
    Object.entries(types).forEach(([type,name])=>{
      tbl+=`<div class="model-row"><span class="model-type">${type}</span><span class="model-name">${name}</span></div>`;
    });
    document.getElementById('model-table').innerHTML=tbl;
  }catch(e){console.error(e)}
}

async function clearCache(){
  if(!confirm('¿Limpiar el cache semántico?')) return;
  await fetch('/smartorch/cache',{method:'DELETE'});
  loadMetrics();
}

loadMetrics();
setInterval(loadMetrics, 10000);
</script>
</body>
</html>"""


def start():
    import uvicorn
    _print_banner()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

def _print_banner():
    print(f"""
  SmartOrch v2.0 -- Local AI Orchestrator [FastAPI]
  Modelos pequenos. RAG semantico. Sin pagar.
  -----------------------------------------------
  API (OpenAI):  http://{HOST}:{PORT}/v1/chat/completions
  Web UI:        http://localhost:{PORT}/
  Docs:          http://localhost:{PORT}/docs
  API Key:       {API_KEY[:24]}...
""")
