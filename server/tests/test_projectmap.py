import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartorch.core import datadir, projectmap


class ProjectMapTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-map-")
        for rel, body in {
            "main.py": "def main():\n    pass\n",
            "pkg/calc.py": "def add(a, b):\n    return a + b\n",
            "pkg/util/text.py": "def slug(s):\n    return s.lower()\n",
        }.items():
            p = Path(self.root, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        patcher = mock.patch.object(datadir, "DATA_DIR", tempfile.mkdtemp(prefix="so-mapdata-"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prompts: list[str] = []

    def fake(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "resumen de " + prompt.splitlines()[0].split(": ", 1)[-1]

    def test_builds_files_dirs_and_project_summaries(self):
        stats = projectmap.build(self.root, summarize=self.fake)
        self.assertEqual((stats["files"], stats["summarized"], stats["reused"]), (3, 3, 0))
        data = projectmap.load(str(Path(self.root).resolve()))
        self.assertEqual(set(data["files"]), {"main.py", "pkg/calc.py", "pkg/util/text.py"})
        self.assertEqual(set(data["dirs"]), {"pkg", "pkg/util"})
        self.assertTrue(data["project"])

    def test_directory_summary_is_built_from_its_children_summaries(self):
        projectmap.build(self.root, summarize=self.fake)
        pkg_prompt = next(p for p in self.prompts if p.startswith("Carpeta: pkg\n"))
        self.assertIn("calc.py: resumen de pkg/calc.py", pkg_prompt)
        self.assertIn("util/ (carpeta)", pkg_prompt)  # las subcarpetas entran ya resumidas

    def test_second_build_reuses_everything_unchanged(self):
        projectmap.build(self.root, summarize=self.fake)
        self.prompts.clear()
        stats = projectmap.build(self.root, summarize=self.fake)
        self.assertEqual((stats["summarized"], stats["reused"]), (0, 3))
        self.assertEqual(self.prompts, [])  # ni archivos, ni carpetas, ni proyecto

    def test_only_the_changed_file_and_its_ancestors_are_resummarized(self):
        projectmap.build(self.root, summarize=self.fake)
        self.prompts.clear()
        Path(self.root, "pkg/util/text.py").write_text("def slug(s):\n    return s.strip()\n", encoding="utf-8")
        stats = projectmap.build(self.root, summarize=lambda p: self.fake(p) + " v2")
        self.assertEqual((stats["summarized"], stats["reused"]), (1, 2))
        kinds = sorted(p.split(":", 1)[0] for p in self.prompts)
        self.assertEqual(kinds, ["Archivo", "Carpeta", "Carpeta", "Proyecto"])  # util, pkg y la raiz

    def test_failures_do_not_abort_and_are_counted(self):
        def flaky(prompt):
            if "calc.py" in prompt.splitlines()[0]:
                raise ValueError("el modelo fallo")
            return self.fake(prompt)
        stats = projectmap.build(self.root, summarize=flaky)
        self.assertEqual((stats["summarized"], stats["failed"]), (2, 1))

    def test_connection_error_aborts_but_keeps_what_was_done(self):
        calls = {"n": 0}

        def dies(prompt):
            calls["n"] += 1
            if calls["n"] == 3:
                raise ConnectionError("ollama apagado")
            return self.fake(prompt)
        with self.assertRaises(ConnectionError):
            projectmap.build(self.root, summarize=dies)
        self.assertEqual(len(projectmap.load(str(Path(self.root).resolve()))["files"]), 2)
        projectmap.build(self.root, summarize=self.fake)  # continua desde lo guardado
        self.assertEqual(len(projectmap.load(str(Path(self.root).resolve()))["files"]), 3)

    def test_render_and_lookup_give_the_model_a_map_without_reading_files(self):
        projectmap.build(self.root, summarize=self.fake)
        text = projectmap.render(self.root)
        self.assertIn("pkg/", text)
        found = projectmap.lookup(self.root, "donde está calc")
        self.assertEqual(found[0][0], "pkg/calc.py")

    def test_render_is_empty_before_building(self):
        self.assertEqual(projectmap.render(self.root), "")

    def test_cancel_stops_the_build(self):
        import threading
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            projectmap.build(self.root, summarize=self.fake, cancel=stop)


if __name__ == "__main__":
    unittest.main()
