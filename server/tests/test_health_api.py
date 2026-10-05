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


if __name__ == "__main__":
    unittest.main()
