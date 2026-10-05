import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="smartorch-test-")
os.environ["SMARTORCH_DATA_DIR"] = _tmp

from smartorch.core import history  # noqa: E402


class HistoryTests(unittest.TestCase):
    def test_save_turn_creates_conversation_with_title(self):
        history.save_turn("t1", "web", "Explícame los decoradores de Python", "Un decorador es...")
        conv = history.get_conversation("t1")
        self.assertEqual(conv["title"], "Explícame los decoradores de Python")
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant"])
        self.assertEqual(conv["messages"][0]["source"], "web")

    def test_conversation_is_shared_across_sources(self):
        history.save_turn("t2", "web", "hola", "hola!")
        history.save_turn("t2", "cli", "y en la terminal?", "también")
        conv = history.get_conversation("t2")
        self.assertEqual(conv["message_count"], 4)
        self.assertEqual({m["source"] for m in conv["messages"]}, {"web", "cli"})

    def test_list_and_search(self):
        history.save_turn("t3", "vscode", "pregunta sobre kubernetes", "respuesta")
        ids = [c["id"] for c in history.list_conversations(query="kubernetes")]
        self.assertIn("t3", ids)
        self.assertNotIn("t1", ids)

    def test_rename_delete_and_missing(self):
        history.save_turn("t4", "api", "x", "y")
        self.assertTrue(history.rename_conversation("t4", "Renombrada"))
        self.assertEqual(history.get_conversation("t4")["title"], "Renombrada")
        self.assertTrue(history.delete_conversation("t4"))
        self.assertIsNone(history.get_conversation("t4"))
        self.assertFalse(history.delete_conversation("t4"))

    def test_export_markdown(self):
        history.save_turn("t5", "web", "pregunta", "```py\nprint(1)\n```")
        md = history.export_markdown("t5")
        self.assertIn("# pregunta", md)
        self.assertIn("print(1)", md)
        self.assertIsNone(history.export_markdown("no-existe"))


if __name__ == "__main__":
    unittest.main()
