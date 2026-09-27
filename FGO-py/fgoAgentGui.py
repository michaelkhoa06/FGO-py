"""English desktop UI for FGO NA story battles played by the Codex agent.

Revamp (user, 2026-09-26): remembered settings, story objective only, a status
card with live battle progress instead of a raw log, dark theme, and a single
Start that works from Confirm Party (reads the party, taps To Battle) or from a
battle already on screen.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QProcess, QProcessEnvironment, QSettings, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from fgo_agent.gui_support import (DEFAULT_MODEL, DEFAULT_OBJECTIVE, DEFAULT_SERIAL, ROOT,
                                   agent_command, battle_progress, latest_screenshot, process_environment,
                                   python_executable, worker_command)

MAX_JOB_OUTPUT = 64 * 1024
MAX_AUTH_PARTIAL = 8192
PROGRESS_INTERVAL_MS = 1500

COLORS = {"window": "#14161b", "card": "#1d2027", "border": "#2b303a", "text": "#e7e9ee", "muted": "#9199a6",
          "accent": "#5b8def", "accent_hover": "#6f9bf2", "danger": "#e5534b", "success": "#46b47a",
          "warning": "#d9a441", "input": "#252932"}

STYLE = f"""
QWidget {{ background: {COLORS['window']}; color: {COLORS['text']}; font-size: 10.5pt; }}
QFrame#card {{ background: {COLORS['card']}; border: 1px solid {COLORS['border']}; border-radius: 10px; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 17pt; font-weight: 600; }}
QLabel#subtitle, QLabel#muted {{ color: {COLORS['muted']}; }}
QLabel#cardTitle {{ font-size: 9pt; font-weight: 600; letter-spacing: 1px; color: {COLORS['muted']}; }}
QLabel#statusHeadline {{ font-size: 13pt; font-weight: 600; }}
QComboBox, QPlainTextEdit {{ background: {COLORS['input']}; border: 1px solid {COLORS['border']};
    border-radius: 6px; padding: 5px 8px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {COLORS['input']}; selection-background-color: {COLORS['accent']}; }}
QPushButton {{ background: {COLORS['input']}; border: 1px solid {COLORS['border']}; border-radius: 6px;
    padding: 7px 14px; }}
QPushButton:hover {{ border-color: {COLORS['accent']}; }}
QPushButton:disabled {{ color: #5d6470; border-color: #252a32; }}
QPushButton#primary {{ background: {COLORS['accent']}; border: none; color: white; font-weight: 600;
    font-size: 12pt; padding: 10px 28px; }}
QPushButton#primary:hover {{ background: {COLORS['accent_hover']}; }}
QPushButton#primary:disabled {{ background: #2e3a55; color: #8a93a3; }}
QPushButton#danger {{ border-color: {COLORS['danger']}; color: {COLORS['danger']}; }}
QPushButton#danger:disabled {{ border-color: #252a32; color: #5d6470; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
"""


def _account_module():
    try:
        from fgo_agent import codex_account
        return codex_account
    except ImportError:
        return None


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(); frame.setObjectName("card")
    layout = QVBoxLayout(frame); layout.setContentsMargins(16, 12, 16, 14); layout.setSpacing(8)
    heading = QLabel(title.upper()); heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    return frame, layout


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, COLORS["window"]), (QPalette.ColorRole.WindowText, COLORS["text"]),
                        (QPalette.ColorRole.Base, COLORS["input"]), (QPalette.ColorRole.AlternateBase, COLORS["card"]),
                        (QPalette.ColorRole.Text, COLORS["text"]), (QPalette.ColorRole.Button, COLORS["input"]),
                        (QPalette.ColorRole.ButtonText, COLORS["text"]), (QPalette.ColorRole.Highlight, COLORS["accent"]),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, COLORS["card"]),
                        (QPalette.ColorRole.ToolTipText, COLORS["text"])):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


class AgentMainWindow(QMainWindow):
    """Owns one Job-Object worker at a time; conflicting account/device writes are blocked."""
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("FGO-Codex", "FGO Agent")
        self.process: QProcess | None = None
        self.process_kind: str | None = None
        self._job_output = ""
        self._auth_partial = ""
        self._auth_discarding_line = False
        self.current_run: Path | None = None
        self._stopping = self._closing = False
        self._progress_offset, self._progress, self._started_at = 0, {}, None
        self.setWindowTitle("FGO Agent")
        self.resize(640, 640)
        self._build_ui()
        self._load_settings()
        self.progress_timer = QTimer(self); self.progress_timer.setInterval(PROGRESS_INTERVAL_MS)
        self.progress_timer.timeout.connect(self._refresh_progress)
        self._set_idle("Ready. Open a story battle's Confirm Party screen (or the battle itself), then press Start.")

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        root = QWidget(self); layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 18); layout.setSpacing(12)
        title = QLabel("FGO Agent"); title.setObjectName("title")
        subtitle = QLabel(f"Story battles played by Codex ({DEFAULT_MODEL})"); subtitle.setObjectName("subtitle")
        layout.addWidget(title); layout.addWidget(subtitle)

        setup, setup_layout = _card("Setup")
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        self.serial = QComboBox(); self.serial.setEditable(True); self.serial.addItem(DEFAULT_SERIAL)
        self.refresh_button = QPushButton("Refresh")
        grid.addWidget(QLabel("Emulator"), 0, 0); grid.addWidget(self.serial, 0, 1); grid.addWidget(self.refresh_button, 0, 2)
        self.account_mode = QComboBox()
        self.account_mode.addItem("Development", "development")
        self.account_mode.addItem("Other Account", "dedicated")
        self.login_button = QPushButton("Login")
        grid.addWidget(QLabel("AI account"), 1, 0); grid.addWidget(self.account_mode, 1, 1); grid.addWidget(self.login_button, 1, 2)
        self.account_identity = QLabel("Account not checked yet."); self.account_identity.setObjectName("muted")
        self.account_identity.setTextFormat(Qt.TextFormat.PlainText); self.account_identity.setWordWrap(True)
        self.account_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.account_identity, 2, 1, 1, 2)
        self.policy = QComboBox(); self.policy.addItem("AI — Codex", "codex"); self.policy.addItem("Baseline (no AI)", "legacy")
        grid.addWidget(QLabel("Player"), 3, 0); grid.addWidget(self.policy, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        setup_layout.addLayout(grid)
        self.start_from_party = QCheckBox("Start from the Confirm Party screen (reads real ATK, skill and NP levels)")
        self.start_from_party.setChecked(True)
        setup_layout.addWidget(self.start_from_party)
        layout.addWidget(setup)

        controls = QHBoxLayout(); controls.setSpacing(10)
        self.start_button = QPushButton("Start"); self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Stop"); self.stop_button.setObjectName("danger")
        self.open_screenshot_button = QPushButton("Open latest screenshot")
        controls.addWidget(self.start_button); controls.addWidget(self.stop_button); controls.addStretch(1)
        controls.addWidget(self.open_screenshot_button)
        layout.addLayout(controls)

        status_card, status_layout = _card("Status")
        self.status = QLabel(); self.status.setObjectName("statusHeadline"); self.status.setWordWrap(True)
        self.progress_label = QLabel(""); self.progress_label.setWordWrap(True)
        self.plan_label = QLabel(""); self.plan_label.setObjectName("muted"); self.plan_label.setWordWrap(True)
        for widget in (self.status, self.progress_label, self.plan_label):
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            status_layout.addWidget(widget)
        self.auth_details = QPlainTextEdit(); self.auth_details.setReadOnly(True)
        self.auth_details.document().setMaximumBlockCount(100); self.auth_details.hide()
        status_layout.addWidget(self.auth_details)
        status_layout.addStretch(1)
        layout.addWidget(status_card, 1)
        self.setCentralWidget(root)

        self.refresh_button.clicked.connect(self.refresh_devices); self.start_button.clicked.connect(self.start_battle)
        self.stop_button.clicked.connect(self.stop)
        self.account_mode.currentIndexChanged.connect(self._selected_account_changed)
        self.login_button.clicked.connect(lambda: self.auth("login"))
        self.open_screenshot_button.clicked.connect(self.open_latest_screenshot)
        for widget in (self.serial, self.policy):
            widget.currentIndexChanged.connect(self._save_settings)
        self.serial.editTextChanged.connect(self._save_settings)
        self.start_from_party.toggled.connect(self._save_settings)

    # ---- remembered settings ------------------------------------------------
    def _load_settings(self):
        self._loading = True
        try:
            serial = self.settings.value("serial", DEFAULT_SERIAL)
            if serial: self.serial.setEditText(str(serial))
            for combo, key in ((self.account_mode, "account_mode"), (self.policy, "policy")):
                index = combo.findData(self.settings.value(key))
                if index >= 0:
                    combo.blockSignals(True); combo.setCurrentIndex(index); combo.blockSignals(False)
            stored = self.settings.value("start_from_party")
            if stored is not None:
                self.start_from_party.setChecked(str(stored).lower() in ("true", "1"))
        finally:
            self._loading = False

    def _save_settings(self, *_):
        if getattr(self, "_loading", False): return
        self.settings.setValue("serial", self.serial.currentText().strip())
        self.settings.setValue("account_mode", self.account_mode_value())
        self.settings.setValue("policy", str(self.policy.currentData()))
        self.settings.setValue("start_from_party", self.start_from_party.isChecked())
        self.settings.sync()

    # ---- account ----------------------------------------------------------
    def account_mode_value(self) -> str: return str(self.account_mode.currentData())
    def _selected_account_changed(self):
        self._save_settings()
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

    # ---- state ------------------------------------------------------------
    def _set_idle(self, message: str):
        self.status.setText(message); active = self.process is not None
        for control in (self.refresh_button, self.start_button, self.serial, self.policy,
                        self.account_mode, self.start_from_party): control.setEnabled(not active)
        self.stop_button.setEnabled(active); self._account_mode_changed()
    def _serial(self) -> str:
        value = self.serial.currentText().strip()
        if not value: raise ValueError("Enter the emulator's ADB serial before starting.")
        return value
    def _account_environment(self) -> tuple[dict[str, str], Path | None]:
        module, mode = _account_module(), self.account_mode_value()
        if module is None:
            if mode == "dedicated": raise RuntimeError("Dedicated account integration is not installed.")
            return process_environment(os.environ, None), None
        return process_environment(None, module.account_environment(mode)), module.account_home(mode)

    def _start(self, kind: str, command: list[str], environment: dict[str, str] | None = None):
        if self.process is not None: self.status.setText("Something is already running."); return
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
        if kind == "one battle":
            self.current_run = None
            self._progress_offset, self._progress, self._started_at = 0, {}, time.monotonic()
            self.progress_label.setText("Starting…"); self.plan_label.setText("")
            self.progress_timer.start()
        if kind == "account login": self.account_identity.setText("Signing in…")
        if kind.startswith("account ") and kind != "account identity":
            self._auth_partial = ""; self._auth_discarding_line = False
            self.auth_details.clear(); self.auth_details.show()
        labels = {"account identity": "Checking account…", "one battle": "Battle running…",
                  "device refresh": "Looking for emulators…", "account login": "Signing in (finish in your browser)…"}
        self._set_idle(labels.get(kind, f"Running {kind}…"))
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
        if (self.process_kind or "").startswith("account ") and self.process_kind != "account identity":
            self._append_auth_output(data)
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

    # ---- actions ----------------------------------------------------------
    def refresh_devices(self): self._start("device refresh", agent_command("devices"))
    def start_battle(self):
        try:
            env, home = self._account_environment()
            self._save_settings()
            self._start("one battle", agent_command("battle", self._serial(), policy=str(self.policy.currentData()),
                                                    account_mode=self.account_mode_value(), codex_home=home,
                                                    objective=DEFAULT_OBJECTIVE,
                                                    start_from_party=self.start_from_party.isChecked()), env)
        except Exception as exc: self._set_idle(str(exc))
    def auth(self, action: str):
        if self.account_mode_value() != "dedicated": self._set_idle("Dedicated account login is required for this action."); return
        module = _account_module()
        if module is None: self._set_idle("Dedicated account integration is not installed."); return
        try: self._start(f"account {action}", list(module.auth_command(action, "dedicated")), process_environment(None, module.account_environment("dedicated")))
        except Exception as exc: self._set_idle(str(exc))
    def stop(self):
        if self.process is None: return
        self._stopping = True; self.status.setText("Stopping…"); self.process.kill()

    # ---- progress -----------------------------------------------------------
    def _refresh_progress(self):
        if self.current_run is None: return
        self._progress_offset, self._progress = battle_progress(self.current_run / "trace.jsonl",
                                                                self._progress_offset, self._progress)
        self.progress_label.setText(self._progress_text())
        plan = self._progress.get("plan")
        self.plan_label.setText(f"Plan: {plan}" if plan else "")
    def _progress_text(self) -> str:
        state, parts = self._progress, []
        if state.get("turn") is not None:
            wave = f"Wave {state.get('wave')}" + (f"/{state['stage_total']}" if state.get("stage_total") else "")
            parts.append(f"{wave} · Turn {state['turn']}")
        if state.get("enemy_hp"):
            parts.append("Enemy HP " + ", ".join(f"{hp:,}" for hp in state["enemy_hp"][:6]))
        if self._started_at is not None:
            minutes, seconds = divmod(int(time.monotonic() - self._started_at), 60)
            parts.append(f"{minutes}:{seconds:02d} elapsed")
        text = "   ·   ".join(parts) or "Waiting for the battle screen…"
        if state.get("last_action"):
            text += f"\nLast: {state['last_action']}"
        return text

    # ---- completion ---------------------------------------------------------
    def _process_error(self, process: QProcess, error):
        if process is self.process and error == QProcess.ProcessError.FailedToStart:
            if self.process_kind == "account identity": self._display_account_identity("", 1)
            if self.process_kind == "account login": self.account_identity.setText("Account not checked yet.")
            self._complete(process, "The agent could not start.")
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
        elif self.process_kind == "one battle":
            self._refresh_progress(); message = self._run_result_message(exit_code)
        elif self.process_kind == "account login": message = "Signed in." if exit_code == 0 else "Login did not complete. Try again."
        elif self.process_kind == "device refresh": message = "Emulator list updated." if exit_code == 0 else "Could not list emulators."
        else: message = f"{self.process_kind} finished with exit code {exit_code}."
        self._complete(process, message)
        if refresh_identity: self.refresh_account_identity()
    def _complete(self, process: QProcess, message: str):
        if process is not self.process: return
        was_auth = (self.process_kind or "").startswith("account ")
        if self.process_kind == "one battle": self.progress_timer.stop()
        self.process = self.process_kind = None; self._job_output = ""; self._auth_partial = ""; self._auth_discarding_line = False
        self._set_idle(message); process.deleteLater()
        if was_auth: self.auth_details.clear(); self.auth_details.hide()
        if self._closing: self.close()
    def _update_devices(self, output: str):
        try:
            start, end = output.rfind("["), output.rfind("]"); devices = json.loads(output[start:end + 1])
            serials = [item.get("serial") for item in devices if isinstance(item, dict) and item.get("serial")]
            current = self.serial.currentText(); self.serial.clear(); self.serial.addItems(serials or [DEFAULT_SERIAL]); self.serial.setEditText(current or DEFAULT_SERIAL)
        except (json.JSONDecodeError, ValueError): self.status.setText("Could not read the emulator list; type the serial instead.")
    def _run_result_message(self, exit_code: int) -> str:
        if self.current_run is None:
            return "The battle did not start. Check that the emulator is running and try again."
        try:
            battle = self.current_run / "battle-result.json"
            if battle.is_file():
                data = json.loads(battle.read_text(encoding="utf-8"))
                outcome = data.get("outcome")
                if outcome == "victory": return "Victory!"
                if outcome == "defeat": return "Defeat. The game is on the defeat screen (do not tap Continue)."
                reason = data.get("reason") or data.get("status") or "unknown"
                return f"Stopped before the end: {reason}"
        except (OSError, json.JSONDecodeError):
            pass
        return "The battle ended without a result. Open the latest screenshot to see where it stopped."
    def open_latest_screenshot(self):
        screenshot = latest_screenshot(self.current_run) if self.current_run is not None else latest_screenshot(ROOT.parent / "agent-runs")
        if screenshot is None: self.status.setText("No screenshots saved yet.")
        else: QDesktopServices.openUrl(QUrl.fromLocalFile(str(screenshot.resolve())))
    def closeEvent(self, event):
        self._save_settings()
        if self.process is not None:
            self._closing = True; self.stop(); event.ignore(); return
        event.accept()


def main(argv=None) -> int:
    arguments = argparse.ArgumentParser(description="FGO Agent English desktop GUI"); arguments.add_argument("--smoke-test", action="store_true")
    parsed = arguments.parse_args(argv); app = QApplication.instance() or QApplication([sys.argv[0]])
    QApplication.setOrganizationName("FGO-Codex"); QApplication.setApplicationName("FGO Agent")
    apply_dark_theme(app)
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)); data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "fgo-agent-gui.lock")); lock.setStaleLockTime(0)
    if not lock.tryLock(100): return 2
    try:
        window = AgentMainWindow()
        if parsed.smoke_test: window.close(); return 0
        window.show(); QTimer.singleShot(0, window.refresh_account_identity); return app.exec()
    finally: lock.unlock()


if __name__ == "__main__": raise SystemExit(main())
