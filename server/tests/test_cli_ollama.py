import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from smartorch import cli


class EnsureOllamaTests(unittest.TestCase):
    def run_ensure(self, up_sequence, exe="C:/Ollama/ollama.exe", answer="", ask=True):
        ups = iter(up_sequence)
        out = io.StringIO()
        with mock.patch.object(cli, "_ollama_up", side_effect=lambda: next(ups, up_sequence[-1])), \
                mock.patch.object(cli, "_find_ollama", return_value=exe), \
                mock.patch.object(cli.subprocess, "Popen") as popen, \
                mock.patch.object(cli.time, "sleep"), \
                mock.patch("builtins.input", return_value=answer), redirect_stdout(out):
            result = cli._ensure_ollama(ask=ask)
        return result, popen, out.getvalue()

    def test_does_nothing_when_ollama_is_already_up(self):
        result, popen, text = self.run_ensure([True])
        self.assertTrue(result)
        popen.assert_not_called()
        self.assertEqual(text, "")

    def test_starts_ollama_serve_and_waits_until_it_answers(self):
        result, popen, text = self.run_ensure([False, False, False, True], answer="")
        self.assertTrue(result)
        self.assertEqual(popen.call_args.args[0], ["C:/Ollama/ollama.exe", "serve"])
        self.assertIn("listo", text)

    def test_user_can_decline(self):
        result, popen, _ = self.run_ensure([False], answer="n")
        self.assertFalse(result)
        popen.assert_not_called()

    def test_reconnect_command_does_not_ask(self):
        with mock.patch("builtins.input", side_effect=AssertionError("no debia preguntar")):
            result, popen, _ = self.run_ensure([False, True], ask=False)
        self.assertTrue(result)
        popen.assert_called_once()

    def test_without_ollama_installed_it_points_to_the_download(self):
        result, popen, text = self.run_ensure([False], exe=None)
        self.assertFalse(result)
        popen.assert_not_called()
        self.assertIn("ollama.com/download", text)

    def test_gives_up_with_a_message_if_it_never_answers(self):
        result, popen, text = self.run_ensure([False], answer="s")
        self.assertFalse(result)
        popen.assert_called_once()
        self.assertIn("no respondió a tiempo", text)

    def test_probe_failure_never_blocks_the_user(self):
        with mock.patch.object(cli, "_request", side_effect=OSError("servidor caido")):
            self.assertTrue(cli._ollama_up())
        with mock.patch.object(cli, "_request", return_value={"running": False}):
            self.assertFalse(cli._ollama_up())


if __name__ == "__main__":
    unittest.main()
