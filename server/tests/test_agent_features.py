import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from smartorch.agent import loop, web
from smartorch.core import datadir


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.received = []
        self.specs_seen = []

    def __call__(self, model, messages, specs=None, eff=None):
        self.received.append([dict(m) for m in messages])
        self.specs_seen.append([s["function"]["name"] for s in (specs or [])])
        return self.replies.pop(0) if self.replies else {"role": "assistant", "content": "fin"}


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def final(text="Listo"):
    return {"role": "assistant", "content": text}


class AgentFeatureTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-feat-")
        Path(self.root, "app.py").write_text("def hola():\n    return 'hola'\n", encoding="utf-8")
        audit_dir = tempfile.mkdtemp(prefix="so-feat-audit-")
        for p in (mock.patch.object(datadir, "DATA_DIR", audit_dir),
                  mock.patch.object(loop, "APPROVAL_TIMEOUT", 5),
                  mock.patch.object(loop, "_chat_json", return_value=""),  # los tests nunca llaman a Ollama
                  mock.patch.object(loop.analysis, "get_profile", side_effect=RuntimeError("sin analisis"))):
            p.start()
            self.addCleanup(p.stop)

    def play(self, replies, answers=(), user="revisa esto", timeout=None, **kwargs):
        """Corre el agente en un hilo. `answers`: valores con los que se responde, en orden, a
        cada aprobacion o pregunta pendiente (True/False para aprobaciones, texto para preguntas)."""
        fake = FakeModel(replies)
        events: list[dict] = []
        handled: set[str] = set()
        queue = list(answers)

        def worker():
            for ev in loop.run([{"role": "user", "content": user}], self.root, **kwargs):
                events.append(ev)

        patches = [mock.patch.object(loop, "_chat", fake)]
        if timeout is not None:
            patches.append(mock.patch.object(loop, "APPROVAL_TIMEOUT", timeout))
        for p in patches:
            p.start()
        try:
            th = threading.Thread(target=worker, daemon=True)
            th.start()
            deadline = time.time() + 40
            while th.is_alive() and time.time() < deadline:
                for ev in list(events):
                    if ev["type"] in ("approval_wait", "ask") and ev["id"] not in handled and queue:
                        loop.registry.decide(ev["id"], queue.pop(0))
                        handled.add(ev["id"])
                time.sleep(0.02)
            th.join(timeout=5)
            self.assertFalse(th.is_alive(), "el agente se quedo colgado")
        finally:
            for p in patches:
                p.stop()
        return events, fake

    def big_project(self):
        """El explorador solo se ofrece en proyectos de cierto tamaño."""
        for i in range(loop.EXPLORE_MIN_FILES):
            Path(self.root, f"modulo_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    @staticmethod
    def of(events, kind):
        return [e for e in events if e["type"] == kind]

    # ── Plan ────────────────────────────────────────────────────────────────

    def test_plan_mode_offers_only_investigation_tools_and_marks_the_plan(self):
        events, fake = self.play([call("read_file", path="app.py"), final("1. Cambiar hola()")], plan=True)
        offered = set(fake.specs_seen[0])
        self.assertTrue({"read_file", "list_files", "glob", "search_text", "ask_user", "todo_write"} <= offered)
        self.assertFalse(offered & {"write_file", "edit_file", "run_command", "run_tests"})
        self.assertIn("MODO PLAN", fake.received[0][0]["content"])
        final_event = self.of(events, "final")[0]
        self.assertTrue(final_event.get("plan"))
        self.assertTrue(self.of(events, "done")[0]["plan"])

    def test_plan_mode_refuses_edits_even_if_the_model_insists(self):
        events, _ = self.play([call("edit_file", path="app.py", old_text="'hola'", new_text="'x'"), final("ok")], plan=True)
        res = self.of(events, "tool_result")[0]
        self.assertFalse(res["ok"])
        self.assertIn("no disponible", res["output"])
        self.assertIn("hola", Path(self.root, "app.py").read_text(encoding="utf-8"))

    # ── Tareas, preguntas, memoria ──────────────────────────────────────────

    def test_todo_write_updates_and_emits_the_list(self):
        todos = [{"text": "Leer app.py", "done": True}, {"text": "Cambiar el saludo", "done": False}]
        events, _ = self.play([call("todo_write", todos=todos), final()])
        ev = self.of(events, "todo")[0]
        self.assertEqual([t["text"] for t in ev["todos"]], ["Leer app.py", "Cambiar el saludo"])
        self.assertEqual([t["done"] for t in ev["todos"]], [True, False])
        self.assertIn("2 tareas, 1 hechas", self.of(events, "tool_result")[0]["output"])

    def test_ask_user_waits_for_the_answer_and_feeds_it_back(self):
        events, fake = self.play([call("ask_user", question="¿Cuál archivo?", options=["app.py", "otro.py"]), final("ok")],
                                 answers=["app.py"], plan=True)
        ask = self.of(events, "ask")[0]
        self.assertEqual((ask["question"], ask["options"]), ("¿Cuál archivo?", ["app.py", "otro.py"]))
        self.assertIn("El usuario respondió: app.py", [m for m in fake.received[1] if m["role"] == "tool"][0]["content"])

    def test_ask_user_without_answer_lets_the_agent_continue(self):
        events, _ = self.play([call("ask_user", question="¿Seguro?"), final("sigo")], timeout=0.3, plan=True)
        self.assertIn("no respondió", self.of(events, "tool_result")[0]["output"])
        self.assertEqual(self.of(events, "final")[0]["content"], "sigo")

    def test_project_memory_file_is_added_to_the_system_prompt(self):
        Path(self.root, "SMARTORCH.md").write_text("Usa siempre type hints y nombres en español.\n", encoding="utf-8")
        _, fake = self.play([final("ok")], user="hola")
        system = fake.received[0][0]["content"]
        self.assertIn("Instrucciones del proyecto (SMARTORCH.md)", system)
        self.assertIn("type hints", system)

    def test_unknown_tool_error_lists_the_valid_ones(self):
        _, fake = self.play([call("borrar_todo"), final()])
        tool_msg = [m for m in fake.received[1] if m["role"] == "tool"][0]["content"]
        self.assertIn("Herramientas válidas", tool_msg)
        self.assertIn("read_file", tool_msg)

    # ── Explorador (subagente) ──────────────────────────────────────────────

    def test_explorer_runs_with_its_own_context_and_returns_a_summary(self):
        self.big_project()
        events, fake = self.play([
            call("explore", question="¿Dónde se define hola?"),   # agente principal
            call("read_file", path="app.py"),                      # explorador
            final("app.py:1 define hola()"),                       # explorador termina
            final("hola está en app.py"),                          # agente principal responde
        ])
        sub = [e for e in events if e.get("agent") == "explorer"]
        self.assertTrue(any(e["type"] == "tool_call" and e["name"] == "read_file" for e in sub))
        explore_result = [e for e in events if e["type"] == "tool_result" and e["name"] == "explore"][0]
        self.assertIn("app.py:1 define hola()", explore_result["output"])
        self.assertEqual([e["content"] for e in events if e["type"] == "final" and "agent" not in e], ["hola está en app.py"])
        # el explorador arranca con un contexto limpio (sistema + su pregunta), no con el del principal
        self.assertEqual([m["role"] for m in fake.received[1]], ["system", "user"])
        self.assertIn("explorador", fake.received[1][0]["content"].lower())
        # y solo puede leer
        self.assertFalse(set(fake.specs_seen[1]) & {"write_file", "edit_file", "run_command", "explore"})

    # ── Niveles de esfuerzo ─────────────────────────────────────────────────

    def test_effort_levels_change_tools_and_step_budget(self):
        self.big_project()
        _, rapid = self.play([final()], effort="rapido")
        _, normal = self.play([final()], effort="normal")
        _, maximum = self.play([final()], effort="maximo")
        self.assertNotIn("explore", rapid.specs_seen[0])
        self.assertIn("explore", normal.specs_seen[0])
        self.assertIn("explore", maximum.specs_seen[0])
        self.assertIn("todo_write", maximum.received[0][0]["content"])   # plan_first solo en maximo
        self.assertNotIn("Antes de actuar, escribe tu plan", normal.received[0][0]["content"])

        events, _ = self.play([call("list_files") for _ in range(30)], effort="rapido")
        self.assertEqual(self.of(events, "done")[0]["steps"], 4)
        events, _ = self.play([call("list_files") for _ in range(30)], effort="maximo")
        self.assertEqual(self.of(events, "done")[0]["steps"], 18)
        self.assertEqual(self.of(events, "start")[0]["effort"], "maximo")

    def test_unknown_effort_falls_back_to_normal(self):
        events, _ = self.play([final()], effort="inventado")
        self.assertEqual(self.of(events, "start")[0]["effort"], "normal")

    # ── Verificacion ────────────────────────────────────────────────────────

    def test_syntax_check_reports_a_broken_edit_and_a_good_one(self):
        events, _ = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="return 'hola'", new_text="return ((("), final()], answers=[True])
        self.assertIn("Error de sintaxis", self.of(events, "tool_result")[-1]["output"])
        Path(self.root, "app.py").write_text("def hola():\n    return 'hola'\n", encoding="utf-8")
        events, _ = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="'hola'", new_text="'adios'"), final()], answers=[True])
        self.assertIn("Sintaxis válida", self.of(events, "tool_result")[-1]["output"])

    def test_syntax_check_is_skipped_in_rapid_mode(self):
        events, _ = self.play([call("edit_file", path="app.py", old_text="'hola'", new_text="'x'"), final()],
                              answers=[True], effort="rapido")
        self.assertNotIn("Sintaxis", self.of(events, "tool_result")[0]["output"])

    def _project_with_failing_tests(self):
        Path(self.root, "calc.py").write_text("def divide(a, b):\n    return a * b\n", encoding="utf-8")
        Path(self.root, "tests").mkdir()
        Path(self.root, "tests", "__init__.py").write_text("", encoding="utf-8")
        Path(self.root, "tests", "test_calc.py").write_text(
            "import unittest\nfrom calc import divide\n\n\nclass T(unittest.TestCase):\n    def test_divide(self):\n        self.assertEqual(divide(6, 3), 2)\n",
            encoding="utf-8")

    def test_maximum_effort_runs_tests_itself_after_editing_and_repairs_failures(self):
        self._project_with_failing_tests()
        events, fake = self.play([
            call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"),   # arreglo equivocado
            final("hecho"),                                                          # el bucle corre los tests por su cuenta: fallan
            call("edit_file", path="calc.py", old_text="a - b", new_text="a / b"),   # el modelo corrige con el error a la vista
            final("arreglado"),                                                      # el bucle vuelve a correr los tests: pasan
        ], answers=[True, True, True, True], effort="maximo", user="arregla el bug de calc.py")
        self.assertEqual([e["content"] for e in self.of(events, "final")], ["arreglado"])
        runs = [e for e in self.of(events, "tool_result") if e["name"] == "run_tests"]
        self.assertEqual([r["ok"] for r in runs], [False, True])
        self.assertIn("TESTS FALLARON", runs[0]["output"])
        self.assertIn("TESTS OK", runs[1]["output"])
        self.assertIn("a / b", Path(self.root, "calc.py").read_text(encoding="utf-8"))
        # el resultado de los tests le llega al modelo como una herramienta (no como un regaño de usuario)
        tool_msgs = [m["content"] for m in fake.received[2] if m["role"] == "tool"]
        self.assertTrue(any("TESTS FALLARON" in t and "intento 1/3" in t and "edit_file" in t for t in tool_msgs))
        self.assertEqual(len(fake.received), 4)

    def test_normal_effort_does_not_force_tests(self):
        self._project_with_failing_tests()
        events, _ = self.play([call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"), final("hecho")],
                              answers=[True], effort="normal")
        self.assertEqual([e["content"] for e in self.of(events, "final")], ["hecho"])
        self.assertFalse([e for e in self.of(events, "tool_call") if e["name"] == "run_tests"])

    def test_repair_attempts_are_limited_and_the_failure_is_reported_honestly(self):
        self._project_with_failing_tests()
        script = [call("edit_file", path="calc.py", old_text="a * b", new_text="a - b")]
        script += [final(f"intento {i}") for i in range(8)]   # el modelo no logra arreglarlo
        events, _ = self.play(script, answers=[True] * 12, effort="maximo", user="arregla el bug de calc.py")
        self.assertLessEqual(self.of(events, "done")[0]["steps"], 54)  # 3 intentos de hasta 18 pasos
        final_text = self.of(events, "final")[-1]["content"]
        # ningun intento lo arreglo: se avisa con honestidad y el proyecto queda como estaba, no a medias
        self.assertTrue(final_text.startswith("⚠ Probé 2 enfoques"), final_text)
        self.assertIn("a * b", Path(self.root, "calc.py").read_text(encoding="utf-8"))

    def test_with_a_single_candidate_failed_edits_stay_and_are_reported(self):
        import dataclasses
        from smartorch.core import effort as effort_mod
        one = dataclasses.replace(effort_mod.EFFORTS["maximo"], candidates=1)
        self._project_with_failing_tests()
        with mock.patch.dict(effort_mod.EFFORTS, {"maximo": one}):
            events, _ = self.play([call("edit_file", path="calc.py", old_text="a * b", new_text="a - b")] +
                                  [final(f"intento {i}") for i in range(8)],
                                  answers=[True] * 12, effort="maximo", user="arregla el bug de calc.py")
        self.assertTrue(self.of(events, "final")[-1]["content"].startswith("⚠ Los tests siguen fallando."))
        self.assertEqual(self.of(events, "candidate"), [])

    def test_a_failed_attempt_is_undone_and_a_different_approach_can_succeed(self):
        self._project_with_failing_tests()
        events, fake = self.play([
            call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"),   # intento 1: equivocado
            final("hecho"), final("sigo"), final("no puedo"),                        # los tests fallan y se agotan las reparaciones
            call("edit_file", path="calc.py", old_text="a * b", new_text="a / b"),   # intento 2: sobre el original restaurado
            final("arreglado"),
        ], answers=[True] * 12, effort="maximo", user="arregla el bug de calc.py")
        cand = self.of(events, "candidate")
        self.assertEqual([(c["attempt"], c["of"]) for c in cand], [(2, 3)])
        self.assertEqual(cand[0]["restored"], 1)
        self.assertEqual(Path(self.root, "calc.py").read_text(encoding="utf-8"), "def divide(a, b):\n    return a / b\n")
        self.assertEqual(self.of(events, "final")[-1]["content"], "arreglado")
        retry_prompt = next(m["content"] for m in reversed(fake.received[4]) if m["role"] == "user")
        self.assertIn("[Intento anterior fallido]", retry_prompt)
        self.assertIn("DISTINTO", retry_prompt)
        self.assertTrue(self.of(events, "done")[0]["learned"])

    def test_rollback_also_removes_files_created_by_the_failed_attempt(self):
        self._project_with_failing_tests()
        events, _ = self.play([
            call("write_file", path="extra.py", content="X = 1\n"),
            call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"),
            final("hecho"), final("sigo"), final("no puedo"),
            final("rendido"), final("rendido"), final("rendido"), final("rendido"),
        ], answers=[True] * 14, effort="maximo", user="arregla el bug de calc.py")
        self.assertFalse(Path(self.root, "extra.py").exists())
        self.assertEqual(Path(self.root, "calc.py").read_text(encoding="utf-8"), "def divide(a, b):\n    return a * b\n")

    def test_verified_task_is_learned_with_the_failure_that_preceded_it(self):
        from smartorch.core import experience
        self._project_with_failing_tests()
        events, _ = self.play([
            call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"),
            final("hecho"),
            call("edit_file", path="calc.py", old_text="a - b", new_text="a / b"),
            final("arreglado"),
        ], answers=[True] * 4, effort="maximo", user="arregla el bug de calc.py")
        self.assertTrue(self.of(events, "done")[0]["learned"])
        saved = experience.listing(self.root)
        self.assertEqual((len(saved), saved[0]["kind"], saved[0]["files"]), (1, "lesson", "calc.py"))
        self.assertIn("TESTS FALLARON", saved[0]["lesson"])

    def test_unverified_or_failed_tasks_are_not_learned(self):
        from smartorch.core import experience
        self._project_with_failing_tests()
        events, _ = self.play([call("edit_file", path="calc.py", old_text="a * b", new_text="a - b"), final("hecho")],
                              answers=[True], effort="normal", user="arregla el bug de calc.py")  # sin tests: no se verifico
        self.assertFalse(self.of(events, "done")[0]["learned"])
        self.assertEqual(experience.listing(self.root), [])

    def test_similar_task_receives_the_remembered_experience(self):
        from smartorch.core import experience
        self._project_with_failing_tests()
        experience.record(self.root, "arregla el bug de division en calc.py", ["calc.py"], "cambié a / b")
        _, fake = self.play([final("ok")], user="corrige el bug de division en calc.py", effort="normal")
        first_user = fake.received[0][-1]["content"]
        self.assertIn("Experiencias previas", first_user)
        self.assertIn("calc.py", first_user)
        _, fake = self.play([final("ok")], user="explica calc.py")  # no es una tarea de modificar: no se inyecta
        self.assertNotIn("Experiencias previas", fake.received[0][-1]["content"])

    def test_run_tests_is_denied_in_readonly_and_plan(self):
        self._project_with_failing_tests()
        events, _ = self.play([call("run_tests"), final()], approval="readonly")
        self.assertIn("solo lectura", self.of(events, "tool_result")[0]["output"])

    # ── Web ─────────────────────────────────────────────────────────────────

    RESULTS = [{"title": "Docs de asyncio", "url": "https://docs.python.org/3/library/asyncio.html", "snippet": "Concurrencia."}]

    def test_web_tools_are_not_offered_unless_requested(self):
        _, fake = self.play([final()])
        self.assertFalse(set(fake.specs_seen[0]) & {"web_search", "web_fetch"})
        _, fake = self.play([final()], web=True)
        self.assertTrue({"web_search", "web_fetch"} <= set(fake.specs_seen[0]))

    def test_global_kill_switch_hides_web_tools(self):
        with mock.patch.dict(os.environ, {"SMARTORCH_WEB": "0"}):
            events, fake = self.play([final()], web=True)
        self.assertFalse(set(fake.specs_seen[0]) & {"web_search", "web_fetch"})
        self.assertFalse(self.of(events, "start")[0]["web"])

    def test_web_search_shows_the_query_and_waits_for_approval(self):
        with mock.patch.object(web, "search", return_value=self.RESULTS) as search:
            events, fake = self.play([call("web_search", query="asyncio gather ejemplo"), final("ok")], answers=[True], web=True)
        tc = self.of(events, "tool_call")[0]
        self.assertTrue(tc["needs_approval"] and tc["network"])
        self.assertIn("asyncio gather ejemplo", tc["preview"])
        search.assert_called_once()
        res = self.of(events, "tool_result")[0]
        self.assertIn("<contenido_web", res["output"])
        self.assertIn("no confiable", res["output"])
        self.assertIn("DATO NO CONFIABLE", fake.received[0][0]["content"])

    def test_rejected_web_search_never_touches_the_network(self):
        with mock.patch.object(web, "search", side_effect=AssertionError("no debia buscar")):
            events, _ = self.play([call("web_search", query="algo"), final()], answers=[False], web=True)
        self.assertIn("rechaz", self.of(events, "tool_result")[0]["output"])

    def test_web_fetch_blocks_internal_addresses_even_when_approved(self):
        events, _ = self.play([call("web_fetch", url="http://127.0.0.1:8080/health"), final()], answers=[True], web=True)
        res = self.of(events, "tool_result")[0]
        self.assertFalse(res["ok"])
        self.assertIn("interna", res["output"])

    def test_plan_mode_can_research_the_web_with_approval(self):
        with mock.patch.object(web, "search", return_value=self.RESULTS):
            events, _ = self.play([call("web_search", query="algo"), final("1. plan")], answers=[True], web=True, plan=True)
        self.assertTrue(self.of(events, "tool_result")[0]["ok"])

    # ── Robustez frente a modelos chicos ────────────────────────────────────

    def test_explorer_is_not_offered_in_small_projects(self):
        _, fake = self.play([final()], effort="normal")
        self.assertNotIn("explore", fake.specs_seen[0])

    def test_identical_repeated_calls_are_cut_off_with_a_hint(self):
        events, fake = self.play([call("glob", pattern="*.xyz") for _ in range(5)] + [final("ya basta")])
        results = self.of(events, "tool_result")
        self.assertTrue(results[0]["ok"] and results[1]["ok"])
        self.assertFalse(results[2]["ok"])
        self.assertIn("Cambia de estrategia", results[2]["output"])
        self.assertEqual(self.of(events, "final")[0]["content"], "ya basta")

    def test_an_empty_answer_gets_one_nudge(self):
        events, fake = self.play([final(""), final("ahora sí")])
        self.assertEqual([e["content"] for e in self.of(events, "final")], ["ahora sí"])
        self.assertIn("No escribiste ninguna respuesta", fake.received[1][-1]["content"])

    def test_system_prompt_teaches_english_identifiers_and_test_first_debugging(self):
        _, fake = self.play([final("ok")])
        system = fake.received[0][0]["content"]
        self.assertIn("suelen estar en inglés", system)
        self.assertIn("ejecuta run_tests para ver el error real", system)

    # ── replace_in_files y mapa del proyecto ────────────────────────────────

    def _shop_files(self):
        Path(self.root, "pricing.py").write_text("def calc_total(items):\n    return sum(items)\n", encoding="utf-8")
        Path(self.root, "cart.py").write_text("from pricing import calc_total\nx = calc_total([1])\n", encoding="utf-8")

    def test_replace_in_files_asks_for_approval_and_shows_every_affected_file(self):
        self._shop_files()
        events, _ = self.play([call("replace_in_files", old="calc_total", new="compute_total"), final("renombrado")],
                              answers=[True], user="renombra calc_total a compute_total")
        tc = self.of(events, "tool_call")[0]
        self.assertTrue(tc["needs_approval"])
        self.assertIn("2 archivos", tc["preview"])
        self.assertIn("compute_total", Path(self.root, "cart.py").read_text(encoding="utf-8"))
        self.assertIn("compute_total", Path(self.root, "pricing.py").read_text(encoding="utf-8"))

    def test_replace_in_files_counts_as_an_edit_so_the_model_is_not_nudged(self):
        self._shop_files()
        events, fake = self.play([call("replace_in_files", old="calc_total", new="compute_total"), final("listo")],
                                 answers=[True], user="renombra calc_total a compute_total")
        self.assertEqual(len(fake.received), 2)
        self.assertEqual(self.of(events, "final")[0]["content"], "listo")

    def test_replace_in_files_follows_the_permission_modes(self):
        self._shop_files()
        events, _ = self.play([call("replace_in_files", old="calc_total", new="x"), final()], approval="readonly")
        self.assertIn("solo lectura", self.of(events, "tool_result")[0]["output"])
        events, _ = self.play([call("replace_in_files", old="calc_total", new="compute_total"), final("ok")],
                              approval="auto_edits", user="renombra calc_total")
        self.assertNotIn("approval_wait", [e["type"] for e in events])  # editar solo: sin preguntar
        self.assertIn("compute_total", Path(self.root, "cart.py").read_text(encoding="utf-8"))

    def test_first_message_carries_the_project_map(self):
        self._shop_files()
        from smartorch.core import analysis
        profile = analysis.analyze(self.root)
        with mock.patch.object(loop.analysis, "get_profile", return_value=profile):
            _, fake = self.play([final("ok")], user="¿qué hace calc_total?")
        first_user = fake.received[0][-1]["content"]
        self.assertIn("Mapa del proyecto", first_user)
        self.assertIn("calc_total", first_user)

    # ── Compactacion ────────────────────────────────────────────────────────

    def test_compact_truncates_old_tool_results_but_keeps_the_latest(self):
        convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "pide"}]
        for i in range(6):
            convo += [{"role": "assistant", "content": ""}, {"role": "tool", "content": f"R{i}" + "x" * 3000}]
        out, changed = loop.compact(convo, budget=9000)
        self.assertTrue(changed)
        tools = [m["content"] for m in out if m["role"] == "tool"]
        self.assertTrue(all(len(t) > 3000 for t in tools[-3:]))
        self.assertTrue(all("recortado" in t and len(t) < 400 for t in tools[:-3]))
        self.assertEqual(out[0]["content"], "s")
        self.assertEqual(out[1]["content"], "pide")

    def test_compact_drops_oldest_turns_when_still_too_big_and_never_orphans_a_tool_result(self):
        convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "pide"}]
        for i in range(20):
            convo += [{"role": "assistant", "content": "a" * 800, "tool_calls": []}, {"role": "tool", "content": "t" * 800}]
        out, changed = loop.compact(convo, budget=6000)
        self.assertTrue(changed)
        self.assertLessEqual(loop._size(out), 6000 + 400)
        self.assertEqual(out[0]["role"], "system")
        self.assertIn("omitieron", out[2]["content"])
        self.assertNotEqual(out[3]["role"], "tool")

    def test_compact_is_a_noop_when_within_budget(self):
        convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "hola"}]
        out, changed = loop.compact(convo, budget=1000)
        self.assertFalse(changed)
        self.assertEqual(out, convo)


if __name__ == "__main__":
    unittest.main()
