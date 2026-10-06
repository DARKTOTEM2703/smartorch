import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartorch.api import server
from smartorch.core import datadir, experience, workspaces

DUP = "def {n}(w):\n    total = w * w\n    total = total * 7\n    total = total + 0\n    return total\n"


class HealthApiTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-health-")
        Path(self.root, "a.py").write_text(DUP.format(n="uno"), encoding="utf-8")
        Path(self.root, "b.py").write_text(DUP.format(n="dos"), encoding="utf-8")
        for p in (mock.patch.object(datadir, "DATA_DIR", tempfile.mkdtemp(prefix="so-healthdata-")),
                  mock.patch.object(workspaces, "is_registered", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_health_reports_graph_duplicates_map_and_experience_counts(self):
        experience.record(self.root, "agrega algo al carrito", ["a.py"])
        h = asyncio.run(server.project_health(self.root))
        self.assertEqual(h["graph"]["files"], 2)
        self.assertEqual(len(h["smells"]["duplicates"]), 1)
        self.assertEqual(h["map"]["summarized_files"], 0)
        self.assertEqual(h["experiences"], 1)

    def test_experiences_can_be_listed_and_forgotten(self):
        i = experience.record(self.root, "agrega algo al carrito", ["a.py"])
        experience.record(self.root, "otra tarea distinta del carrito", ["b.py"])
        self.assertEqual(len(asyncio.run(server.experiences_list(self.root))["experiences"]), 2)
        self.assertEqual(asyncio.run(server.experiences_forget(self.root, i))["deleted"], 1)
        self.assertEqual(asyncio.run(server.experiences_forget(self.root))["deleted"], 1)

    def test_unregistered_workspaces_are_refused(self):
        with mock.patch.object(workspaces, "is_registered", return_value=False):
            with self.assertRaises(server.HTTPException) as ctx:
                asyncio.run(server.project_health(self.root))
        self.assertEqual(ctx.exception.status_code, 403)



class OllamaProbeTests(unittest.TestCase):
    def test_probe_reports_running_and_down_without_raising(self):
        import json
        import urllib.error
        import io

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Resp(json.dumps({"models": [{}, {}]}).encode())):
            up = asyncio.run(server.ollama_probe())
        self.assertEqual((up["running"], up["models"]), (True, 2))
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("WinError 10061")):
            down = asyncio.run(server.ollama_probe())
        self.assertEqual((down["running"], down["models"]), (False, 0))
        self.assertIn("http", down["url"])

    def test_connection_errors_are_short_and_actionable(self):
        import urllib.error
        from smartorch.agent import loop
        from smartorch.core import ollama_client
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("WinError 10061")):
            with self.assertRaises(ConnectionError) as ctx:
                loop._chat("m", [], [], loop.effort_mod.get("normal"))
        msg = str(ctx.exception)
        self.assertIn("Ollama no disponible", msg)
        self.assertIn("¿Está corriendo?", msg)
        self.assertNotIn("10061", msg)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            with self.assertRaises(ConnectionError) as ctx2:
                ollama_client.chat_text("m", [{"role": "user", "content": "hola"}])
        self.assertIn("¿Está corriendo?", str(ctx2.exception))


if __name__ == "__main__":
    unittest.main()
