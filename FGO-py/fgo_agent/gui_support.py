"""Small, testable helpers for the English FGO agent desktop GUI.

The GUI must not inspect credential files.  Account functions are imported only
when the optional account integration module is available.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "fgoAgentGuiWorker.py"
DEFAULT_SERIAL = "127.0.0.1:16384"
DEFAULT_MODEL = "gpt-6-sol"
DEFAULT_REASONING = "low"
DEFAULT_OBJECTIVE = "farm_fast"
OBJECTIVE_LABELS = (("Farming — fewest turns", "farm_fast"),
                    ("Story / unknown — survive and win", "survive_and_win"))
AUTH_ENVIRONMENT_KEYS = frozenset({"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
                                   "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION",
                                   "OPENAI_PROJECT_ID"})


def python_executable() -> str:
    """Use console Python when the GUI was launched by pythonw.exe."""
    executable = Path(sys.executable)
    return str(executable.with_name("python.exe")) if executable.name.lower() == "pythonw.exe" else str(executable)


def agent_command(command: str, serial: str | None = None, *, policy: str = "codex",
                  account_mode: str = "development", codex_home: Path | None = None,
                  objective: str = DEFAULT_OBJECTIVE) -> list[str]:
    """Build an explicit fgoAgent invocation without executing it."""
    command_line = [python_executable(), str(ROOT / "fgoAgent.py"), command]
    if command == "devices":
        return command_line
    if not serial or not serial.strip():
        raise ValueError("Select or enter an ADB serial.")
    command_line.extend(["--serial", serial.strip()])
    if command == "battle":
        if policy not in {"codex", "legacy"}:
            raise ValueError("Policy must be AI or baseline.")
        command_line.extend(["--policy", policy])
        if policy == "codex":
            if objective not in {value for _, value in OBJECTIVE_LABELS}:
                raise ValueError("Objective must be a known battle objective.")
            command_line.extend(["--model", DEFAULT_MODEL, "--reasoning", DEFAULT_REASONING,
                                 "--objective", objective])
        # Only an isolated dedicated account receives an explicit Codex home.
        if account_mode == "dedicated" and codex_home is not None:
            command_line.extend(["--codex-home", str(codex_home)])
    return command_line


def worker_command(command: list[str]) -> tuple[str, list[str]]:
    """Wrap a GUI-owned command in the Windows Job Object worker.

    The worker creates the job before it starts the child.  Killing the tracked
    wrapper therefore kills that owned child tree without enumerating processes.
    """
    return python_executable(), [str(WORKER_PATH), "--", *command]


def process_environment(base: Mapping[str, str] | None, account: Mapping[str, object] | None) -> dict[str, str]:
    """Return a process environment without reading or serializing credentials."""
    # account_environment is a complete, already-sanitized child environment.
    # Never layer it over os.environ, which might restore an ambient token.
    environment = dict(account) if account is not None else dict(base or {})
    if account is None:
        for key in AUTH_ENVIRONMENT_KEYS:
            environment.pop(key, None)
    environment = {str(key): str(value) for key, value in environment.items() if value is not None}
    environment["PYTHONUTF8"] = "1"
    return environment


def latest_screenshot(runs_directory: Path) -> Path | None:
    screenshot_directory = runs_directory / "screenshots"
    pattern = "*.png" if screenshot_directory.is_dir() else "*/screenshots/*.png"
    candidates = [path for path in (screenshot_directory if screenshot_directory.is_dir() else runs_directory).glob(pattern) if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
