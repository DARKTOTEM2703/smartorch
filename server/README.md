# SmartOrch — Local AI Orchestrator

> Modelos pequeños. Contexto inteligente. Sin pagar. Sin corporaciones.

SmartOrch convierte modelos de lenguaje locales pequeños (7B-13B) en un asistente de IA poderoso comparable a Claude o GPT-4, usando técnicas de orquestación inteligente: RAG sobre tu código, Chain-of-Thought automático, y routing por tipo de tarea.

**100% local. 100% gratis. 100% tuyo.**

---

## ¿Qué hace SmartOrch diferente?

| Herramienta | Modelo | Costo | Privacidad | RAG código |
|-------------|--------|-------|------------|------------|
| Claude / GPT | Grande (remoto) | $$$  | ❌ tu código sale | ❌ |
| Ollama solo | Pequeño (local) | Gratis | ✅ | ❌ manual |
| **SmartOrch** | Pequeño (local) | **Gratis** | **✅ nunca sale** | **✅ automático** |

### Técnicas aplicadas automáticamente:
- **RAG (Retrieval-Augmented Generation)** — indexa tu proyecto, inyecta código relevante en cada pregunta
- **Chain-of-Thought** — fuerza razonamiento paso a paso en modelos pequeños
- **Task Router** — código → modelo code-specialist, análisis → modelo agent
- **Context Compressor** — gestiona la ventana de contexto inteligentemente
- **File Watcher** — reindexia automáticamente cuando guardas cambios

---

## Instalación rápida

### Requisitos
- Python 3.10+
- [Ollama](https://ollama.com) instalado con al menos un modelo

### Modelos recomendados
```bash
ollama pull hermes3:8b          # agente sin restricciones, razonamiento
ollama pull qwen2.5-coder:7b    # especialista en código, autocomplete
```

### Instalar SmartOrch
```bash
git clone https://github.com/tu-usuario/smartorch
cd smartorch
pip install -r requirements.txt    # solo stdlib, sin dependencias
python run.py                      # inicia todo automáticamente
```

---

## Uso

### Inicio automático (recomendado)
```bash
python run.py                         # auto-detecta workspace abierto en VS Code
python run.py Z:\mi_proyecto          # workspace explícito
python run.py --reindex Z:\proyecto   # fuerza reindexado
python run.py --no-watch              # sin monitoreo de cambios
```

### Indexar un proyecto manualmente
```bash
python index_project.py Z:\CYBERRANGE_V2
python index_project.py ~/mis_proyectos/webapp
```

### Web UI
Abre `http://localhost:8080` en tu browser — chat completo con historial.

---

## Integrar con VS Code (Continue extension)

En `~/.continue/config.yaml`:
```yaml
name: SmartOrch Config
version: 1.0.0

models:
  - name: SmartOrch (Auto-Router)
    provider: openai
    model: hermes3:8b
    apiBase: http://localhost:8080/v1
    apiKey: smartorch-local-key
    roles:
      - chat
      - agent

  - name: SmartOrch Code
    provider: openai
    model: qwen2.5-coder:7b
    apiBase: http://localhost:8080/v1
    apiKey: smartorch-local-key
    roles:
      - autocomplete
```

---

## Conectar desde otra máquina (red local / Tailscale)

```bash
# En la máquina con GPU (servidor)
python run.py Z:\mi_proyecto

# Desde laptop u otra máquina
curl http://192.168.1.14:8080/health
# o via Tailscale:
curl http://100.x.x.x:8080/health
```

Cualquier herramienta compatible con OpenAI API funciona apuntando a:
- **URL:** `http://<IP>:8080/v1`
- **API Key:** `smartorch-local-key` (o define `SMARTORCH_API_KEY`)

---

## API endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/` | Web UI (chat) |
| GET | `/health` | Estado del servidor |
| GET | `/v1/models` | Lista de modelos (formato OpenAI) |
| POST | `/v1/chat/completions` | Chat principal (formato OpenAI) |
| POST | `/v1/completions` | Completions legacy |
| GET | `/smartorch/status` | Estado interno + índice |
| POST | `/smartorch/index` | Indexar directorio via API |

---

## Variables de entorno

```env
OLLAMA_URL=http://localhost:11434
MODEL_CODE=qwen2.5-coder:7b
MODEL_AGENT=hermes3:8b
MODEL_CHAT=hermes3:8b
SMARTORCH_HOST=0.0.0.0
SMARTORCH_PORT=8080
SMARTORCH_API_KEY=smartorch-local-key
MAX_CONTEXT_CHARS=6000
AGENT_WORK_DIR=~
```

---

## Lenguajes soportados

Python, JavaScript, TypeScript, Go, Rust, Java, C/C++, C#, Ruby, PHP,
Kotlin, Swift, Shell/Bash, PowerShell, HTML, CSS/SCSS, Vue, Svelte,
Markdown, YAML, JSON, TOML y más.

---

## Filosofía

El desarrollo de software es arte y debe ser libre. Las herramientas de IA
no deberían estar monopolizadas por corporaciones ni costar suscripciones
mensuales. SmartOrch demuestra que modelos pequeños con orquestación
inteligente pueden rivalizar con modelos gigantes.

**Hecho por desarrolladores, para desarrolladores.**

---

## Contribuir

PR bienvenido. Issues bienvenidos. Forks bienvenidos.
Este proyecto es para la comunidad.

Licencia: MIT
