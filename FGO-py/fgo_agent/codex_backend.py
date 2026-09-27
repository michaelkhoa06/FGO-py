"""A bounded, read-only Codex CLI decision backend.

The backend is deliberately separate from the battle driver: Codex receives only the
caller-provided state and can only return a JSON decision.  It is never given an ADB
handle or the repository as its working directory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


_DIAGNOSTIC_LIMIT = 2_000
_JSONL_LIMIT = 2_000_000
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|cookie|credential|password|secret|token)", re.I)
_SECRET_VALUE = re.compile(
    r"(?i)(?:(?:bearer|token|api[_-]?key|authorization|cookie|credential|password|secret)\s*[:=]\s*[^\s,;]+|bearer\s+[^\s,;]+)"
)
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "code_mode", "code_mode_host", "apps",
    "plugins", "hooks", "memories", "skill_search", "multi_agent",
    "computer_use", "browser_use", "image_generation",
)


class BackendError(RuntimeError):
    """A Codex process failure with bounded, redacted diagnostics."""


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[redacted]", value)
    return value


def _diagnostic(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = _SECRET_VALUE.sub("[redacted]", value).replace("\x00", " ").strip()
    return value[:_DIAGNOSTIC_LIMIT]


def _native_executable(candidate: str | os.PathLike[str] | None) -> str | None:
    if not candidate:
        return None
    path = Path(candidate)
    if path.suffix.lower() in {".cmd", ".bat"} or (os.name == "nt" and path.suffix.lower() != ".exe"):
        return None
    if path.is_file():
        return str(path.resolve())
    resolved = shutil.which(str(candidate))
    if (resolved and Path(resolved).suffix.lower() not in {".cmd", ".bat"}
            and (os.name != "nt" or Path(resolved).suffix.lower() == ".exe")):
        return str(Path(resolved).resolve())
    return None


def _codex_version(executable: str) -> tuple[int, ...]:
    """Numeric ``codex --version``; unreadable versions sort first."""
    try:
        result = subprocess.run([executable, "--version"], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout or "")
    return tuple(int(part) for part in match.groups()) if match else ()


_FOUND_CODEX: str | None = None


def find_codex(executable: str | os.PathLike[str] | None = None) -> str:
    """Find a native ``codex.exe`` without falling back to npm's cmd shim.

    The server offers newer models only to newer clients (gpt-6-sol was refused to
    0.145.0 and served to 0.155.0), so prefer the highest version among the PATH
    binary and the one the Codex desktop app keeps updated.
    """
    global _FOUND_CODEX
    if executable is not None:
        found = _native_executable(executable)
        if found:
            return found
        raise BackendError("Configured Codex executable is not a native executable.")
    if _FOUND_CODEX is not None:
        return _FOUND_CODEX

    candidates = []
    command = _native_executable(shutil.which("codex.exe"))
    if command:
        candidates.append(command)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for app_binary in sorted((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")):
            found = _native_executable(app_binary)
            if found and found not in candidates:
                candidates.append(found)
    if len(candidates) > 1:
        candidates.sort(key=_codex_version, reverse=True)
    if candidates:
        _FOUND_CODEX = candidates[0]
        return _FOUND_CODEX

    roots = []
    for name in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value) / "npm" / "node_modules")
    for root in roots:
        packages = (
            root / "@openai" / "codex-win32-x64" / "vendor",
            root / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor",
        )
        for package in packages:
            if package.is_dir():
                matches = sorted(package.glob("**/codex.exe"))
                if matches:
                    return str(matches[0].resolve())
    raise BackendError("Native codex.exe was not found. Install @openai/codex-win32-x64 or pass executable.")


class CodexBackend:
    """Run one schema-constrained, state-only game decision through Codex CLI."""

    def __init__(self, directory: Path, model: str = "gpt-6-sol", effort: str = "low",
                 timeout: float = 60, executable: str | os.PathLike[str] | None = None,
                 codex_home: Path | None = None, service_tier: str | None = None,
                 plan_effort: str | None = "medium") -> None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if not isinstance(model, str) or not model.strip() or not isinstance(effort, str) or not effort.strip():
            raise ValueError("model and effort must be non-empty strings")
        self.directory = Path(directory).resolve()
        self.model = model
        self.effort = effort
        # A whole-battle plan is made a few times per battle, not every action, so
        # it can afford more reasoning than a single turn's decision.
        self.plan_effort = plan_effort
        self.timeout = float(timeout)
        self.executable = executable
        # "priority" is the Codex "Fast" tier: about 1.5x faster decisions.
        self.service_tier = service_tier
        self.codex_home = Path(codex_home).resolve() if codex_home is not None else None
        self.last_call: dict[str, Any] | None = None

    def choose(self, request: dict, schema: dict) -> dict:
        if not isinstance(request, dict) or not isinstance(schema, dict):
            raise ValueError("request and schema must be JSON objects")
        call_dir = self._new_call_directory()
        safe_request, safe_schema = _redact(request), _redact(schema)
        self._write_json(call_dir / "request.json", safe_request)
        self._write_json(call_dir / "schema.json", safe_schema)
        output_path = call_dir / "final.json"
        executable = find_codex(self.executable)
        with tempfile.TemporaryDirectory(prefix="fgo-codex-") as isolated:
            effort = self.effort
            if request.get("task") == "battle_plan" and self.plan_effort:
                # The planner marks cheap calls (revisions, minor re-plans) as "low".
                wanted = (request.get("planning") or {}).get("reasoning")
                effort = wanted if wanted in ("low", "medium", "high") else self.plan_effort
            command = self._command(executable, Path(isolated), call_dir / "schema.json", output_path, effort)
            metadata = {
                "model": self.model, "effort": effort, "service_tier": self.service_tier,
                "timeout_seconds": self.timeout,
                "executable": executable, "command": command, "cwd": isolated,
                "started_at": datetime.now(timezone.utc).isoformat(), "status": "started",
            }
            self.last_call = {"call_dir": str(call_dir), **metadata}
            prompt = self._prompt(safe_request)
            try:
                from .codex_account import environment_for_home
                result = subprocess.run(
                    command, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=self.timeout, check=False, shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=isolated,
                    encoding="utf-8", errors="replace",
                    env=environment_for_home(self.codex_home),
                )
            except subprocess.TimeoutExpired as exc:
                self._write_text(call_dir / "stdout.txt", _diagnostic(exc.stdout))
                self._write_text(call_dir / "stderr.txt", _diagnostic(exc.stderr))
                self._finish(metadata, call_dir, "timeout")
                raise BackendError(f"Codex decision timed out after {self.timeout:g} seconds.") from None
            except OSError as exc:
                self._write_text(call_dir / "stdout.txt", "")
                self._write_text(call_dir / "stderr.txt", _diagnostic(str(exc)))
                self._finish(metadata, call_dir, "launch_error")
                raise BackendError(f"Codex decision could not start: {_diagnostic(str(exc))}") from None

        stdout, stderr = result.stdout or "", _diagnostic(result.stderr)
        self._write_text(call_dir / "stderr.txt", stderr)
        if result.returncode != 0:
            self._write_text(call_dir / "stdout.txt", _diagnostic(stdout))
            self._finish(metadata, call_dir, "failed", returncode=result.returncode)
            detail = stderr or _diagnostic(stdout) or "no diagnostics"
            raise BackendError(f"Codex decision failed (exit {result.returncode}): {detail}")
        try:
            safe_jsonl, usage = self._audit_jsonl(stdout)
        except BackendError:
            self._write_text(call_dir / "stdout.txt", _diagnostic(stdout))
            self._finish(metadata, call_dir, "invalid_event_stream")
            raise
        self._write_text(call_dir / "stdout.jsonl", safe_jsonl)
        self._write_text(call_dir / "stdout.txt", safe_jsonl)
        if not output_path.is_file():
            self._finish(metadata, call_dir, "missing_output")
            raise BackendError("Codex decision did not produce a final JSON response.")
        try:
            response = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._finish(metadata, call_dir, "malformed_output")
            raise BackendError("Codex decision produced malformed final JSON.") from None
        if not isinstance(response, dict):
            self._finish(metadata, call_dir, "invalid_output")
            raise BackendError("Codex decision final JSON must be an object.")
        self._write_json(output_path, _redact(response))
        self._finish(metadata, call_dir, "completed", returncode=result.returncode, usage=usage)
        return response

    def _new_call_directory(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        for index in range(1, 100_000):
            candidate = self.directory / f"call{index:03d}"
            try:
                candidate.mkdir()
                return candidate
            except FileExistsError:
                continue
        raise BackendError("Could not allocate a Codex call directory.")

    def _command(self, executable: str, isolated: Path, schema: Path, output: Path,
                 effort: str | None = None) -> list[str]:
        command = [executable, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", str(schema),
                   "-o", str(output), "--json", "-C", str(isolated), "-m", self.model,
                   "-c", f'model_reasoning_effort="{effort or self.effort}"', "-c", 'web_search="disabled"',
                   "-c", "project_doc_max_bytes=0"]
        if self.service_tier:
            command.extend(("-c", f'service_tier="{self.service_tier}"'))
        for feature in _DISABLED_FEATURES:
            command.extend(("-c", f"features.{feature}=false"))
        if self.codex_home is not None:
            command.extend(("-c", 'cli_auth_credentials_store="file"'))
        return command + ["-"]

    @staticmethod
    def _prompt(request: dict) -> str:
        if request.get("task") == "battle_plan":
            return ("Plan the rest of this battle before it is played, using only the supplied JSON state. "
                    "Follow the objective in quest_context, the instructions in the state and "
                    "planning.instructions. Do not inspect files, run tools, contact services, control "
                    "devices, or infer unprovided facts. Return exactly one JSON object that conforms to the "
                    "supplied output schema.\n\nSTATE:\n" + json.dumps(request, ensure_ascii=False, sort_keys=True))
        return ("Choose this turn's game decision using only the supplied JSON state. Follow the objective in "
                "quest_context and the instructions in the state. Plan the whole turn in one ordered list of "
                "allowed action ids rather than a single action when several are worthwhile. Do not inspect "
                "files, run tools, contact services, control devices, or infer unprovided facts. Return exactly "
                "one JSON object that conforms to the supplied output schema.\n\nSTATE:\n" +
                json.dumps(request, ensure_ascii=False, sort_keys=True))

    def _finish(self, metadata: dict[str, Any], call_dir: Path, status: str, **extra: Any) -> None:
        metadata.update(extra, status=status, ended_at=datetime.now(timezone.utc).isoformat())
        self._write_json(call_dir / "metadata.json", metadata)
        self.last_call = {"call_dir": str(call_dir), **metadata}

    @staticmethod
    def _audit_jsonl(stdout: str) -> tuple[str, Any]:
        """Return redacted audit JSONL and usage only for one clean completed turn."""
        if len(stdout.encode("utf-8", errors="replace")) > _JSONL_LIMIT:
            raise BackendError("Codex decision event stream exceeded the 2 MB audit limit.")
        events, completed, usage = [], 0, None
        for number, line in enumerate(stdout.splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise BackendError(f"Codex decision emitted malformed JSONL at event {number}.") from None
            if not isinstance(event, dict):
                raise BackendError(f"Codex decision emitted a non-object event at event {number}.")
            event_type = str(event.get("type", "")).lower()
            item = event.get("item")
            item_type = str(item.get("type", "")).lower() if isinstance(item, dict) else ""
            item_status = str(item.get("status", "")).lower() if isinstance(item, dict) else ""
            if ("error" in event_type or "failed" in event_type or event.get("error")
                    or str(event.get("status", "")).lower() in {"error", "failed"}
                    or item_status in {"error", "failed"}):
                raise BackendError("Codex decision event stream reported an error or failed event.")
            if any(marker in item_type for marker in ("tool", "command", "mcp", "function_call", "web_search")):
                raise BackendError("Codex decision attempted a prohibited tool event.")
            if event_type == "turn.completed":
                completed += 1
                usage = event.get("usage")
                if usage is None and isinstance(event.get("turn"), dict):
                    usage = event["turn"].get("usage")
            events.append(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
        if completed != 1:
            raise BackendError("Codex decision event stream must contain exactly one completed turn.")
        return "\n".join(events) + "\n", usage

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.write_text(value, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
