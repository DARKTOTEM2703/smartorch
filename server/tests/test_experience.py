import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from smartorch.core import codegraph, datadir, experience, projectmap
from smartorch.core.watcher import WorkspaceWatcher


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-exp-")
        patcher = mock.patch.object(datadir, "DATA_DIR", tempfile.mkdtemp(prefix="so-expdata-"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recall_ranks_by_overlap_and_ignores_unrelated_tasks(self):
        experience.record(self.root, "agrega descuento por volumen al carrito", ["shop/cart.py"], "nueva función")
        experience.record(self.root, "configura el logging del servidor", ["server/log.py"], "")
        got = experience.recall(self.root, "añade un descuento por volumen en el carrito de compras")
        self.assertEqual([g["files"] for g in got], ["shop/cart.py"])
        self.assertEqual(experience.recall(self.root, "dibuja un logo"), [])

    def test_experiences_are_scoped_to_their_project(self):
        other = tempfile.mkdtemp(prefix="so-exp2-")
        experience.record(self.root, "agrega descuento por volumen al carrito", ["cart.py"])
        self.assertEqual(experience.recall(other, "agrega descuento por volumen al carrito"), [])

    def test_repeating_a_task_updates_instead_of_duplicating(self):
        a = experience.record(self.root, "agrega descuento al carrito", ["cart.py"], "v1")
        b = experience.record(self.root, "agrega descuento al carrito", ["cart.py"], "v2")
        self.assertEqual(a, b)
        self.assertEqual(len(experience.listing(self.root)), 1)
        self.assertEqual(experience.listing(self.root)[0]["summary"], "v2")

    def test_nothing_is_recorded_without_task_or_files(self):
        self.assertIsNone(experience.record(self.root, "", ["a.py"]))
        self.assertIsNone(experience.record(self.root, "algo", []))

    def test_forget_removes_one_or_all(self):
        i = experience.record(self.root, "tarea uno del carrito", ["a.py"])
        experience.record(self.root, "tarea dos del carrito", ["b.py"])
        self.assertEqual(experience.forget(self.root, i), 1)
        self.assertEqual(experience.forget(self.root), 1)
        self.assertEqual(experience.listing(self.root), [])

    def test_project_cap_keeps_the_most_used(self):
        with mock.patch.object(experience, "MAX_PER_PROJECT", 3):
            for n in range(5):
                experience.record(self.root, f"tarea numero{n} del carrito", [f"f{n}.py"])
        self.assertEqual(len(experience.listing(self.root)), 3)

    def test_render_formats_recipes_and_lessons(self):
        experience.record(self.root, "arregla la division en calc", ["calc.py"], "usé /", "falló con ZeroDivision")
        text = experience.render(experience.recall(self.root, "arregla la division en calc"))
        self.assertIn("Experiencias previas", text)
        self.assertIn("Lección: falló con ZeroDivision", text)


class WatcherKeepsKnowledgeFreshTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-watch-")
        Path(self.root, "a.py").write_text("def uno():\n    return 1\n", encoding="utf-8")
        patcher = mock.patch.object(datadir, "DATA_DIR", tempfile.mkdtemp(prefix="so-watchdata-"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.watcher = WorkspaceWatcher(self.root)

    def test_map_is_not_rebuilt_when_the_user_never_built_it(self):
        with mock.patch.object(projectmap, "build") as build:
            self.watcher._refresh_map()
        build.assert_not_called()

    def test_map_is_refreshed_when_it_exists_and_deferred_while_the_agent_works(self):
        projectmap.build(self.root, summarize=lambda p: "resumen")
        from smartorch.agent import loop
        with mock.patch.object(projectmap, "build") as build:
            loop._set_active(+1)
            try:
                self.watcher._refresh_map()
                build.assert_not_called()
                self.assertTrue(self.watcher._pending_map)
            finally:
                loop._set_active(-1)
            self.watcher._refresh_map()
            build.assert_called_once()
            self.assertFalse(self.watcher._pending_map)

    def _reindex_isolated(self):
        # el RAG semantico real cargaria el modelo de embeddings y escribiria en el ChromaDB del usuario
        with mock.patch("smartorch.core.watcher.get_index"), \
                mock.patch("smartorch.rag.chunker.index_directory", return_value=[]), \
                mock.patch("smartorch.rag.store.sync_root", return_value=0), \
                mock.patch.object(WorkspaceWatcher, "_refresh_map"):
            self.watcher._reindex()

    def test_reindex_updates_the_code_graph(self):
        self._reindex_isolated()
        self.assertTrue(codegraph.find(self.root, "uno"))
        Path(self.root, "a.py").write_text("def uno():\n    return 1\n\ndef dos():\n    return 2\n", encoding="utf-8")
        self._reindex_isolated()
        self.assertTrue(codegraph.find(self.root, "dos"))


if __name__ == "__main__":
    unittest.main()
