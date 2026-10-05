import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from smartorch.rag import store


def _fake_embed(texts):
    # Embeddings deterministas y baratos: un vector por palabra clave
    vecs = []
    for t in texts:
        low = t.lower()
        vecs.append([1.0 if "alpha" in low else 0.0, 1.0 if "beta" in low else 0.0, 0.1])
    return np.array(vecs, dtype="float32")


def _chunk(cid, text, root):
    return {"id": cid, "text": text, "metadata": {"file": cid, "root": root}}


class RagScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="so-rag-")
        patches = [
            mock.patch.object(store, "DB_DIR", __import__("pathlib").Path(self.tmp)),
            mock.patch.object(store, "_collection", None),
            mock.patch("smartorch.rag.embedder.embed", _fake_embed),
            mock.patch("smartorch.rag.embedder.embed_one", lambda q: _fake_embed([q])[0]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_search_is_limited_to_roots(self):
        store.upsert_chunks([
            _chunk("a1", "alpha module", "/proj/a"),
            _chunk("b1", "alpha module copy", "/proj/b"),
        ])
        files = {r["file"] for r in store.search("alpha", 5, roots=["/proj/a"])}
        self.assertEqual(files, {"a1"})
        both = {r["file"] for r in store.search("alpha", 5, roots=["/proj/a", "/proj/b"])}
        self.assertEqual(both, {"a1", "b1"})

    def test_empty_roots_return_nothing(self):
        store.upsert_chunks([_chunk("a1", "alpha", "/proj/a")])
        self.assertEqual(store.search("alpha", 5, roots=[]), [])

    def test_sync_root_removes_stale_without_touching_other_roots(self):
        store.upsert_chunks([
            _chunk("a1", "alpha one", "/proj/a"),
            _chunk("a2", "alpha two", "/proj/a"),
            _chunk("b1", "beta one", "/proj/b"),
        ])
        removed = store.sync_root("/proj/a", [_chunk("a1", "alpha one", "/proj/a")])
        self.assertEqual(removed, 1)
        self.assertEqual(store.root_chunk_count("/proj/a"), 1)
        self.assertEqual(store.root_chunk_count("/proj/b"), 1)

    def test_filename_match_boosts_ranking(self):
        from smartorch.rag import retriever
        pool = [
            {"text": "x", "file": "docs/README.md", "score": 0.46},
            {"text": "y", "file": "core/watcher.py", "score": 0.43},
        ]
        with mock.patch.object(retriever, "semantic_search", return_value=pool):
            top = retriever.search("donde esta el watcher del proyecto", top_k=2, roots=["/p"])
        self.assertEqual(top[0]["file"], "core/watcher.py")

    def test_purge_unscoped_drops_legacy_chunks(self):
        store.upsert_chunks([
            {"id": "old", "text": "alpha legacy", "metadata": {"file": "old.py"}},
            _chunk("new", "alpha current", "/proj/a"),
        ])
        self.assertEqual(store.purge_unscoped(), 1)
        self.assertEqual(store.chunk_count(), 1)


if __name__ == "__main__":
    unittest.main()
