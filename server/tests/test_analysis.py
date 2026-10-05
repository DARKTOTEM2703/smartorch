import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartorch.core import analysis
from smartorch.rag.chunker import IgnoreRules, index_directory, is_indexable, skip_dir


def _write(root, rel, text):
    path = Path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-analysis-")
        _write(self.root, ".gitignore", "build/\n*.log\n")
        _write(self.root, "app/main.py", '"""Punto de entrada."""\n\nclass Servidor:\n    """Atiende peticiones."""\n    def arrancar(self): pass\n    def _privado(self): pass\n\ndef saludar():\n    pass\n\nif __name__ == "__main__":\n    saludar()\n')
        _write(self.root, "app/util.js", "export function sumar(a, b) { return a + b }\nexport const doble = (x) => x * 2\nexport class Caja {}\n")
        _write(self.root, "app/estilos.css", "body { color: red }\n")
        _write(self.root, "tests/test_main.py", 'def test_a(): pass\nif __name__ == "__main__":\n    pass\n')
        _write(self.root, "build/salida.py", "x = 1\n")
        _write(self.root, "debug.log", "ruido\n")
        _write(self.root, "pkg.egg-info/PKG-INFO.txt", "meta\n")
        _write(self.root, "requirements.txt", "fastapi>=0.1\nuvicorn\n# comentario\n")
        _write(self.root, "notas.md", "# Notas\nTODO: documentar el servidor\n")
        # subcarpeta con su propio .gitignore
        _write(self.root, "sub/.gitignore", "generado/\n")
        _write(self.root, "sub/real.py", "y = 2\n")
        _write(self.root, "sub/generado/copia.py", "y = 2\n")

    def test_ignore_rules_cover_root_nested_and_artifacts(self):
        rules = IgnoreRules(self.root)
        self.assertTrue(rules.ignored(Path(self.root, "build"), True))
        self.assertTrue(rules.ignored(Path(self.root, "debug.log")))
        self.assertTrue(rules.ignored(Path(self.root, "sub", "generado"), True))
        self.assertFalse(rules.ignored(Path(self.root, "sub", "real.py")))
        self.assertTrue(skip_dir("pkg.egg-info"))
        self.assertTrue(skip_dir("node_modules"))

    def test_indexable_rules(self):
        for name in ("a.py", "x.css", "p.vue", "q.sql", "Dockerfile", "notes.txt"):
            self.assertTrue(is_indexable(Path(name)), name)
        for name in ("package-lock.json", "app.min.js", "lib.map", "foto.png"):
            self.assertFalse(is_indexable(Path(name)), name)

    def test_chunks_cover_everything_not_ignored(self):
        files = {c["metadata"]["file"] for c in index_directory(self.root)}
        self.assertIn("app/main.py", files)
        self.assertIn("app/estilos.css", files)
        self.assertIn("sub/real.py", files)
        self.assertNotIn("build/salida.py", files)
        self.assertNotIn("sub/generado/copia.py", files)
        self.assertFalse(any("egg-info" in f for f in files))

    def test_profile_counts_symbols_entries_and_todos(self):
        p = analysis.analyze(self.root)
        by_path = {m["path"]: m for m in p["modules"]}
        main = by_path["app/main.py"]
        names = {s["name"] for s in main["symbols"]}
        self.assertEqual(names, {"Servidor", "saludar"})
        server = next(s for s in main["symbols"] if s["name"] == "Servidor")
        self.assertEqual(server["methods"], ["arrancar"])  # los privados no se listan
        self.assertEqual(main["summary"], "Punto de entrada.")
        js = {s["name"] for s in by_path["app/util.js"]["symbols"]}
        self.assertEqual(js, {"sumar", "doble", "Caja"})
        self.assertIn("app/main.py", p["entry_points"])
        self.assertNotIn("tests/test_main.py", p["entry_points"])  # un test no es punto de entrada
        self.assertEqual(p["tests"], 1)
        self.assertEqual(p["dependencies"]["Python (requirements)"], ["fastapi", "uvicorn"])
        self.assertTrue(any(t["tag"] == "TODO" and "documentar" in t["text"] for t in p["todos"]))
        self.assertNotIn("build/salida.py", by_path)

    def test_overview_and_report_mention_structure(self):
        p = analysis.analyze(self.root)
        text = analysis.overview(p)
        self.assertIn("app/main.py", text)
        self.assertIn("Servidor", text)
        report = analysis.report_markdown(p)
        self.assertIn("# Análisis de", report)
        self.assertIn("| Python |", report)

    def test_symbol_files_finds_definitions_named_in_query(self):
        p = analysis.analyze(self.root)
        self.assertEqual(analysis.symbol_files(p, "donde se define la clase Servidor?")[0], "app/main.py")
        self.assertEqual(analysis.symbol_files(p, "que hace sumar"), ["app/util.js"])
        self.assertEqual(analysis.symbol_files(p, "hola como estas"), [])

    def test_profile_is_cached_until_files_change(self):
        cache_dir = tempfile.mkdtemp(prefix="so-analysis-cache-")  # fuera del proyecto: no altera su huella
        with mock.patch.object(analysis, "_cache_file", lambda root: os.path.join(cache_dir, "profile.json")):
            first = analysis.get_profile(self.root, force=True)
            with mock.patch.object(analysis, "analyze", side_effect=AssertionError("no debia recalcular")):
                again = analysis.get_profile(self.root)
            self.assertEqual(first["generated"], again["generated"])


if __name__ == "__main__":
    unittest.main()
