import json
import os
import tempfile
import unittest
from unittest import mock

from smartorch.core import datadir


class DataDirTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="so-home-")
        self.target = os.path.join(tempfile.mkdtemp(prefix="so-target-"), "datos")
        self.boot_dir = os.path.join(self.home, ".smartorch")
        os.makedirs(self.boot_dir)
        with open(os.path.join(self.boot_dir, "history.db"), "wb") as f:
            f.write(b"x" * 2048)
        os.makedirs(os.path.join(self.boot_dir, "rag"))
        with open(os.path.join(self.boot_dir, "rag", "chunk.bin"), "wb") as f:
            f.write(b"y" * 1024)
        self.patches = [
            mock.patch.object(datadir, "BOOTSTRAP_DIR", self.boot_dir),
            mock.patch.object(datadir, "BOOTSTRAP_FILE", os.path.join(self.boot_dir, "location.json")),
            mock.patch.object(datadir, "DATA_DIR", self.boot_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_info_lists_items_and_free_space(self):
        data = datadir.info()
        names = {i["name"]: i for i in data["items"]}
        self.assertTrue(names["history.db"]["exists"])
        self.assertFalse(names["index.json"]["exists"])
        self.assertIsNotNone(data["free_gb"])
        self.assertFalse(data["custom"])

    def test_set_data_dir_copies_and_writes_bootstrap(self):
        result = datadir.set_data_dir(self.target)
        self.assertTrue(result["changed"])
        self.assertEqual(sorted(result["copied"]), ["history.db", "rag"])
        self.assertTrue(os.path.isfile(os.path.join(self.target, "rag", "chunk.bin")))
        with open(os.path.join(self.boot_dir, "location.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["data_dir"], os.path.abspath(self.target))
        # lo anterior no se borra
        self.assertTrue(os.path.isfile(os.path.join(self.boot_dir, "history.db")))

    def test_no_move_only_updates_location(self):
        result = datadir.set_data_dir(self.target, move=False)
        self.assertEqual(result["copied"], [])
        self.assertFalse(os.path.exists(os.path.join(self.target, "history.db")))

    def test_same_dir_is_noop(self):
        self.assertFalse(datadir.set_data_dir(self.boot_dir)["changed"])

    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"SMARTORCH_DATA_DIR": self.target}):
            self.assertEqual(datadir.resolve(), os.path.abspath(self.target))

    def test_resolve_reads_bootstrap_and_reset(self):
        datadir.set_data_dir(self.target, move=False)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMARTORCH_DATA_DIR", None)
            self.assertEqual(datadir.resolve(), os.path.abspath(self.target))
            datadir.reset_data_dir()
            self.assertEqual(datadir.resolve(), datadir.BOOTSTRAP_DIR)


if __name__ == "__main__":
    unittest.main()
