# SmartOrch — Roadmap Completo + Guía de Código

> Alternativa 100% libre a Continue/GitHub Copilot.
> Modelos locales, sin telemetría, sin pagar. Hecho para devs y especialistas en ciberseguridad.

---

## Decisión de arquitectura: ¿Forkar Continue o construir propio?

### Opción A — Forkar Continue ✅ RECOMENDADA
```
Pros:
  + UI profesional ya hecha (React, atajos, diff view, @mentions)
  + Tree-sitter integrado
  + Soporte multi-modelo ya implementado
  + Comunidad activa, PR/fixes gratis

Contras:
  - Codebase grande (~50k líneas TypeScript + Go)
  - Necesitas Node 20+, npm, y compilar todo
  - Algunos features están atados a sus servidores

Estrategia: clonar → apuntar todo a Ollama/SmartOrch → quitar telemetría → agregar módulos cyberrange
```

### Opción B — Construir propio (SmartOrch-VSCode actual)
```
Pros:
  + Total control, sin deuda técnica ajena
  + Liviano (~5MB vs ~200MB)

Contras:
  - Hay que reimplementar diff view, @mentions, slash commands desde cero
  - Meses de trabajo para llegar a la paridad de UI
```

---

## RUTA A: Forkar Continue (empieza aquí)

### Paso 1 — Clonar y configurar

```bash
# Clonar el repo oficial
git clone https://github.com/continuedev/continue
cd continue

# Renombrar origin para no confundir
git remote rename origin upstream
git remote add origin https://github.com/TU_USUARIO/smartorch-ide

# Instalar dependencias (requiere Node 20+)
npm install -g pnpm
pnpm install
```

### Paso 2 — Estructura del monorepo de Continue

```
continue/
├── core/                     ← Motor central (TypeScript)
│   ├── llm/                  ← Adaptadores de modelos ← MODIFICAR AQUÍ
│   │   ├── llms/
│   │   │   ├── Ollama.ts     ← Ya existe, solo configurar
│   │   │   └── OpenAI.ts     ← Apuntar a SmartOrch
│   │   └── index.ts
│   ├── context/              ← Providers de contexto (@file, @codebase)
│   │   ├── providers/
│   │   │   ├── FileContextProvider.ts
│   │   │   └── CodebaseContextProvider.ts
│   ├── indexing/             ← RAG e indexado
│   │   ├── embeddings/       ← Embeddings ← AGREGAR nomic-embed-text
│   │   └── chunk/            ← Tree-sitter chunking
│   └── config/               ← Config del usuario
│
├── extensions/vscode/        ← Extensión VS Code ← MODIFICAR
│   ├── src/
│   │   ├── extension.ts      ← Activación
│   │   ├── webviewProtocol.ts ← Comunicación extensión ↔ UI
│   │   └── ideProtocol.ts    ← Acceso al editor
│   └── package.json
│
├── gui/                      ← UI React (chat, sidebar)
│   ├── src/
│   │   ├── components/
│   │   └── pages/
│   └── package.json
│
└── binary/                   ← Proceso Node separado (el "servidor" de Continue)
    └── src/
        └── IpcIde.ts
```

### Paso 3 — Quitar telemetría (importante)

```typescript
// core/util/posthog.ts  ← VACIAR este archivo
export class Telemetry {
  static capture() {}           // no-op
  static identify() {}          // no-op
  static async setup() {}       // no-op
}
```

```typescript
// extensions/vscode/src/extension.ts
// Buscar y eliminar:
// setupCa()  → telemetría
// ContinueGUIWebviewViewProvider → cambiar nombre a SmartOrchProvider
// Todas las referencias a "continue.dev" → "smartorch.local"
```

### Paso 4 — Apuntar a Ollama/SmartOrch

```typescript
// core/config/default.ts  ← REEMPLAZAR contenido

export const defaultConfig = {
  models: [
    {
      title: "SmartOrch Chat",
      provider: "openai",
      model: "hermes3:8b",
      apiBase: "http://localhost:8080/v1",
      apiKey: "smartorch-local-key",
    },
    {
      title: "SmartOrch Code",
      provider: "openai",
      model: "qwen2.5-coder:7b",
      apiBase: "http://localhost:8080/v1",
      apiKey: "smartorch-local-key",
    },
  ],
  tabAutocompleteModel: {
    title: "SmartOrch Autocomplete",
    provider: "openai",
    model: "qwen2.5-coder:7b",
    apiBase: "http://localhost:8080/v1",
    apiKey: "smartorch-local-key",
  },
  embeddingsProvider: {
    provider: "ollama",
    model: "nomic-embed-text",
    apiBase: "http://localhost:11434",
  },
  contextProviders: [
    { name: "file" },
    { name: "codebase" },
    { name: "diff" },
    { name: "terminal" },
  ],
  slashCommands: [
    { name: "edit",    description: "Editar código seleccionado" },
    { name: "comment", description: "Agregar comentarios" },
    { name: "fix",     description: "Corregir errores" },
    { name: "test",    description: "Generar tests" },
  ],
};
```

### Paso 5 — Agregar comandos de cyberrange

```typescript
// core/commands/slash/yara.ts  ← ARCHIVO NUEVO

import { SlashCommand } from "../../..";

const YaraCommand: SlashCommand = {
  name: "yara",
  description: "Generar regla YARA para el código seleccionado",
  run: async function* ({ llm, input, ide }) {
    const selectedCode = await ide.getSelectedText();
    yield* llm.streamChat([
      {
        role: "system",
        content: `Eres un experto en threat hunting. Analiza el código y genera una regla YARA
                  que detecte este malware o comportamiento sospechoso. Incluye strings, condiciones
                  y metadata completa.`,
      },
      {
        role: "user",
        content: `Genera una regla YARA para:\n\`\`\`\n${selectedCode}\n\`\`\``,
      },
    ]);
  },
};

export default YaraCommand;
```

```typescript
// core/commands/slash/sigma.ts  ← ARCHIVO NUEVO

import { SlashCommand } from "../../..";

const SigmaCommand: SlashCommand = {
  name: "sigma",
  description: "Generar regla Sigma para Wazuh",
  run: async function* ({ llm, input, ide }) {
    const selectedCode = await ide.getSelectedText();
    yield* llm.streamChat([
      {
        role: "system",
        content: `Eres un experto en SIEM y Wazuh. Genera reglas Sigma en formato YAML
                  para detectar el comportamiento descrito. Compatible con Wazuh/OpenSearch.`,
      },
      {
        role: "user",
        content: `Genera regla Sigma para:\n\`\`\`\n${selectedCode}\n\`\`\``,
      },
    ]);
  },
};

export default SigmaCommand;
```

```typescript
// core/config/default.ts  ← AGREGAR a slashCommands:
{ name: "yara",  description: "Generar regla YARA" },
{ name: "sigma", description: "Generar regla Sigma para Wazuh" },
{ name: "c2",    description: "Analizar payload C2" },
```

### Paso 6 — Compilar y probar

```bash
cd continue/extensions/vscode

# Compilar GUI (React)
cd ../../gui && npm run build

# Compilar extensión
cd ../extensions/vscode
npm run build

# Abrir en VS Code modo debug
code .
# → F5
```

---

## RUTA B: Mejorar SmartOrch-VSCode actual

Si prefieres construir el tuyo desde cero aquí está todo el código que falta.

### Servidor FastAPI (reemplaza http.server)

```python
# smartorch/api/server.py  ← REEMPLAZAR COMPLETO

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import asyncio, json, time

from ..core.orchestrator import Orchestrator
from ..core.indexer import get_index
from ..config import Config

app = FastAPI(title="SmartOrch API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

orchestrator = Orchestrator()

# --- Auth ---
async def verify_key(authorization: str = Header(None)):
    if Config.API_KEY and authorization != f"Bearer {Config.API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Modelos Pydantic ---
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    model: Optional[str] = None
    max_tokens: Optional[int] = 2048
    temperature: Optional[float] = 0.3
    stream: Optional[bool] = False

class CompletionRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    max_tokens: Optional[int] = 128
    temperature: Optional[float] = 0.1

# --- Endpoints OpenAI-compatibles ---
@app.post("/v1/chat/completions")
async def chat(req: ChatRequest, _=Depends(verify_key)):
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    t0   = time.time()

    if req.stream:
        async def generate():
            async for chunk in orchestrator.stream(msgs, req.max_tokens):
                data = json.dumps({
                    "choices": [{"delta": {"content": chunk}, "finish_reason": None}]
                })
                yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream")

    result = await asyncio.to_thread(orchestrator.chat, msgs, req.max_tokens)
    return {
        "id": f"so-{int(t0)}",
        "object": "chat.completion",
        "model": result.get("model", req.model),
        "choices": [{"message": {"role": "assistant", "content": result["content"]}, "finish_reason": "stop"}],
        "smartorch": {"task_type": result.get("task_type"), "elapsed": round(time.time()-t0, 2)},
    }

@app.post("/v1/completions")
async def complete(req: CompletionRequest, _=Depends(verify_key)):
    from ..core.ollama_client import OllamaClient
    client = OllamaClient()
    text   = client.generate(req.prompt, req.model or Config.MODEL_CODE, req.max_tokens)
    return {
        "object": "text_completion",
        "choices": [{"text": text, "finish_reason": "stop"}],
    }

@app.get("/v1/models")
async def models(_=Depends(verify_key)):
    return {
        "object": "list",
        "data": [
            {"id": Config.MODEL_CHAT,   "object": "model"},
            {"id": Config.MODEL_CODE,   "object": "model"},
            {"id": Config.MODEL_AGENT,  "object": "model"},
        ],
    }

@app.get("/health")
async def health():
    idx = get_index()
    return {"status": "ok", "chunks": idx.total_chunks if idx else 0}

@app.post("/smartorch/index")
async def index(body: dict, _=Depends(verify_key)):
    from ..core.indexer import build_index
    root   = body.get("root", ".")
    chunks = await asyncio.to_thread(build_index, root)
    return {"status": "ok", "chunks": chunks}

# run.py lo llama así:
# import uvicorn
# uvicorn.run("smartorch.api.server:app", host=Config.HOST, port=Config.PORT, reload=False)
```

### RAG con embeddings semánticos

```python
# smartorch/rag/embedder.py  ← ARCHIVO NUEVO

from sentence_transformers import SentenceTransformer
import numpy as np

_model = None

def get_model():
    global _model
    if _model is None:
        # all-MiniLM-L6-v2: 80MB, rápido en CPU, muy bueno para código
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def embed(texts: list[str]) -> np.ndarray:
    return get_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)

def embed_one(text: str) -> np.ndarray:
    return embed([text])[0]
```

```python
# smartorch/rag/store.py  ← ARCHIVO NUEVO

import chromadb
from pathlib import Path
from .embedder import embed

_client = None
_collection = None

def get_collection(persist_dir: str = ".smartorch_db"):
    global _client, _collection
    if _collection is None:
        Path(persist_dir).mkdir(exist_ok=True)
        _client = chromadb.PersistentClient(path=persist_dir)
        _collection = _client.get_or_create_collection(
            name="codebase",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection

def upsert_chunks(chunks: list[dict]):
    """chunks: [{"id": str, "text": str, "metadata": dict}]"""
    col = get_collection()
    if not chunks:
        return
    embeddings = embed([c["text"] for c in chunks]).tolist()
    col.upsert(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        embeddings=embeddings,
        metadatas=[c.get("metadata", {}) for c in chunks],
    )

def search(query: str, n_results: int = 5) -> list[dict]:
    col = get_collection()
    if col.count() == 0:
        return []
    from .embedder import embed_one
    q_embed = embed_one(query).tolist()
    results = col.query(query_embeddings=[q_embed], n_results=min(n_results, col.count()))
    out = []
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        out.append({"text": doc, "file": meta.get("file", ""), "score": 1 - dist})
    return out
```

```python
# smartorch/rag/chunker.py  ← ARCHIVO NUEVO

import os
from pathlib import Path

# Extensiones soportadas
EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
              ".java", ".c", ".cpp", ".h", ".cs", ".rb", ".php",
              ".md", ".yaml", ".yml", ".json", ".sh", ".ps1"}

def chunk_file(path: str, chunk_size: int = 50, overlap: int = 10) -> list[dict]:
    """Divide un archivo en chunks con overlap. Devuelve lista de dicts."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines  = text.splitlines()
    chunks = []
    step   = chunk_size - overlap

    for i in range(0, max(1, len(lines)), step):
        segment = lines[i : i + chunk_size]
        if not any(l.strip() for l in segment):
            continue
        chunk_text = "\n".join(segment)
        chunks.append({
            "id":       f"{path}::{i}",
            "text":     f"# {path} (líneas {i+1}-{i+len(segment)})\n{chunk_text}",
            "metadata": {"file": path, "start_line": i + 1},
        })

    return chunks

def index_directory(root: str) -> list[dict]:
    """Recorre un directorio y devuelve todos los chunks."""
    all_chunks = []
    skip = {"node_modules", ".git", "__pycache__", "venv", ".venv",
            "dist", "build", "out", ".smartorch_db"}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in EXTENSIONS:
                continue
            fpath = os.path.join(dirpath, fname)
            if os.path.getsize(fpath) > 500_000:  # skip >500KB
                continue
            all_chunks.extend(chunk_file(fpath))

    return all_chunks
```

### Sidebar real en VS Code (WebviewViewProvider)

```typescript
// src/sidebar-provider.ts  ← ARCHIVO NUEVO

import * as vscode from 'vscode';
import { SmartOrchClient, ChatMessage } from './smartorch-client';

export class SmartOrchSidebarProvider implements vscode.WebviewViewProvider {
  static readonly viewId = 'smartorch.chatView';
  private _view?: vscode.WebviewView;
  private _history: ChatMessage[] = [];

  constructor(
    private readonly _context: vscode.ExtensionContext,
    private readonly _client: SmartOrchClient
  ) {}

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ) {
    this._view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html   = this._getHtml();

    webviewView.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === 'chat')       await this._handleChat(msg.text, msg.includeFile);
      else if (msg.type === 'clear') { this._history = []; webviewView.webview.postMessage({ type: 'cleared' }); }
      else if (msg.type === 'ready') webviewView.webview.postMessage({ type: 'ready' });
    });
  }

  /** Llamado desde comandos (explicar, corregir, etc.) */
  async sendMessage(prompt: string, code?: string) {
    if (!this._view) {
      await vscode.commands.executeCommand('workbench.view.extension.smartorch');
    }
    const full = code ? `${prompt}\n\n\`\`\`\n${code}\n\`\`\`` : prompt;
    await this._handleChat(full, false);
  }

  private async _handleChat(text: string, includeFile: boolean) {
    let finalText = text;
    if (includeFile) {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        const doc  = editor.document;
        const code = doc.getText().slice(0, 4000);
        finalText  = `Archivo: ${doc.fileName}\n\`\`\`${doc.languageId}\n${code}\n\`\`\`\n\n${text}`;
      }
    }

    this._history.push({ role: 'user', content: finalText });
    this._view?.webview.postMessage({ type: 'thinking' });

    try {
      const resp = await this._client.chat(this._history);
      this._history.push({ role: 'assistant', content: resp.content });
      this._view?.webview.postMessage({
        type: 'response', content: resp.content,
        model: resp.model, taskType: resp.taskType,
      });
    } catch (e: any) {
      this._view?.webview.postMessage({
        type: 'error',
        content: `SmartOrch no responde. ¿Está corriendo?\n→ python run.py`,
      });
    }
  }

  private _getHtml(): string {
    return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root{--bg:#1e1e2e;--surface:#2a2a3e;--border:#383860;--accent:#89b4fa;--text:#cdd6f4;--dim:#6c7086;--user:#1e3a5f;--ai:#1a2a1a;--code:#181825;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;flex-direction:column;height:100vh;font-size:13px;}
  #msgs{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px;}
  .msg{padding:8px 12px;border-radius:8px;line-height:1.6;word-break:break-word;}
  .user{background:var(--user);border:1px solid #2a5080;border-radius:8px 8px 2px 8px;align-self:flex-end;max-width:88%;}
  .assistant{background:var(--ai);border:1px solid var(--border);}
  .meta{font-size:10px;color:var(--dim);margin-top:3px;}
  pre{background:var(--code);border:1px solid var(--border);border-radius:4px;padding:8px;overflow-x:auto;margin:4px 0;font-size:11px;}
  code{font-family:'Cascadia Code','Fira Code',monospace;}
  p{margin:3px 0;}
  .dots{display:flex;gap:3px;padding:8px;}
  .dots span{width:5px;height:5px;border-radius:50%;background:var(--dim);animation:b 1.2s infinite;}
  .dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}
  @keyframes b{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-4px)}}
  #bar{padding:6px;border-top:1px solid var(--border);background:var(--surface);}
  .opts{display:flex;gap:6px;margin-bottom:5px;align-items:center;}
  label{display:flex;gap:3px;align-items:center;color:var(--dim);font-size:11px;cursor:pointer;}
  .row{display:flex;gap:5px;}
  #inp{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:5px;padding:6px 8px;color:var(--text);font-size:12px;font-family:inherit;resize:none;min-height:32px;max-height:120px;outline:none;}
  #inp:focus{border-color:var(--accent);}
  btn,button{background:var(--accent);color:#1e1e2e;border:none;border-radius:5px;padding:6px 12px;cursor:pointer;font-weight:600;font-size:11px;}
  .sec{background:var(--surface);color:var(--text);border:1px solid var(--border);}
  ::-webkit-scrollbar{width:3px}::-webkit-scrollbar-thumb{background:var(--border)}
</style>
</head>
<body>
<div id="msgs">
  <div class="msg assistant">
    <p><strong>⚡ SmartOrch</strong></p>
    <p style="color:var(--dim)">RAG automático activo. <br>Activa "Incluir archivo" para contexto del editor.</p>
  </div>
</div>
<div id="bar">
  <div class="opts">
    <label><input type="checkbox" id="inc"> Incluir archivo</label>
    <button class="sec" style="padding:3px 8px;font-size:10px" onclick="clr()">Limpiar</button>
  </div>
  <div class="row">
    <textarea id="inp" placeholder="Pregunta... (Enter = enviar, Shift+Enter = nueva línea)" rows="1"></textarea>
    <button onclick="send()">▶</button>
  </div>
</div>
<script>
const vscode=acquireVsCodeApi(),msgs=document.getElementById('msgs'),inp=document.getElementById('inp');
function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function fmt(t){
  t=t.replace(/\`\`\`(\\w*)\n?([\\s\\S]*?)\`\`\`/g,(_,l,c)=>\`<pre><code>\${esc(c.trim())}</code></pre>\`);
  t=t.replace(/\`([^\`]+)\`/g,'<code>$1</code>');
  t=t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  return t.split(/\\n\\n+/).map(p=>\`<p>\${p.replace(/\\n/g,'<br>')}</p>\`).join('');
}
function add(role,html,meta=''){
  const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=html+(meta?\`<div class="meta">\${meta}</div>\`:'');
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;
}
function send(){
  const t=inp.value.trim();if(!t)return;
  add('user',\`<p>\${esc(t)}</p>\`);
  inp.value='';inp.style.height='auto';
  const th=document.createElement('div');th.className='msg assistant';th.id='th';
  th.innerHTML='<div class="dots"><span></span><span></span><span></span></div>';
  msgs.appendChild(th);msgs.scrollTop=msgs.scrollHeight;
  vscode.postMessage({type:'chat',text:t,includeFile:document.getElementById('inc').checked});
}
function clr(){vscode.postMessage({type:'clear'});}
window.addEventListener('message',e=>{
  const m=e.data;document.getElementById('th')?.remove();
  if(m.type==='response') add('assistant',fmt(m.content),\`\${m.model}·\${m.taskType||'chat'}\`);
  else if(m.type==='error') add('assistant',\`<p style="color:#f38ba8">\${esc(m.content)}</p>\`);
  else if(m.type==='cleared') msgs.innerHTML='<div class="msg assistant"><p>Historial limpiado.</p></div>';
});
inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
inp.addEventListener('input',()=>{inp.style.height='auto';inp.style.height=Math.min(inp.scrollHeight,120)+'px';});
</script>
</body></html>`;
  }
}
```

### Actualizar extension.ts para usar el sidebar

```typescript
// src/extension.ts  ← CAMBIO CLAVE: registrar sidebar provider

import { SmartOrchSidebarProvider } from './sidebar-provider';

// Dentro de activate():
const sidebarProvider = new SmartOrchSidebarProvider(context, client);
context.subscriptions.push(
  vscode.window.registerWebviewViewProvider(
    SmartOrchSidebarProvider.viewId,
    sidebarProvider
  )
);

// Los comandos ahora usan sidebarProvider.sendMessage():
vscode.commands.registerCommand('smartorch.explainCode', async () => {
  const code = getSelectedText();
  if (!code) return;
  sidebarProvider.sendMessage('Explícame este código paso a paso:', code);
});
```

---

## Comparativa de rutas

| | Forkar Continue | SmartOrch propio |
|---|---|---|
| Tiempo para tener algo funcional | 2-3 días | Ya funciona |
| UI final | Profesional (React) | Suficiente |
| Diff view / @mentions | Gratis (ya está) | Hay que codear |
| Control total | Menor | Total |
| Peso | ~200MB | ~5MB |
| Módulos cyberrange | Hay que agregar | Ya tiene base |

---

## Plan de acción (esta semana)

```
Día 1:  Instalar deps Python: pip install fastapi uvicorn sentence-transformers chromadb watchdog
        Migrar servidor a FastAPI
        
Día 2:  Agregar RAG semántico (embedder.py + store.py + chunker.py)
        Probar búsqueda semántica vs TF-IDF

Día 3:  Agregar sidebar-provider.ts a la extensión VS Code
        Compilar y probar F5

Día 4:  Decidir: ¿forkar Continue o continuar con el propio?
        Si forkar: clonar, quitar telemetría, apuntar a Ollama

Día 5:  Commit y push al repo
```

---

## Instalar dependencias ahora

```bash
# Servidor SmartOrch
pip install fastapi uvicorn sentence-transformers chromadb watchdog

# Bajar modelo de embeddings (para Ollama)
ollama pull nomic-embed-text

# Extensión VS Code
cd E:\SMARTORCH-VSCODE
npm install
npx tsc -p ./
# Abrir en VS Code → F5 para probar
```

---

*SmartOrch — código libre para desarrolladores libres.*
