"""
Compressor — compactación progresiva de contexto como Claude Code.

Estrategia:
  1. Si el historial supera el umbral, resumir los turnos viejos con el LLM
  2. El resumen reemplaza esos turnos como mensaje de sistema
  3. Se mantienen los últimos N turnos intactos
  4. Si el resumen sigue siendo largo, truncar solo como último recurso

El compressor usa chat_text() (no generate()) para que el system prompt
del resumen se aplique correctamente al LLM.
"""
import logging
from smartorch.config import COMPRESS_THRESHOLD, MAX_CONTEXT_CHARS, MODEL_CONTEXT_CHARS, MODELS

logger = logging.getLogger(__name__)

# Cuántos turnos recientes conservar intactos (un turno = user + assistant)
_KEEP_TURNS = 3


def total_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in messages)


def _summarize(turns_to_compress: list[dict], model: str) -> str:
    """
    Llama al LLM para resumir turnos viejos.
    Usa chat() con system prompt para maximizar calidad del resumen.
    """
    from smartorch.core.ollama_client import chat_text

    history_lines = []
    for m in turns_to_compress:
        role = m.get("role", "")
        if role == "system":
            continue  # no resumir system prompts internos
        content = m.get("content", "")
        # Quitar bloques RAG para no inflar el resumen
        if "[CONTEXTO DEL PROYECTO]" in content:
            content = content.split("[CONTEXTO DEL PROYECTO]")[0].strip()
        if content:
            history_lines.append(f"{role.upper()}: {content[:600]}")

    if not history_lines:
        return ""

    history_text = "\n\n".join(history_lines)

    msgs = [
        {
            "role": "system",
            "content": (
                "Eres un compressor de contexto de conversación. "
                "Tu único trabajo es resumir de forma densa y precisa. "
                "Formato: párrafo continuo, máximo 180 palabras. "
                "Conserva: decisiones tomadas, código clave, variables importantes, "
                "errores encontrados, archivos mencionados. "
                "Descarta: saludos, confirmaciones triviales, repeticiones."
            ),
        },
        {
            "role": "user",
            "content": f"Resume esta conversación anterior:\n\n{history_text}\n\nRESUMEN:",
        },
    ]

    try:
        summary = chat_text(
            model=model,
            messages=msgs,
            temperature=0.1,
            max_tokens=300,
        )
        logger.info(f"[COMPRESS] Resumen generado: {len(summary)} chars (de {len(history_text)} chars)")
        return summary.strip()
    except Exception as e:
        logger.warning(f"[COMPRESS] Resumen falló: {e}")
        # Fallback: extracto denso sin LLM
        lines = [l for l in history_lines if len(l) > 20]
        return " | ".join(l[:120] for l in lines[:6])


def compress_history(messages: list[dict], model: str | None = None) -> list[dict]:
    """
    Compacta el historial si supera COMPRESS_THRESHOLD.
    Equivalente a lo que Claude Code llama 'context compaction'.
    """
    if total_chars(messages) <= COMPRESS_THRESHOLD:
        return messages

    compress_model = model or MODELS.get("compress", MODELS.get("chat", "hermes3:8b"))

    # Separar mensajes de sistema (CoT/Expert) de la conversación
    system_msgs = [m for m in messages if m.get("role") == "system"]
    conv_msgs   = [m for m in messages if m.get("role") != "system"]

    # Mantener los últimos _KEEP_TURNS * 2 mensajes intactos
    keep_n      = _KEEP_TURNS * 2
    keep_recent = conv_msgs[-keep_n:] if len(conv_msgs) > keep_n else conv_msgs
    to_compress = conv_msgs[:-keep_n]  if len(conv_msgs) > keep_n else []

    if not to_compress:
        # Nada que comprimir — truncar el más largo como último recurso
        return _hard_truncate(messages)

    summary_text = _summarize(to_compress, compress_model)

    if summary_text:
        summary_msg = {
            "role":    "system",
            "content": f"[COMPACTACIÓN — conversación anterior resumida]\n{summary_text}",
        }
        compacted = system_msgs + [summary_msg] + keep_recent
    else:
        compacted = system_msgs + keep_recent

    logger.info(
        f"[COMPRESS] {total_chars(messages)} → {total_chars(compacted)} chars "
        f"({len(to_compress)} msgs comprimidos en resumen)"
    )
    return compacted


def fit_context(messages: list[dict], max_chars: int | None = None, model: str | None = None) -> list[dict]:
    """
    Punto de entrada principal.
    Asegura que el contexto cabe en max_chars, usando compresión LLM primero
    y truncación solo como último recurso.

    Si no se pasa max_chars, usa el límite del modelo específico
    (qwen2.5-coder:7b tiene 32k → 20k chars; hermes3:8b → 6k chars).
    """
    if max_chars is None:
        if model:
            # Buscar por prefijo del modelo
            base = model.split(":")[0] + ":" + model.split(":")[1] if ":" in model else model
            max_chars = MODEL_CONTEXT_CHARS.get(base, MAX_CONTEXT_CHARS)
        else:
            max_chars = MAX_CONTEXT_CHARS

    # Paso 1: compresión por resumen LLM si supera el umbral
    messages = compress_history(messages)

    # Paso 2: si sigue siendo largo, truncar contenido de mensajes viejos
    if total_chars(messages) > max_chars:
        messages = _hard_truncate(messages, max_chars)

    return messages


def _hard_truncate(messages: list[dict], max_chars: int = MAX_CONTEXT_CHARS) -> list[dict]:
    """Truncación de último recurso — corta el mensaje más largo (no el último)."""
    messages = list(messages)
    while total_chars(messages) > max_chars and len(messages) > 2:
        # Truncar el mensaje más largo excepto el último
        longest_idx = max(
            range(len(messages) - 1),
            key=lambda i: len(messages[i].get("content", ""))
        )
        content = messages[longest_idx]["content"]
        cut = max(max_chars // 4, 200)
        messages[longest_idx] = {
            **messages[longest_idx],
            "content": content[:cut] + "\n[... compactado ...]",
        }
    return messages
