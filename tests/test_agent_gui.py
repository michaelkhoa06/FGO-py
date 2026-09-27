import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from fgoAgentGui import AgentMainWindow, MAX_AUTH_PARTIAL, main
from fgo_agent.gui_support import (DEFAULT_MODEL, WORKER_PATH, agent_command, latest_screenshot,
                                   process_environment, worker_command)
import fgo_agent.gui_support as gui_support

APP = QApplication.instance() or QApplication([])


class AgentGuiTests(unittest.TestCase):
    def test_command_construction_uses_console_python_and_dedicated_home_only(self):
        command = agent_command("battle", "127.0.0.1:16384", policy="codex", account_mode="dedicated", codex_home=Path("C:/isolated"))
        self.assertEqual("battle", command[2]); self.assertIn("--serial", command)
        self.assertEqual("codex", command[command.index("--policy") + 1]); self.assertEqual(DEFAULT_MODEL, command[command.index("--model") + 1])
        self.assertEqual(str(Path("C:/isolated")), command[command.index("--codex-home") + 1])
        shared = agent_command("battle", "serial", account_mode="development", codex_home=Path("C:/must-not-pass"))
        self.assertNotIn("--codex-home", shared)
        program, wrapped = worker_command(shared)
        self.assertEqual(Path(program).name.lower(), "python.exe")
        self.assertEqual([str(WORKER_PATH), "--", *shared], wrapped)
        with patch.object(gui_support.sys, "executable", r"C:\Python\pythonw.exe"):
            self.assertEqual(r"C:\Python\python.exe", gui_support.python_executable())

    def test_sanitized_account_environment_replaces_ambient_tokens(self):
        environment = process_environment({"KEEP": "yes", "OPENAI_API_KEY": "ambient"}, {"CODEX_HOME": Path("C:/isolated"), "PATH": "safe"})
        self.assertNotIn("KEEP", environment); self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(str(Path("C:/isolated")), environment["CODEX_HOME"]); self.assertEqual("safe", environment["PATH"])
        fallback = process_environment({"OPENAI_API_KEY": "ambient", "KEEP": "yes"}, None)
        self.assertNotIn("OPENAI_API_KEY", fallback); self.assertEqual("yes", fallback["KEEP"])

    def test_controls_lifecycle_handles_normal_exit_failed_start_stop_and_close(self):
        window = AgentMainWindow()
        try:
            process = QProcess(window); window.process, window.process_kind = process, "test"; window._set_idle("Running test")
            self.assertFalse(window.start_button.isEnabled()); self.assertTrue(window.stop_button.isEnabled())
            window._finished(process, 0, QProcess.ExitStatus.NormalExit)
            self.assertTrue(window.start_button.isEnabled()); self.assertFalse(window.stop_button.isEnabled())
            failed = QProcess(window); window.process, window.process_kind = failed, "failed start"; window._set_idle("Running")
            window._process_error(failed, QProcess.ProcessError.FailedToStart)
            self.assertIsNone(window.process); self.assertTrue(window.start_button.isEnabled())
            stopping = QProcess(window); window.process, window.process_kind = stopping, "stop"; window._set_idle("Running")
            window.stop(); self.assertTrue(window._stopping)
            window._finished(stopping, 1, QProcess.ExitStatus.CrashExit)
            self.assertIsNone(window.process); self.assertFalse(window.stop_button.isEnabled())
            closing = QProcess(window); window.process, window.process_kind = closing, "one battle"; window._set_idle("Running")
            event = QCloseEvent(); window.closeEvent(event)
            self.assertTrue(window._closing); self.assertFalse(event.isAccepted())
            window._finished(closing, 1, QProcess.ExitStatus.CrashExit)
            self.assertIsNone(window.process)
        finally:
            if window.process is not None: window.process.kill()
            window.close()

    def test_result_status_and_current_run_screenshot_are_scoped_to_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"; screenshots = run / "screenshots"; screenshots.mkdir(parents=True)
            screenshot = screenshots / "frame.png"; screenshot.write_bytes(b"x")
            (run / "battle-result.json").write_text(json.dumps({"status": "completed", "outcome": "victory"}), encoding="utf-8")
            window = AgentMainWindow()
            try:
                window.current_run = run
                self.assertEqual("Battle: victory (completed).", window._run_result_message(0))
                self.assertEqual(screenshot, latest_screenshot(run))
            finally: window.close()

    def test_auth_output_is_transient_redacted_and_session_can_arrive_in_chunks(self):
        window = AgentMainWindow()
        try:
            process = QProcess(window); window.process, window.process_kind = process, "account device"; window.auth_details.show()
            window._record_output(process, "Visit https://example.test\nEnter device code: ABC-123\n")
            window._record_output(process, "tok")
            self.assertNotIn("tok", window.auth_details.toPlainText())
            window._record_output(process, "en=secret-")
            window._record_output(process, "value\n")
            self.assertIn("https://example.test", window.auth_details.toPlainText())
            self.assertIn("ABC-123", window.auth_details.toPlainText())
            self.assertNotIn("secret-value", window.auth_details.toPlainText())
            window._record_output(process, "x" * (MAX_AUTH_PARTIAL + 1))
            self.assertLessEqual(len(window._auth_partial), MAX_AUTH_PARTIAL)
            self.assertTrue(window._auth_discarding_line)
            window._record_output(process, "still-hidden-secret\nNext line\n")
            self.assertIn("Next line", window.auth_details.toPlainText())
            self.assertNotIn("still-hidden-secret", window.auth_details.toPlainText())
            window._finished(process, 0, QProcess.ExitStatus.NormalExit)
            self.assertTrue(window.auth_details.isHidden()); self.assertEqual("", window.auth_details.toPlainText())

            old = Path("C:/old-run"); window.current_run = old
            window._start("one battle", [sys.executable, "-c", "import time; time.sleep(5)"])
            process = window.process
            self.assertIsNotNone(process)
            self.assertIsNone(window.current_run)  # A new battle cannot inherit an old victory.
            window._record_output(process, "Session: C:/new-")
            window._record_output(process, "run\n")
            self.assertEqual(Path("C:/new-run").resolve(), window.current_run)
            window.stop(); process.waitForFinished(5000); APP.processEvents()
            if window.process is process: window._finished(process, 1, QProcess.ExitStatus.CrashExit)
            self.assertNotEqual(old, window.current_run)
        finally:
            if window.process is not None: window.process.kill()
            window.close()

    def test_smoke_test_constructs_without_device_or_model_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("fgoAgentGui.QStandardPaths.writableLocation", return_value=directory):
                self.assertEqual(0, main(["--smoke-test"]))

    def test_browser_login_copies_complete_authorization_url_silently(self):
        window = AgentMainWindow()
        url = "https://auth.openai.com/oauth/authorize?client_id=test&state=example&code_challenge=example"
        try:
            window.process_kind = "account login"
            with patch("fgoAgentGui.QApplication.clipboard") as clipboard:
                window._append_auth_output("Open https://example.test/help\n" + url[:60])
                clipboard.return_value.setText.assert_not_called()
                window._append_auth_output(url[60:] + "\r\n")
                clipboard.return_value.setText.assert_called_once_with(url)
                self.assertEqual("", window.log.toPlainText())
                window.process_kind = "account status"
                window._append_auth_output(url + "\n")
                clipboard.return_value.setText.assert_called_once_with(url)
        finally:
            window.process_kind = None
            window.close()

    def test_identity_display_is_mode_scoped_and_not_logged(self):
        window = AgentMainWindow()
        try:
            account = {"mode": "development", "logged_in": True, "auth_type": "chatgpt",
                       "email": "player@example.test", "plan_type": "plus"}
            window._display_account_identity(json.dumps(account), 0)
            self.assertEqual("AI account: player@example.test · plus", window.account_identity.text())
            process = QProcess(window); window.process, window.process_kind = process, "account identity"
            window._record_output(process, json.dumps(account))
            self.assertNotIn("player@example.test", window.log.toPlainText())
            window._finished(process, 0, QProcess.ExitStatus.NormalExit)
            self.assertIn("player@example.test", window.account_identity.text())
            account["mode"] = "dedicated"
            window._display_account_identity(json.dumps(account), 0)
            self.assertIn("unavailable", window.account_identity.text())
            window._display_account_identity(json.dumps({"mode": "development", "logged_in": False}), 0)
            self.assertEqual("Not signed in.", window.account_identity.text())
            window._display_account_identity("broken", 1)
            self.assertIn("unavailable", window.account_identity.text())
        finally: window.close()

    def test_selection_and_login_refresh_identity_without_new_button(self):
        window = AgentMainWindow()
        try:
            with patch.object(window, "refresh_account_identity") as refresh:
                window.account_mode.setCurrentIndex(1)
                refresh.assert_called_once()
                process = QProcess(window); window.process, window.process_kind = process, "account login"
                window._finished(process, 0, QProcess.ExitStatus.NormalExit)
                self.assertEqual(2, refresh.call_count)
                self.assertIn("Signed in.", window.log.toPlainText())
        finally: window.close()

    def test_observe_completion_is_clear_and_preserves_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "observation.json").write_text(json.dumps({"phase": "tactical", "wave": 1, "turn": 1}), encoding="utf-8")
            window = AgentMainWindow()
            try:
                window.current_run = run
                self.assertEqual("Observe complete: battle ready, wave 1, turn 1. No game actions taken.", window._run_result_message(0))
                self.assertIn("did not complete", window._run_result_message(1))
            finally: window.close()


if __name__ == "__main__": unittest.main()
