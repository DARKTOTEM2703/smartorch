"""SmartOrch — Configuración central"""
import os

# ── Ollama ────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Ruta al ejecutable de Ollama (para pull/list desde código si es necesario)
def _find_ollama_exe() -> str:
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA",""), "Programs","Ollama","ollama.exe"),
        r"D:\Ollama\ollama.exe",
        r"C:\Ollama\ollama.exe",
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "ollama"  # asumir en PATH

OLLAMA_EXE = os.environ.get("OLLAMA_EXE", _find_ollama_exe())

MODELS = {
    # Defaults: 7-8b, ~5-6GB VRAM total
    # Para más calidad (8GB VRAM): MODEL_CODE=qwen2.5-coder:14b MODEL_AGENT=phi4:14b
    # Para razonamiento (9GB VRAM): MODEL_AGENT=deepseek-r1:14b (thinking model)
    "code":      os.environ.get("MODEL_CODE",      "qwen2.5-coder:7b"),
    "complete":  os.environ.get("MODEL_COMPLETE",  "qwen2.5-coder:1.5b"),  # autocompletado: rapido y ligero
    "agent":     os.environ.get("MODEL_AGENT",     "hermes3:8b"),
    "security":  os.environ.get("MODEL_SECURITY",  "hermes3:8b"),
    "chat":      os.environ.get("MODEL_CHAT",       "hermes3:8b"),
    "compress":  os.environ.get("MODEL_COMPRESS",  "hermes3:8b"),
    "draft":     os.environ.get("MODEL_DRAFT",     "qwen2.5-coder:1.5b"),
}

# Context windows de modelos soportados
CONTEXT_WINDOWS_BY_MODEL: dict[str, int] = {
    "hermes3:8b":           8192,
    "qwen2.5-coder:7b":    32768,
    "qwen2.5-coder:14b":   32768,
    "qwen2.5-coder:32b":   32768,
    "qwen2.5-coder:1.5b":  32768,
    "phi4:14b":            16384,
    "deepseek-r1:14b":     32768,
    "qwen2.5:32b":         32768,
    "llama3.1:8b":          8192,
    "nomic-embed-text":     8192,
}

# Speculative decoding: usar modelo draft si está disponible
SPECULATIVE_ENABLED  = os.environ.get("SPECULATIVE", "true").lower() == "true"
SPECULATIVE_MIN_TOKENS = int(os.environ.get("SPECULATIVE_MIN_TOKENS", "300"))

# ── Servidor ──────────────────────────────────────────
HOST       = os.environ.get("SMARTORCH_HOST", "127.0.0.1")  # solo local; Docker/remoto lo cambian con SMARTORCH_HOST
PORT       = int(os.environ.get("SMARTORCH_PORT", "8080"))
API_KEY    = os.environ.get("SMARTORCH_API_KEY", "smartorch-local-key")
DEBUG      = os.environ.get("SMARTORCH_DEBUG", "false").lower() == "true"

# ── Límites de contexto ───────────────────────────────
# qwen2.5-coder:7b tiene 32k tokens — usamos ~20k chars (~5k tokens) para
# dejar margen a la respuesta. hermes3:8b tiene 8k — límite más conservador.
MAX_CONTEXT_CHARS  = int(os.environ.get("MAX_CONTEXT_CHARS",  "20000"))
MAX_TOKENS_OUT     = int(os.environ.get("MAX_TOKENS_OUT",     "3072"))
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD", "22000"))

# Contexto por modelo — respeta el límite real de cada uno
MODEL_CONTEXT_CHARS: dict[str, int] = {
    "hermes3:8b":          6000,   # 8k tokens → ~6k chars seguros
    "qwen2.5-coder:7b":   20000,   # 32k tokens → usamos ~20k chars
    "qwen2.5-coder:14b":  24000,
    "qwen2.5-coder:1.5b": 16000,
    "phi4:14b":           10000,
    "deepseek-r1:14b":    20000,
}

# ── Agente ────────────────────────────────────────────
AGENT_MAX_STEPS    = int(os.environ.get("AGENT_MAX_STEPS", "8"))
AGENT_WORK_DIR     = os.environ.get("AGENT_WORK_DIR", os.path.expanduser("~"))

CONTEXT_WINDOW_DEFAULT = 8192

def model_context_window(model_name: str) -> int:
    base = (model_name.split(":")[0] + ":" + model_name.split(":")[1]) if ":" in model_name else model_name
    return CONTEXT_WINDOWS_BY_MODEL.get(base, CONTEXT_WINDOW_DEFAULT)
