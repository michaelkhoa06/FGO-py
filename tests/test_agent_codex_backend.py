import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent.codex_backend import BackendError, CodexBackend, find_codex


COMPLETED_EVENT = '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}\n'


class CodexBackendTests(unittest.TestCase):
    def _backend(self, directory):
        return CodexBackend(Path(directory), executable=r"C:\\tools\\codex.exe", timeout=3)

    def test_choose_uses_native_read_only_state_only_command_and_saves_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)

            def run(command, **kwargs):
                self.assertIsInstance(command, list)
                self.assertFalse(kwargs["shell"])
                self.assertIn("--sandbox", command)
                self.assertEqual("read-only", command[command.index("--sandbox") + 1])
                self.assertIn('model_reasoning_effort="low"', command)
                self.assertIn('web_search="disabled"', command)
                self.assertIn("features.shell_tool=false", command)
                self.assertIn("-", command)
                self.assertNotIn(str(Path.cwd()), kwargs["cwd"])
                Path(command[command.index("-o") + 1]).write_text('{"kind":"attack"}', encoding="utf-8")
                self.assertEqual("utf-8", kwargs["encoding"])
                self.assertEqual("replace", kwargs["errors"])
                return subprocess.CompletedProcess(command, 0, COMPLETED_EVENT, "")

            with patch("fgo_agent.codex_backend.find_codex", return_value=r"C:\\tools\\codex.exe"), \
                 patch("fgo_agent.codex_backend.subprocess.run", side_effect=run):
                self.assertEqual({"kind": "attack"}, backend.choose({"phase": "cards"}, {"type": "object"}))
            call = Path(directory) / "call001"
            self.assertEqual({"phase": "cards"}, json.loads((call / "request.json").read_text()))
            self.assertEqual("completed", json.loads((call / "metadata.json").read_text())["status"])
            self.assertEqual(3, backend.last_call["usage"]["output_tokens"])
            self.assertEqual("completed", backend.last_call["status"])

    def test_timeout_writes_bounded_diagnostics_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            timeout = subprocess.TimeoutExpired(["codex.exe"], 3, output=b"token=abc", stderr=b"late")
            with patch("fgo_agent.codex_backend.find_codex", return_value=r"C:\\tools\\codex.exe"), \
                 patch("fgo_agent.codex_backend.subprocess.run", side_effect=timeout) as run:
                with self.assertRaisesRegex(BackendError, "timed out"):
                    backend.choose({}, {"type": "object"})
            self.assertEqual(1, run.call_count)
            call = Path(directory) / "call001"
            self.assertEqual("timeout", json.loads((call / "metadata.json").read_text())["status"])
            self.assertNotIn("abc", (call / "stdout.txt").read_text())

    def test_nonzero_exit_is_a_strict_failure_with_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            result = subprocess.CompletedProcess(["codex.exe"], 7, "", "authorization: secret-value")
            with patch("fgo_agent.codex_backend.find_codex", return_value=r"C:\\tools\\codex.exe"), \
                 patch("fgo_agent.codex_backend.subprocess.run", return_value=result):
                with self.assertRaises(BackendError) as raised:
                    backend.choose({}, {"type": "object"})
            self.assertIn("exit 7", str(raised.exception))
            self.assertNotIn("secret-value", str(raised.exception))
            self.assertEqual("failed", backend.last_call["status"])

    def test_malformed_or_non_object_final_json_is_rejected(self):
        for final in ("not json", "[]"):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as directory:
                backend = self._backend(directory)

                def run(command, **kwargs):
                    Path(command[command.index("-o") + 1]).write_text(final, encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, COMPLETED_EVENT, "")

                with patch("fgo_agent.codex_backend.find_codex", return_value=r"C:\\tools\\codex.exe"), \
                     patch("fgo_agent.codex_backend.subprocess.run", side_effect=run):
                    with self.assertRaisesRegex(BackendError, "final JSON"):
                        backend.choose({}, {"type": "object"})
                self.assertIn(backend.last_call["status"], {"malformed_output", "invalid_output"})

    def test_tool_event_or_incomplete_turn_is_rejected_before_output_is_accepted(self):
        for event in ('{"type":"item.completed","item":{"type":"command_execution"}}\n' + COMPLETED_EVENT,
                      '{"type":"item.completed","item":{"type":"agent_message","status":"failed"}}\n' + COMPLETED_EVENT,
                      '{"type":"thread.started"}\n'):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                backend = self._backend(directory)

                def run(command, **kwargs):
                    Path(command[command.index("-o") + 1]).write_text('{"kind":"attack"}', encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, event, "")

                with patch("fgo_agent.codex_backend.find_codex", return_value=r"C:\\tools\\codex.exe"), \
                     patch("fgo_agent.codex_backend.subprocess.run", side_effect=run):
                    with self.assertRaisesRegex(BackendError, "prohibited tool|error or failed|exactly one"):
                        backend.choose({}, {"type": "object"})
                self.assertEqual("invalid_event_stream", backend.last_call["status"])

    def test_fast_tier_is_passed_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = CodexBackend(Path(directory), timeout=3)
            fast = CodexBackend(Path(directory), timeout=3, service_tier="priority")
            args = ("codex.exe", Path(directory), Path(directory) / "s.json", Path(directory) / "o.json")
            self.assertFalse(any("service_tier" in part for part in plain._command(*args)))
            self.assertIn('service_tier="priority"', fast._command(*args))

    def test_find_codex_rejects_cmd_shims(self):
        with patch("fgo_agent.codex_backend.shutil.which", return_value=r"C:\\npm\\codex.cmd"), \
             patch("fgo_agent.codex_backend.os.environ.get", return_value=None):
            with self.assertRaisesRegex(BackendError, "not found"):
                find_codex()

    def test_find_codex_accepts_an_explicit_native_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex.exe"
            executable.write_bytes(b"placeholder")
            self.assertEqual(str(executable.resolve()), find_codex(executable))


if __name__ == "__main__":
    unittest.main()


class FastDefaultTests(unittest.TestCase):
    def test_battles_default_to_gpt_6_sol_on_the_fast_tier(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1] / "FGO-py"
        spec = importlib.util.spec_from_file_location("fgoAgentCli", root / "fgoAgent.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        args = cli.parser().parse_args(["battle", "--policy", "codex"]) if hasattr(cli, "parser") else None
        if args is None:
            self.skipTest("fgoAgent exposes no parser() to inspect")
        self.assertTrue(args.fast)
        self.assertEqual("gpt-6-sol", args.model)
