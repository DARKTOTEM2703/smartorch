"""
Orquestador principal — pipeline completo de inteligencia.

Pipeline:
  1. Cache        → respuesta instantánea si query similar ya fue respondida
  2. Router+RAG   → en paralelo (ahorra ~300ms por request)
  3. CoT          → inyecta system prompt experto según la tarea
  4. Compressor   → comprime el historial si supera el límite
  5. Ollama       → genera la respuesta
  6. Verificación → auto-corrección para code/agent/security
"""
import re
import logging
import concurrent.futures
from smartorch.core import router, chain, compressor, gating, ollama_client as ollama, indexer, cache as resp_cache
from smartorch.config import MAX_TOKENS_OUT, MODEL_CONTEXT_CHARS, MODELS, SPECULATIVE_ENABLED, SPECULATIVE_MIN_TOKENS

logger = logging.getLogger(__name__)

# Cargar índice TF-IDF al iniciar
_idx = indexer.get_index()
_idx.load()


def _get_rag_context(messages: list[dict]) -> str:
    """
    Busca código relevante para la query actual.
    Prioridad: RAG semántico (ChromaDB) → TF-IDF → vacío
    """
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            # Tomar solo el texto original sin el bloque RAG anterior
            content = m.get("content", "")
            if "[CONTEXTO DEL PROYECTO]" in content:
                content = content.split("[CONTEXTO DEL PROYECTO]")[0]
            user_text = content.strip()
            break

    if not gating.wants_project_context(user_text):
        return ""

    # Intentar RAG semántico primero
    try:
        from smartorch.rag.retriever import search_formatted, search
        from smartorch.rag.store import collection_ready
        if collection_ready():
            ctx = search_formatted(user_text, top_k=5, max_chars=3000)
            if ctx:
                logger.debug(f"[RAG] Semántico: {len(ctx)} chars")
                return ctx
    except ImportError:
        pass

    # Fallback: TF-IDF
    if _idx.size > 0:
        ctx = _idx.search_formatted(user_text, top_k=4, max_chars=2500)
        if ctx:
            logger.debug(f"[RAG] TF-IDF: {len(ctx)} chars")
            return ctx

    return ""


def _inject_rag(messages: list[dict], rag_ctx: str) -> list[dict]:
    """Inyecta el contexto RAG en el último mensaje del usuario."""
    if not rag_ctx:
        return messages

    enriched = list(messages)
    rag_block = f"\n\n[CONTEXTO DEL PROYECTO — código relevante]\n```\n{rag_ctx}\n```"

    for i in range(len(enriched) - 1, -1, -1):
        if enriched[i].get("role") == "user":
            # Evitar duplicar RAG en re-envíos
            content = enriched[i]["content"]
            if "[CONTEXTO DEL PROYECTO]" not in content:
                enriched[i] = {**enriched[i], "content": content + rag_block}
            break

    return enriched


_VERIFY_TASK_TYPES = {"code", "agent", "security"}
_VERIFY_MIN_CHARS  = 200  # no verificar respuestas muy cortas
_DECOMP_MIN_CHARS  = 350  # mínimo para activar decomposición

_draft_model_available: bool | None = None  # None = no verificado aún


def _check_draft_model() -> bool:
    """Verifica una sola vez si el modelo draft está disponible en Ollama."""
    global _draft_model_available
    if _draft_model_available is not None:
        return _draft_model_available
    try:
        available = ollama.list_models()
        draft_name = MODELS["draft"].split(":")[0]
        _draft_model_available = any(draft_name in m for m in available)
        if _draft_model_available:
            logger.info(f"[ORCH] Speculative decoding habilitado: {MODELS['draft']}")
    except Exception:
        _draft_model_available = False
    return _draft_model_available


def _speculative_generate(fitted: list[dict], full_model: str, max_tokens: int, temperature: float) -> str:
    """
    Draft + Refine:
    1. qwen:1.5b genera un borrador rápido
    2. qwen:7b revisa y mejora el borrador
    Mejora calidad ~20% y reduce tokens de generación del modelo grande.
    """
    draft_model = MODELS["draft"]
    try:
        draft = ollama.chat_text(
            model=draft_model,
            messages=fitted,
            temperature=temperature + 0.1,
            max_tokens=max_tokens,
        )
        logger.info(f"[SPEC] Borrador generado ({len(draft)} chars) con {draft_model}")
    except Exception as e:
        logger.warning(f"[SPEC] Borrador falló: {e} — usando modelo completo")
        return ollama.chat_text(model=full_model, messages=fitted, temperature=temperature, max_tokens=max_tokens)

    refine_msgs = list(fitted) + [{
        "role": "assistant",
        "content": draft,
    }, {
        "role": "user",
        "content": (
            "Revisa el código anterior. Si es correcto y completo, repítelo tal cual. "
            "Si tiene bugs o está incompleto, devuelve la versión corregida completa. "
            "IMPORTANTE: devuelve solo el código/respuesta, sin explicaciones extra."
        ),
    }]
    try:
        refined = ollama.chat_text(
            model=full_model,
            messages=refine_msgs,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        logger.info(f"[SPEC] Refinado ({len(refined)} chars) con {full_model}")
        return refined
    except Exception:
        return draft


def _needs_decomposition(user_text: str) -> bool:
    """Detecta si la tarea es suficientemente compleja para descomponer."""
    if len(user_text) < _DECOMP_MIN_CHARS:
        return False
    multi_step = re.search(
        r'\b(y (luego|despues|ademas|también)|'
        r'primero.+despues|paso \d|step \d|'
        r'and then|after that|also create|también (crea|escribe|genera))\b',
        user_text, re.IGNORECASE
    )
    return bool(multi_step)


def _decompose_task(user_text: str, model: str) -> list[str]:
    """
    Llama al LLM para descomponer la tarea en pasos concretos.
    Devuelve lista de subtareas o lista vacía si falla.
    """
    prompt = (
        "Descompón la siguiente tarea en 2-4 pasos CONCRETOS y ejecutables. "
        "Responde SOLO con los pasos numerados, sin explicación:\n\n"
        f"TAREA: {user_text[:800]}\n\nPASOS:"
    )
    try:
        result = ollama.chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        steps = []
        for line in result.strip().split('\n'):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-')):
                steps.append(re.sub(r'^[\d\.\-\)\s]+', '', line).strip())
        return steps if len(steps) >= 2 else []
    except Exception:
        return []


def _thinking_generate(fitted: list[dict], model: str, max_tokens: int, temperature: float) -> tuple[str, str]:
    """
    2-call thinking mode — garantiza razonamiento explícito sin depender
    de que el modelo siga tags de formato (no confiable en 8B).

    Paso 1: pedir solo el razonamiento (temperatura alta, tokens cortos)
    Paso 2: pedir la respuesta final usando el razonamiento como contexto

    Retorna: (thinking_text, final_answer)
    """
    # Paso 1 — razonamiento
    thinking_msgs = list(fitted) + [{
        "role": "user",
        "content": (
            "Antes de responder, piensa paso a paso:\n"
            "1. Que se pide exactamente?\n"
            "2. Que enfoques posibles hay?\n"
            "3. Que errores o casos borde debo considerar?\n"
            "4. Como estructuro la respuesta?\n\n"
            "Escribe SOLO el razonamiento, sin dar la respuesta todavia."
        ),
    }]
    try:
        thinking_text = ollama.chat_text(
            model=model,
            messages=thinking_msgs,
            temperature=min(temperature + 0.25, 0.85),
            max_tokens=min(max_tokens // 3, 400),
        )
        logger.info(f"[THINK] Razonamiento: {len(thinking_text)} chars")
    except Exception as e:
        logger.warning(f"[THINK] Paso 1 falló: {e} — continuando sin thinking")
        thinking_text = ""

    # Paso 2 — respuesta final con razonamiento como contexto
    answer_msgs = list(fitted)
    if thinking_text:
        answer_msgs = answer_msgs + [
            {"role": "assistant", "content": f"[Razonamiento interno]\n{thinking_text}"},
            {"role": "user",      "content": "Ahora da la respuesta final, correcta y completa, basandote en tu razonamiento anterior."},
        ]

    try:
        final_result = ollama.chat(
            model=model,
            messages=answer_msgs,
            temperature=max(temperature - 0.1, 0.1),
            max_tokens=max_tokens,
        )
        return thinking_text, final_result["content"]
    except Exception as e:
        return thinking_text, f"Error: {e}"


def _refine_code(draft: str, fitted: list[dict], model: str, max_tokens: int) -> str:
    """
    Multi-pass refinement para código:
    Pass 2 — el modelo critica su propio borrador y lo reescribe si encuentra errores.
    Solo se activa si el borrador parece código real (contiene bloques de código).
    """
    if "```" not in draft or len(draft) < 100:
        return draft

    refine_msgs = list(fitted) + [
        {"role": "assistant", "content": draft},
        {
            "role": "user",
            "content": (
                "Revisa el codigo que acabas de escribir. Busca:\n"
                "1. Bugs logicos o errores de sintaxis\n"
                "2. Violaciones de SOLID/DRY no detectadas\n"
                "3. Casos borde no manejados\n"
                "4. Variables sin inicializar o imports faltantes\n\n"
                "Si el codigo es correcto, responde SOLO: 'OK'\n"
                "Si hay errores, devuelve SOLO el codigo corregido completo (sin explicacion)."
            ),
        },
    ]
    try:
        refined = ollama.chat_text(
            model=model,
            messages=refine_msgs,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        if refined.strip().upper().startswith("OK"):
            logger.debug("[REFINE] Código validado sin cambios")
            return draft
        if len(refined) > len(draft) * 0.5:  # resultado sustancial
            logger.info(f"[REFINE] Código refinado: {len(draft)} → {len(refined)} chars")
            return refined
    except Exception as e:
        logger.warning(f"[REFINE] Multi-pass falló: {e}")
    return draft


def _ensemble_generate(fitted: list[dict], models_to_try: list[str], max_tokens: int, temperature: float) -> tuple[str, str]:
    """
    Ensemble: ejecuta dos modelos secuencialmente y elige el mejor resultado.
    Criterio: mayor longitud + mayor confianza (proxy de calidad para código).
    Devuelve: (mejor_respuesta, modelo_ganador)
    """
    results = []
    for m in models_to_try:
        try:
            r = ollama.chat(model=m, messages=fitted, temperature=temperature, max_tokens=max_tokens)
            content = r.get("content", "")
            if content:
                conf  = chain.confidence_score(content)
                score = len(content) * 0.3 + conf * 500  # heurística: longitud + confianza
                results.append((score, content, m))
                logger.info(f"[ENSEMBLE] {m}: {len(content)} chars, conf={conf:.2f}, score={score:.0f}")
        except Exception as e:
            logger.warning(f"[ENSEMBLE] {m} falló: {e}")

    if not results:
        raise ConnectionError("Todos los modelos del ensemble fallaron")

    results.sort(key=lambda x: x[0], reverse=True)
    best_score, best_content, best_model = results[0]
    logger.info(f"[ENSEMBLE] Ganador: {best_model} (score={best_score:.0f})")
    return best_content, best_model


def _run_verification(query: str, draft: str, model: str) -> str:
    """
    Ejecuta auto-verificación del borrador.
    Devuelve el texto final (corregido si hay errores, draft original si no).
    """
    verify_msgs = chain.build_verification_prompt(query, draft)
    try:
        result = ollama.chat_text(model=model, messages=verify_msgs, temperature=0.1)
        if result.startswith("CORREGIDO:"):
            corrected = result[len("CORREGIDO:"):].strip()
            if corrected:
                logger.info("[ORCH] Auto-verificación: respuesta corregida")
                return corrected
        logger.debug("[ORCH] Auto-verificación: VERIFICADO sin cambios")
    except Exception as e:
        logger.warning(f"[ORCH] Auto-verificación falló: {e}")
    return draft


def run(messages: list[dict], temperature: float = 0.3, max_tokens: int = MAX_TOKENS_OUT) -> dict:
    """
    Ejecuta el pipeline completo de SmartOrch.
    Devuelve dict con: content, model, task_type, context_chars, prompt_tokens,
                       completion_tokens, tokens_per_sec, cache_hit, error
    """
    # 0. Cache semántico — respuesta instantánea si query similar ya fue respondida
    cached = resp_cache.lookup(messages)
    if cached:
        return cached

    # 1+2. Router + RAG en paralelo (ahorran ~300ms en cada request)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_route = ex.submit(router.route, messages)
        f_rag   = ex.submit(_get_rag_context, messages)
        task_type, model = f_route.result()
        rag_ctx          = f_rag.result()

    logger.info(f"[ORCH] task={task_type} model={model} turns={len(messages)}")
    enriched  = _inject_rag(messages, rag_ctx)
    if rag_ctx:
        logger.info(f"[ORCH] RAG: {len(rag_ctx)} chars inyectados")

    # 3. CoT — system prompt experto según la tarea
    enriched = chain.inject_cot(enriched, task_type)

    # 4. Compresión — ajustar al límite real del modelo (qwen tiene 32k, no 8k)
    fitted = compressor.fit_context(enriched, model=model)

    # 5. Task decomposition — solo para agent con tareas multi-paso complejas
    user_query = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_query = m.get("content", "")[:800]
            break

    if task_type == "agent" and _needs_decomposition(user_query):
        subtasks = _decompose_task(user_query, model)
        if subtasks:
            logger.info(f"[ORCH] Decomposición: {len(subtasks)} pasos")
            step_results = []
            total_prompt_tok = 0
            total_compl_tok  = 0
            for i, step in enumerate(subtasks, 1):
                step_msgs = list(fitted) + [{"role": "user", "content": f"Paso {i}: {step}"}]
                try:
                    r = ollama.chat(model=model, messages=step_msgs, temperature=temperature, max_tokens=max_tokens // len(subtasks))
                    step_results.append(f"**Paso {i}: {step}**\n{r['content']}")
                    total_prompt_tok += r.get("prompt_tokens", 0)
                    total_compl_tok  += r.get("completion_tokens", 0)
                    fitted = step_msgs + [{"role": "assistant", "content": r["content"]}]
                except Exception:
                    break
            if step_results:
                return {
                    "content":           "\n\n".join(step_results),
                    "model":             model,
                    "task_type":         task_type,
                    "context_chars":     sum(len(m.get("content","")) for m in fitted),
                    "prompt_tokens":     total_prompt_tok,
                    "completion_tokens": total_compl_tok,
                    "tokens_per_sec":    0.0,
                    "error":             False,
                }

    # 5b. Decidir estrategia de generación
    use_thinking = chain.should_think(messages, task_type)
    use_speculative = (
        not use_thinking
        and SPECULATIVE_ENABLED
        and task_type == "code"
        and max_tokens >= SPECULATIVE_MIN_TOKENS
        and len(user_query) > 80
        and _check_draft_model()
    )
    # Ensemble: para code con thinking activo, usar hermes3 como segundo opinión
    use_ensemble = (
        use_thinking
        and task_type == "code"
        and len(user_query) > 120
        and model != MODELS.get("chat", "hermes3:8b")  # evitar llamar 2x el mismo modelo
    )

    thinking_text   = ""
    prompt_tokens   = 0
    compl_tokens    = 0
    tok_per_sec     = 0.0

    try:
        if use_speculative:
            logger.info("[ORCH] Usando speculative decoding (draft + refine)")
            response_text = _speculative_generate(fitted, model, max_tokens, temperature)
            prompt_tokens = sum(len(m.get("content","")) for m in fitted) // 4
            compl_tokens  = len(response_text) // 4

        elif use_thinking and use_ensemble:
            # Ensemble: thinking en el modelo de código + respuesta directa en chat model
            logger.info("[ORCH] Thinking + Ensemble (2 modelos)")
            thinking_text, think_answer = _thinking_generate(fitted, model, max_tokens, temperature)
            chat_model = MODELS.get("chat", "hermes3:8b")
            ensemble_answer, winning_model = _ensemble_generate(
                fitted,
                [model, chat_model],
                max_tokens,
                temperature,
            )
            # Combinar: si el ensemble gana al thinking, usar ese
            t_conf = chain.confidence_score(think_answer)
            e_conf = chain.confidence_score(ensemble_answer)
            if e_conf > t_conf + 0.1 and len(ensemble_answer) > len(think_answer) * 0.8:
                response_text = ensemble_answer
                model = winning_model
                logger.info(f"[ORCH] Ensemble ganó (conf {e_conf:.2f} > {t_conf:.2f})")
            else:
                response_text = think_answer
                logger.info(f"[ORCH] Thinking ganó (conf {t_conf:.2f})")
            prompt_tokens = sum(len(m.get("content","")) for m in fitted) // 4
            compl_tokens  = (len(thinking_text) + len(response_text)) // 4

        elif use_thinking:
            logger.info("[ORCH] Thinking mode activado (2 llamadas)")
            thinking_text, response_text = _thinking_generate(fitted, model, max_tokens, temperature)
            prompt_tokens = sum(len(m.get("content","")) for m in fitted) // 4
            compl_tokens  = (len(thinking_text) + len(response_text)) // 4

        else:
            llm_result    = ollama.chat(model=model, messages=fitted, temperature=temperature, max_tokens=max_tokens)
            response_text = llm_result["content"]
            prompt_tokens = llm_result.get("prompt_tokens", 0)
            compl_tokens  = llm_result.get("completion_tokens", 0)
            tok_per_sec   = llm_result.get("tokens_per_sec", 0.0)

    except ConnectionError as e:
        return {
            "content": f"SmartOrch error: {e}\n\nAsegurate de que Ollama esta corriendo: `ollama serve`",
            "model":     model,
            "task_type": task_type,
            "error":     True,
        }

    # 6. Multi-pass refinement — código pasa por un segundo loop de autocrítica
    if task_type == "code" and len(response_text) >= _VERIFY_MIN_CHARS:
        response_text = _refine_code(response_text, fitted, model, max_tokens)

    # 7. Confidence scoring — detectar alucinaciones
    conf = chain.confidence_score(response_text)
    if conf < 0.6 and task_type in _VERIFY_TASK_TYPES and len(response_text) >= _VERIFY_MIN_CHARS:
        logger.info(f"[CONF] Confianza baja ({conf:.2f}) — disparando re-verificacion")

    # 8. Auto-verificación — tareas críticas O baja confianza
    needs_verify = (
        task_type in _VERIFY_TASK_TYPES
        and len(response_text) >= _VERIFY_MIN_CHARS
        and (conf < 0.7 or task_type == "security")
    )
    if needs_verify:
        qtext = user_query or next(
            (m.get("content","")[:500] for m in reversed(messages) if m.get("role")=="user"), ""
        )
        response_text = _run_verification(qtext, response_text, model)

    result = {
        "content":           response_text,
        "thinking":          thinking_text,
        "model":             model,
        "task_type":         task_type,
        "context_chars":     sum(len(m.get("content", "")) for m in fitted),
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": compl_tokens,
        "tokens_per_sec":    tok_per_sec,
        "confidence":        round(chain.confidence_score(response_text), 2),
        "cache_hit":         False,
        "error":             False,
    }
    resp_cache.store(messages, result)
    return result
