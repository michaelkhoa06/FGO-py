"""English desktop UI for one observable FGO NA battle."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QProcess, QProcessEnvironment, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from fgo_agent.gui_support import (DEFAULT_MODEL, DEFAULT_OBJECTIVE, DEFAULT_REASONING, DEFAULT_SERIAL,
                                   OBJECTIVE_LABELS, ROOT,
                                   agent_command, latest_screenshot, process_environment, worker_command, python_executable)

MAX_LOG_BLOCKS = 500
MAX_JOB_OUTPUT = 64 * 1024
MAX_AUTH_PARTIAL = 8192


def _account_module():
    try:
        from fgo_agent import codex_account
        return codex_account
    except ImportError:
        return None


class AgentMainWindow(QMainWindow):
    """Owns one Job-Object worker at a time; conflicting account/device writes are blocked."""
    def __init__(self):
        super().__init__()
        self.process: QProcess | None = None
        self.process_kind: str | None = None
        self._job_output = ""
        self._auth_partial = ""
        self._auth_discarding_line = False
        self.current_run: Path | None = None
        self._stopping = self._closing = False
        self.setWindowTitle("FGO Agent — English")
        self.resize(760, 560)
        self._build_ui()
        self._set_idle("Ready. Start only after you have already entered a battle.")

    def _build_ui(self):
        root, layout = QWidget(self), QVBoxLayout()
        root.setLayout(layout)
        warning = QLabel("One battle only. Start after the battle is already open. No quest selection, repeat farming, or AP refill is available.")
        warning.setWordWrap(True); layout.addWidget(warning)
        connection, connection_form = QGroupBox("Device"), QFormLayout()
        connection.setLayout(connection_form)
        self.serial = QComboBox(); self.serial.setEditable(True); self.serial.addItem(DEFAULT_SERIAL)
        self.refresh_button = QPushButton("Refresh devices")
        row = QHBoxLayout(); row.addWidget(self.serial); row.addWidget(self.refresh_button)
        connection_form.addRow("ADB serial", row); layout.addWidget(connection)
        account, account_form = QGroupBox("Codex account"), QFormLayout()
        account.setLayout(account_form)
        self.account_mode = QComboBox()
        self.account_mode.addItem("Development", "development")
        self.account_mode.addItem("Other Account", "dedicated")
        self.login_button = QPushButton("Login")
        accounts_row = QHBoxLayout()
        accounts_row.addWidget(self.account_mode); accounts_row.addWidget(self.login_button)
        account_form.addRow("Account", accounts_row)
        self.account_identity = QLabel("Account not checked yet.")
        self.account_identity.setTextFormat(Qt.TextFormat.PlainText)
        self.account_identity.setWordWrap(True)
        self.account_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        account_form.addRow(self.account_identity)
        layout.addWidget(account)
        run, run_form = QGroupBox("One battle"), QFormLayout()
        run.setLayout(run_form)
        self.policy = QComboBox(); self.policy.addItem("AI — Codex", "codex"); self.policy.addItem("Baseline", "legacy")
        self.objective = QComboBox()
        for label, value in OBJECTIVE_LABELS:
            self.objective.addItem(label, value)
        self.objective.setCurrentIndex(max(0, self.objective.findData(DEFAULT_OBJECTIVE)))
        self.start_button, self.observe_button, self.stop_button = QPushButton("Start one battle"), QPushButton("Observe (read-only)"), QPushButton("Stop")
        buttons = QHBoxLayout()
        for button in (self.start_button, self.observe_button, self.stop_button): buttons.addWidget(button)
        run_form.addRow("Policy", self.policy); run_form.addRow("Objective", self.objective)
        run_form.addRow("AI model", QLabel(f"{DEFAULT_MODEL}, {DEFAULT_REASONING} reasoning")); run_form.addRow(buttons)
        layout.addWidget(run)
        self.status, self.log = QLabel(), QPlainTextEdit(); self.log.setReadOnly(True); self.log.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.auth_details = QPlainTextEdit(); self.auth_details.setReadOnly(True); self.auth_details.document().setMaximumBlockCount(100); self.auth_details.hide()
        self.open_run_button, self.open_screenshot_button = QPushButton("Open run folder"), QPushButton("Open latest screenshot")
        files = QHBoxLayout(); files.addWidget(self.open_run_button); files.addWidget(self.open_screenshot_button)
        layout.addWidget(self.status); layout.addWidget(self.auth_details); layout.addWidget(self.log); layout.addLayout(files); self.setCentralWidget(root)
        self.refresh_button.clicked.connect(self.refresh_devices); self.start_button.clicked.connect(self.start_battle); self.observe_button.clicked.connect(self.observe); self.stop_button.clicked.connect(self.stop)
        self.account_mode.currentIndexChanged.connect(self._selected_account_changed)
        self.login_button.clicked.connect(lambda: self.auth("login"))
        self.open_run_button.clicked.connect(self.open_current_run); self.open_screenshot_button.clicked.connect(self.open_latest_screenshot)

    def account_mode_value(self) -> str: return str(self.account_mode.currentData())
    def _selected_account_changed(self):
        self._account_mode_changed()
        self.refresh_account_identity()
    def refresh_account_identity(self):
        if self.process is not None or self._closing: return
        self.account_identity.setText("Checking account…")
        try:
            env, _ = self._account_environment()
            self._start("account identity", [python_executable(), "-m", "fgo_agent.codex_account",
                                             "identity", "--mode", self.account_mode_value()], env)
        except Exception:
            self.account_identity.setText("Account unavailable. Try reopening the app.")
    def _display_account_identity(self, output: str, exit_code: int):
        try:
            data = json.loads(output)
            if exit_code != 0 or data.get("error") or data.get("mode") != self.account_mode_value():
                raise ValueError("Account check failed")
            if not data.get("logged_in"):
                message = "Not signed in."
            elif data.get("auth_type") == "chatgpt":
                email = data.get("email") or "ChatGPT (email unavailable)"
                plan = data.get("plan_type")
                message = f"AI account: {email}" + (f" · {plan}" if plan else "")
            else:
                message = "Signed in using " + str(data.get("auth_type") or "an unknown account type") + "."
            self.account_identity.setText(message)
        except (ValueError, TypeError, AttributeError):
            self.account_identity.setText("Account unavailable. Try reopening the app.")
    def _account_mode_changed(self):
        dedicated, active = self.account_mode_value() == "dedicated", self.process is not None
        self.login_button.setEnabled(dedicated and not active)
    def _set_idle(self, message: str):
        self.status.setText(message); active = self.process is not None
        for control in (self.refresh_button, self.start_button, self.observe_button, self.serial, self.policy,
                        self.objective, self.account_mode): control.setEnabled(not active)
        self.stop_button.setEnabled(active); self._account_mode_changed()
    def _append(self, text: str):
        if text.strip(): self.log.appendPlainText(text.rstrip())
    def _serial(self) -> str:
        value = self.serial.currentText().strip()
        if not value: raise ValueError("Enter an ADB serial before running the agent.")
        return value
    def _account_environment(self) -> tuple[dict[str, str], Path | None]:
        module, mode = _account_module(), self.account_mode_value()
        if module is None:
            if mode == "dedicated": raise RuntimeError("Dedicated account integration is not installed.")
            return process_environment(os.environ, None), None
        return process_environment(None, module.account_environment(mode)), module.account_home(mode)

    def _start(self, kind: str, command: list[str], environment: dict[str, str] | None = None):
        if self.process is not None: self._append("A process is already running."); return
        process = QProcess(self); process.setWorkingDirectory(str(ROOT)); process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        if environment is not None:
            qt_environment = QProcessEnvironment()
            for key, value in environment.items(): qt_environment.insert(key, value)
            process.setProcessEnvironment(qt_environment)
        process.readyReadStandardOutput.connect(lambda p=process: self._read_output(p, False))
        process.readyReadStandardError.connect(lambda p=process: self._read_output(p, True))
        process.finished.connect(lambda code, status, p=process: self._finished(p, code, status))
        process.errorOccurred.connect(lambda error, p=process: self._process_error(p, error))
        self.process, self.process_kind, self._job_output, self._stopping = process, kind, "", False
        if kind in {"one battle", "read-only observation"}: self.current_run = None
        if kind == "account login": self.account_identity.setText("Signing in…")
        if kind.startswith("account ") and kind != "account identity":
            self._auth_partial = ""; self._auth_discarding_line = False
            self.auth_details.clear(); self.auth_details.show()
        self._set_idle("Checking account…" if kind == "account identity" else f"Running {kind}…")
        if kind != "account identity": self._append("$ " + " ".join(command))
        program, arguments = worker_command(command); process.start(program, arguments)
    def _read_output(self, process: QProcess, stderr: bool):
        data = bytes(process.readAllStandardError() if stderr else process.readAllStandardOutput()).decode("utf-8", "replace")
        if process is not self.process: return
        self._record_output(process, data)
    def _record_output(self, process: QProcess, data: str):
        if process is not self.process: return
        self._job_output = (self._job_output + data)[-MAX_JOB_OUTPUT:]
        match = re.search(r"^Session:\s*(.+?)\s*$", self._job_output, re.MULTILINE)
        if match is not None:
            self.current_run = Path(match.group(1)).expanduser().resolve()
        if self.process_kind == "account identity": return
        if (self.process_kind or "").startswith("account "):
            self._append_auth_output(data)
        else: self._append(data)
    def _append_auth_output(self, data: str):
        """Display only complete login lines, after redaction across process chunks."""
        if self._auth_discarding_line:
            newline = data.find("\n")
            if newline < 0: return
            self._auth_discarding_line = False
            data = data[newline + 1:]
        self._auth_partial += data
        while "\n" in self._auth_partial:
            line, self._auth_partial = self._auth_partial.split("\n", 1)
            if self.process_kind == "account login":
                # Copy the complete browser authorization URL, never partial chunks.
                for url in re.findall(r"https://[^\s<>\"']+", line):
                    parsed = QUrl(url)
                    if parsed.host() == "auth.openai.com" and parsed.path() == "/oauth/authorize":
                        QApplication.clipboard().setText(url)
            self.auth_details.appendPlainText(self._redact_auth_text(line.rstrip("\r")))
        if len(self._auth_partial) > MAX_AUTH_PARTIAL:
            # A partial line cannot be safely redacted: drop it until its newline.
            self._auth_partial = ""; self._auth_discarding_line = True
            self.auth_details.appendPlainText("[Long login line omitted]")
    @staticmethod
    def _redact_auth_text(text: str) -> str:
        return re.sub(r"(?i)\b(token|api[ _-]?key|access[ _-]?token|password)\s*[:=]\s*\S+", r"\1=[redacted]", text)

    def refresh_devices(self): self._start("device refresh", agent_command("devices"))
    def observe(self):
        try: env, _ = self._account_environment(); self._start("read-only observation", agent_command("observe", self._serial()), env)
        except Exception as exc: self._set_idle(str(exc))
    def start_battle(self):
        try:
            env, home = self._account_environment()
            self._start("one battle", agent_command("battle", self._serial(), policy=str(self.policy.currentData()),
                                                    account_mode=self.account_mode_value(), codex_home=home,
                                                    objective=str(self.objective.currentData())), env)
        except Exception as exc: self._set_idle(str(exc))
    def auth(self, action: str):
        if self.account_mode_value() != "dedicated": self._set_idle("Dedicated account login is required for this action."); return
        module = _account_module()
        if module is None: self._set_idle("Dedicated account integration is not installed."); return
        try: self._start(f"account {action}", list(module.auth_command(action, "dedicated")), process_environment(None, module.account_environment("dedicated")))
        except Exception as exc: self._set_idle(str(exc))
    def check_login(self):
        module = _account_module()
        if module is None: self._set_idle("Account integration is not installed."); return
        try:
            mode = self.account_mode_value()
            self._start("account status", list(module.auth_command("status", mode)), process_environment(None, module.account_environment(mode)))
        except Exception as exc: self._set_idle(str(exc))
    def stop(self):
        if self.process is None: return
        self._stopping = True; self.status.setText("Stopping owned process…"); self.process.kill()
    def _process_error(self, process: QProcess, error):
        if process is self.process and error == QProcess.ProcessError.FailedToStart:
            if self.process_kind == "account identity": self._display_account_identity("", 1)
            if self.process_kind == "account login": self.account_identity.setText("Account not checked yet.")
            self._complete(process, "The owned worker could not start.")
    def _finished(self, process: QProcess, exit_code: int, _exit_status):
        if process is not self.process: return
        self._read_output(process, False); self._read_output(process, True)
        refresh_identity = self.process_kind == "account login" and not self._stopping and not self._closing
        if self.process_kind == "device refresh" and exit_code == 0: self._update_devices(self._job_output)
        if self.process_kind == "account identity":
            self._display_account_identity(self._job_output, exit_code if not self._stopping else 1)
            message = "Ready."
        elif self.process_kind == "account status": message = "Account status: signed in." if exit_code == 0 else "Account status: not signed in or unavailable."
        elif self._stopping:
            message = "Stopped."
            if self.process_kind == "account login": self.account_identity.setText("Account not checked yet.")
        elif self.process_kind == "one battle": message = self._run_result_message(exit_code)
        elif self.process_kind == "read-only observation": message = self._run_result_message(exit_code)
        elif self.process_kind == "account login": message = "Signed in." if exit_code == 0 else "Login did not complete. Try again."
        else: message = f"{self.process_kind} finished with exit code {exit_code}."
        if self.process_kind in {"account login", "read-only observation"}: self._append(message)
        self._complete(process, message)
        if refresh_identity: self.refresh_account_identity()
    def _complete(self, process: QProcess, message: str):
        if process is not self.process: return
        was_auth = (self.process_kind or "").startswith("account ")
        self.process = self.process_kind = None; self._job_output = ""; self._auth_partial = ""; self._auth_discarding_line = False
        self._set_idle(message); process.deleteLater()
        if was_auth: self.auth_details.clear(); self.auth_details.hide()
        if self._closing: self.close()
    def _update_devices(self, output: str):
        try:
            start, end = output.rfind("["), output.rfind("]"); devices = json.loads(output[start:end + 1])
            serials = [item.get("serial") for item in devices if isinstance(item, dict) and item.get("serial")]
            current = self.serial.currentText(); self.serial.clear(); self.serial.addItems(serials or [DEFAULT_SERIAL]); self.serial.setEditText(current or DEFAULT_SERIAL)
        except (json.JSONDecodeError, ValueError): self._append("Could not parse device list; enter the serial manually.")
    def _run_result_message(self, exit_code: int) -> str:
        if self.current_run is None:
            return f"Run finished with exit code {exit_code}."
        try:
            battle = self.current_run / "battle-result.json"
            if battle.is_file():
                data = json.loads(battle.read_text(encoding="utf-8"))
                detail = data.get("outcome") or data.get("status") or data.get("reason") or "unknown"
                return f"Battle: {detail} ({data.get('status', 'unknown')})."
            observation = self.current_run / "observation.json"
            if observation.is_file():
                data = json.loads(observation.read_text(encoding='utf-8'))
                if exit_code != 0: return "Observe did not complete; inspect the saved run."
                if data.get("phase") == "tactical":
                    return f"Observe complete: battle ready, wave {data.get('wave', '?')}, turn {data.get('turn', '?')}. No game actions taken."
                return f"Observe complete: {data.get('phase', 'unknown')} screen. No game actions taken."
        except (OSError, json.JSONDecodeError):
            pass
        return f"Run finished with exit code {exit_code}; inspect the saved run."
    def _open_path(self, path: Path): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
    def open_current_run(self): self._open_path(self.current_run or (ROOT.parent / "agent-runs"))
    def open_latest_screenshot(self):
        screenshot = latest_screenshot(self.current_run) if self.current_run is not None else latest_screenshot(ROOT.parent / "agent-runs")
        if screenshot is None: self._set_idle("No saved screenshots exist yet.")
        else: QDesktopServices.openUrl(QUrl.fromLocalFile(str(screenshot.resolve())))
    def closeEvent(self, event):
        if self.process is not None:
            self._closing = True; self.stop(); event.ignore(); return
        event.accept()


def main(argv=None) -> int:
    arguments = argparse.ArgumentParser(description="FGO Agent English desktop GUI"); arguments.add_argument("--smoke-test", action="store_true")
    parsed = arguments.parse_args(argv); app = QApplication.instance() or QApplication([sys.argv[0]])
    QApplication.setOrganizationName("FGO-Codex"); QApplication.setApplicationName("FGO Agent")
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)); data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "fgo-agent-gui.lock")); lock.setStaleLockTime(0)
    if not lock.tryLock(100): return 2
    try:
        window = AgentMainWindow()
        if parsed.smoke_test: window.close(); return 0
        window.show(); QTimer.singleShot(0, window.refresh_account_identity); return app.exec()
    finally: lock.unlock()


if __name__ == "__main__": raise SystemExit(main())
