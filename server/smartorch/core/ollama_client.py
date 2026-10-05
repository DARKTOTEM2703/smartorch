"""Cliente Ollama — wrapper sync con soporte de streaming y conteo real de tokens"""
import json
import urllib.request
import urllib.error
from typing import Generator, Union
from smartorch.config import OLLAMA_URL, MAX_TOKENS_OUT


def chat(
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = MAX_TOKENS_OUT,
) -> dict:
    """
    Llamada sincrónica a /api/chat de Ollama.
    Devuelve:
      {
        "content":           str,
        "prompt_tokens":     int,   # tokens de entrada (real de Ollama)
        "completion_tokens": int,   # tokens de salida (real de Ollama)
        "tokens_per_sec":    float, # velocidad de generación
        "total_duration_ms": int,   # duración total en ms
      }
    """
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())

        content         = data.get("message", {}).get("content", "").strip()
        prompt_tokens   = data.get("prompt_eval_count", 0)
        compl_tokens    = data.get("eval_count", 0)
        eval_dur_ns     = data.get("eval_duration", 0)
        total_dur_ns    = data.get("total_duration", 0)
        tok_per_sec     = round(compl_tokens / (eval_dur_ns / 1e9), 1) if eval_dur_ns > 0 else 0.0

        return {
            "content":           content,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": compl_tokens,
            "tokens_per_sec":    tok_per_sec,
            "total_duration_ms": round(total_dur_ns / 1e6),
        }
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama no disponible en {OLLAMA_URL}: {e}")


def chat_text(
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = MAX_TOKENS_OUT,
) -> str:
    """Shorthand — devuelve solo el texto (para llamadas internas que no necesitan tokens)."""
    return chat(model, messages, temperature, max_tokens)["content"]


def chat_stream(
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = MAX_TOKENS_OUT,
) -> Generator[Union[str, dict], None, None]:
    """
    Stream de tokens desde Ollama /api/chat.
    Yields:
      - str  para cada token de texto
      - dict {"type":"token_stats", "prompt_tokens": int, "completion_tokens": int,
               "tokens_per_sec": float} como último evento al finalizar
    """
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw_line in resp:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if data.get("done"):
                        prompt_tokens = data.get("prompt_eval_count", 0)
                        compl_tokens  = data.get("eval_count", 0)
                        eval_dur_ns   = data.get("eval_duration", 0)
                        tok_per_sec   = round(compl_tokens / (eval_dur_ns / 1e9), 1) if eval_dur_ns > 0 else 0.0
                        yield {
                            "type":              "token_stats",
                            "prompt_tokens":     prompt_tokens,
                            "completion_tokens": compl_tokens,
                            "tokens_per_sec":    tok_per_sec,
                        }
                        break
                except json.JSONDecodeError:
                    continue
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama no disponible: {e}")


def generate(model: str, prompt: str, max_tokens: int = 128, temperature: float = 0.1,
             suffix: str | None = None, strip: bool = True, stop: list[str] | None = None) -> str:
    """
    Llamada sincrona a /api/generate (autocompletado).
    Con `suffix` el modelo rellena el hueco entre prompt y suffix (FIM) si lo soporta.
    strip=False conserva la indentacion exacta, necesaria para completar en linea.
    """
    body = {
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if suffix is not None:
        body["suffix"] = suffix
    if stop:
        body["options"]["stop"] = stop
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            text = json.loads(resp.read().decode()).get("response", "")
            return text.strip() if strip else text
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama no disponible en {OLLAMA_URL}: {e}")


def list_models() -> list[str]:
    """Lista modelos disponibles en Ollama vía API."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    # Fallback: llamar al ejecutable directamente
    try:
        import subprocess
        from smartorch.config import OLLAMA_EXE
        result = subprocess.run([OLLAMA_EXE, "list"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        return [line.split()[0] for line in lines if line.strip()]
    except Exception:
        return []
