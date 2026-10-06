import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from smartorch import singleton

RUN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SingletonTests(unittest.TestCase):
    def test_only_a_real_smartorch_counts_as_the_running_server(self):
        ok = Resp(json.dumps({"status": "ok", "service": "SmartOrch", "version": "2.0.0"}).encode())
        with mock.patch("urllib.request.urlopen", return_value=ok):
            self.assertEqual(singleton.running_server()["service"], "SmartOrch")
        other = Resp(json.dumps({"status": "ok", "service": "OtraCosa"}).encode())
        with mock.patch("urllib.request.urlopen", return_value=other):
            self.assertIsNone(singleton.running_server())
        with mock.patch("urllib.request.urlopen", side_effect=OSError("nadie escucha")):
            self.assertIsNone(singleton.running_server())

    def test_register_workspace_posts_the_root_with_the_api_key(self):
        sent = {}

        def fake(req, timeout=0):
            sent["url"], sent["body"], sent["auth"] = req.full_url, json.loads(req.data), req.headers.get("Authorization")
            return Resp(json.dumps({"status": "ok", "tfidf_chunks": 7}).encode())
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            out = singleton.register_workspace("E:/proyecto")
        self.assertEqual(out["tfidf_chunks"], 7)
        self.assertTrue(sent["url"].endswith("/smartorch/index"))
        self.assertEqual(sent["body"], {"root": "E:/proyecto"})
        self.assertTrue(sent["auth"].startswith("Bearer "))

    def _run_main(self, argv):
        spec = importlib.util.spec_from_file_location("smartorch_run_py", RUN_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["run.py", *argv]), redirect_stdout(out), \
                mock.patch("smartorch.api.server.start", side_effect=AssertionError("no debia arrancar otro servidor")):
            mod.main()
        return out.getvalue()

    def test_run_py_reuses_the_running_server_and_registers_the_project(self):
        folder = tempfile.mkdtemp(prefix="so-single-")
        with mock.patch.object(singleton, "running_server", return_value={"service": "SmartOrch"}), \
                mock.patch.object(singleton, "register_workspace", return_value={"tfidf_chunks": 3}) as reg:
            text = self._run_main([folder])
        reg.assert_called_once_with(os.path.abspath(folder))
        self.assertIn("ya está corriendo", text)
        self.assertIn("Proyecto registrado", text)

    def test_run_py_without_a_folder_just_reuses_the_server(self):
        with mock.patch.object(singleton, "running_server", return_value={"service": "SmartOrch"}), \
                mock.patch.object(singleton, "register_workspace") as reg:
            text = self._run_main([])
        reg.assert_not_called()
        self.assertIn("reutilizo ese servidor", text)

    def test_a_registration_failure_is_reported_not_raised(self):
        with mock.patch.object(singleton, "running_server", return_value={"service": "SmartOrch"}), \
                mock.patch.object(singleton, "register_workspace", side_effect=OSError("timeout")):
            text = self._run_main([tempfile.mkdtemp(prefix="so-single-")])
        self.assertIn("No pude registrar", text)


if __name__ == "__main__":
    unittest.main()
