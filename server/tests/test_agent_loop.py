import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from smartorch.agent import loop
from smartorch.core import datadir


class FakeModel:
    """Sustituye a Ollama: responde con un guion y recuerda lo que recibio."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.received = []

    def __call__(self, model, messages, specs=None, eff=None):
        self.received.append([dict(m) for m in messages])
        if not self.replies:
            return {"role": "assistant", "content": "fin"}
        return self.replies.pop(0)


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def final(text="Listo"):
    return {"role": "assistant", "content": text}


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-agentloop-")
        Path(self.root, "app.py").write_text("def hola():\n    return 'hola'\n", encoding="utf-8")
        audit_dir = tempfile.mkdtemp(prefix="so-audit-")
        for p in (mock.patch.object(datadir, "DATA_DIR", audit_dir),
                  mock.patch.object(loop, "APPROVAL_TIMEOUT", 5),
                  mock.patch.object(loop.analysis, "get_profile", side_effect=RuntimeError("sin analisis"))):
            p.start()
            self.addCleanup(p.stop)
        self.audit_dir = audit_dir

    def play(self, replies, mode="ask", answers=(), max_steps=None, timeout=None, user="revisa esto"):
        """Corre el agente en un hilo y responde las aprobaciones pendientes con `answers`."""
        fake = FakeModel(replies)
        events: list[dict] = []
        answered: set[str] = set()
        queue = list(answers)

        def worker():
            for ev in loop.run([{"role": "user", "content": user}], self.root, approval=mode, max_steps=max_steps):
                events.append(ev)

        patches = [mock.patch.object(loop, "_chat", fake)]
        if timeout is not None:
            patches.append(mock.patch.object(loop, "APPROVAL_TIMEOUT", timeout))
        for p in patches:
            p.start()
        try:
            th = threading.Thread(target=worker, daemon=True)
            th.start()
            deadline = time.time() + 20
            while th.is_alive() and time.time() < deadline:
                for ev in list(events):
                    if ev["type"] == "approval_wait" and ev["id"] not in answered and queue:
                        loop.registry.decide(ev["id"], queue.pop(0))
                        answered.add(ev["id"])
                time.sleep(0.02)
            th.join(timeout=5)
            self.assertFalse(th.is_alive(), "el agente se quedo colgado")
        finally:
            for p in patches:
                p.stop()
        return events, fake

    @staticmethod
    def types(events):
        return [e["type"] for e in events]

    def read_app(self):
        return Path(self.root, "app.py").read_text(encoding="utf-8")

    def test_reading_is_automatic_and_feeds_the_model(self):
        events, fake = self.play([call("read_file", path="app.py"), final("hace hola")])
        tc = next(e for e in events if e["type"] == "tool_call")
        self.assertEqual((tc["name"], tc["needs_approval"]), ("read_file", False))
        res = next(e for e in events if e["type"] == "tool_result")
        self.assertTrue(res["ok"])
        self.assertIn("1: def hola():", res["output"])
        self.assertNotIn("approval_wait", self.types(events))
        self.assertEqual(events[-2]["type"], "final")
        tool_msg = [m for m in fake.received[1] if m["role"] == "tool"][0]
        self.assertIn("def hola()", tool_msg["content"])

    def test_edit_waits_for_approval_then_applies_and_is_audited(self):
        events, _ = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="return 'hola'", new_text="return 'adios'"), final()],
                              answers=[True])
        tc = next(e for e in events if e["type"] == "tool_call" and e["name"] == "edit_file")
        self.assertTrue(tc["needs_approval"])
        self.assertIn("-    return 'hola'", tc["preview"])
        self.assertIn("approval_wait", self.types(events))
        self.assertIn("adios", self.read_app())
        with open(os.path.join(self.audit_dir, "agent-audit.log"), encoding="utf-8") as f:
            self.assertIn("edit_file", f.read())

    def test_rejected_edit_changes_nothing_and_model_is_told(self):
        events, fake = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="'hola'", new_text="'x'"), final()], answers=[False])
        self.assertIn("hola", self.read_app())
        self.assertNotIn("'x'", self.read_app())
        res = [e for e in events if e["type"] == "tool_result"][-1]
        self.assertFalse(res["ok"])
        self.assertIn("rechaz", res["output"])
        self.assertIn("rechaz", [m for m in fake.received[2] if m["role"] == "tool"][-1]["content"])

    def test_unanswered_approval_times_out_and_cancels(self):
        events, _ = self.play([call("write_file", path="nuevo.txt", content="x"), final()], timeout=0.3)
        self.assertFalse(Path(self.root, "nuevo.txt").exists())
        res = next(e for e in events if e["type"] == "tool_result")
        self.assertIn("Sin respuesta", res["output"])

    def test_readonly_mode_denies_every_mutation(self):
        events, _ = self.play([call("write_file", path="a.txt", content="x"),
                               call("run_command", command="echo hola"), final()], mode="readonly")
        self.assertFalse(Path(self.root, "a.txt").exists())
        self.assertNotIn("approval_wait", self.types(events))
        outputs = [e["output"] for e in events if e["type"] == "tool_result"]
        self.assertTrue(outputs and all("solo lectura" in o for o in outputs))

    def test_auto_edits_applies_edits_but_still_asks_for_commands(self):
        events, _ = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="'hola'", new_text="'auto'"),
                               call("run_command", command="echo hola"), final()],
                              mode="auto_edits", answers=[False])
        self.assertIn("auto", self.read_app())
        self.assertEqual(self.types(events).count("approval_wait"), 1)
        waits = [e for e in events if e["type"] == "tool_call" and e["needs_approval"]]
        self.assertEqual([w["name"] for w in waits], ["run_command"])

    def test_blocked_command_never_asks_nor_runs(self):
        events, _ = self.play([call("run_command", command="rm -rf /"), final()], answers=[True])
        self.assertNotIn("approval_wait", self.types(events))
        res = next(e for e in events if e["type"] == "tool_result")
        self.assertFalse(res["ok"])
        self.assertIn("bloqueado", res["output"])

    def test_unknown_tool_is_reported_and_loop_continues(self):
        events, _ = self.play([call("formatear_disco"), final("sigo vivo")])
        res = next(e for e in events if e["type"] == "tool_result")
        self.assertFalse(res["ok"])
        self.assertEqual(events[-2]["content"], "sigo vivo")

    def test_path_escape_attempt_is_an_error_not_a_crash(self):
        events, _ = self.play([call("read_file", path="../../etc/passwd"), final()])
        res = next(e for e in events if e["type"] == "tool_result")
        self.assertFalse(res["ok"])
        self.assertIn("fuera del workspace", res["output"])

    def test_step_limit_stops_runaway_loops(self):
        events, _ = self.play([call("list_files") for _ in range(10)], max_steps=3)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertIn("límite", final_event["content"])
        self.assertEqual(events[-1]["steps"], 3)

    def test_text_format_tool_call_is_understood(self):
        reply = {"role": "assistant", "content": 'Voy a mirar. <tool_call>{"name": "list_files", "arguments": {}}</tool_call>'}
        events, _ = self.play([reply, final()])
        self.assertEqual(next(e for e in events if e["type"] == "tool_call")["name"], "list_files")
        self.assertIn("app.py", next(e for e in events if e["type"] == "tool_result")["output"])

    def test_invalid_workspaces_are_rejected_before_calling_the_model(self):
        for bad in (str(Path.home()), Path(self.root).anchor, os.path.join(self.root, "no-existe"), ""):
            with mock.patch.object(loop, "_chat", side_effect=AssertionError("no debia llamar al modelo")):
                events = list(loop.run([{"role": "user", "content": "x"}], bad))
            self.assertEqual([e["type"] for e in events], ["error"], bad)

    def test_named_file_is_preloaded_so_the_model_does_not_have_to_ask_for_it(self):
        events, fake = self.play([final("hace hola")], user="explica app.py")
        first_user = fake.received[0][-1]["content"]
        self.assertIn("ya están cargados", first_user)
        self.assertIn("def hola()", first_user)
        self.assertEqual(len(fake.received), 1)  # respondio directo: sin llamadas extra ni regaños
        self.assertEqual([e["content"] for e in events if e["type"] == "final"], ["hace hola"])

    def test_readme_is_preloaded_for_general_questions_but_not_for_code_tasks(self):
        Path(self.root, "README.md").write_text("# Proyecto\nHace cosas utiles.\n", encoding="utf-8")
        _, fake = self.play([final("ok")], user="explica este proyecto")
        self.assertIn("Hace cosas utiles", fake.received[0][-1]["content"])
        _, fake = self.play([final("ok")], user="arregla el bug de este proyecto")
        self.assertNotIn("Hace cosas utiles", fake.received[0][-1]["content"])

    def test_secret_files_are_never_preloaded(self):
        Path(self.root, ".env").write_text("TOKEN=supersecreto\n", encoding="utf-8")
        _, fake = self.play([final("ok")], user="explica .env")
        self.assertNotIn("supersecreto", fake.received[0][-1]["content"])

    def test_action_task_without_edits_gets_two_nudges_then_an_honest_warning(self):
        events, fake = self.play([final("listo, ya lo hice"), final("de verdad"), final("sigo sin editar")],
                                 user="agrega una función de despedida")
        nudge = fake.received[1][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("ejecútalo con una herramienta", nudge["content"])
        self.assertIn("append_file", nudge["content"])
        final_text = [e["content"] for e in events if e["type"] == "final"][0]
        self.assertTrue(final_text.startswith("⚠ No modifiqué ningún archivo."))
        self.assertEqual(len(fake.received), 3)  # dos empujones y se acepta la respuesta, con la advertencia

    def test_action_task_that_edits_is_not_nudged_or_warned(self):
        events, fake = self.play([call("read_file", path="app.py"), call("edit_file", path="app.py", old_text="'hola'", new_text="'adios'"), final("hecho")],
                                 answers=[True], user="cambia el saludo a adios")
        self.assertEqual(len(fake.received), 3)
        self.assertEqual([e["content"] for e in events if e["type"] == "final"], ["hecho"])

    def test_readonly_and_plan_never_push_the_model_to_edit(self):
        events, fake = self.play([final("no puedo editar")], mode="readonly", user="agrega una función")
        self.assertEqual(len(fake.received), 1)
        self.assertEqual([e["content"] for e in events if e["type"] == "final"], ["no puedo editar"])

    def test_already_read_file_does_not_trigger_anything_extra(self):
        events, fake = self.play([call("read_file", path="app.py"), final("listo")], user="explica app.py")
        self.assertEqual(len(fake.received), 2)

    def test_fixing_a_bug_never_edits_the_tests(self):
        Path(self.root, "tests").mkdir()
        Path(self.root, "tests", "test_app.py").write_text("assert 1\n", encoding="utf-8")
        events, _ = self.play([call("edit_file", path="tests/test_app.py", old_text="assert 1", new_text="assert 2"), final()],
                              answers=[True], user="los tests fallan, arréglalo")
        self.assertIn("assert 1", Path(self.root, "tests", "test_app.py").read_text(encoding="utf-8"))
        self.assertIn("especificación", [e for e in events if e["type"] == "tool_result"][0]["output"])
        self.assertNotIn("approval_wait", self.types(events))

    def test_writing_tests_is_allowed_when_asked(self):
        self.assertFalse(loop._protects_tests("agrega un test para hola"))
        self.assertTrue(loop._protects_tests("hay un bug, corrígelo"))

    def test_append_file_adds_code_without_old_text(self):
        events, _ = self.play([call("read_file", path="app.py"), call("append_file", path="app.py", content="def adios():\n    return 'adios'"),
                               final()], answers=[True], user="agrega una función adios")
        text = self.read_app()
        self.assertTrue(text.startswith("def hola():") and text.rstrip().endswith("return 'adios'"))
        self.assertIn("Sintaxis válida", [e for e in events if e["type"] == "tool_result"][-1]["output"])

    def test_tool_log_summarises_actions(self):
        events, _ = self.play([call("read_file", path="app.py"), final()])
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("read_file(app.py)", events[-1]["tool_log"][0])


if __name__ == "__main__":
    unittest.main()
