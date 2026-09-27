"""Append-only, JSON-safe session tracing for controller decisions."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class TraceWriter:
    """Writes an audit-friendly JSONL stream into an existing session directory."""

    def __init__(self, session_path: str | Path, *, now: Callable[[], datetime] | None = None):
        self.session_path = Path(session_path)
        self.session_path.mkdir(parents=True, exist_ok=True)
        self.path = self.session_path / "trace.jsonl"
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record(self, event: str, payload: Any) -> None:
        entry = {
            "timestamp": self._now().astimezone(timezone.utc).isoformat(),
            "event": event,
            "payload": _json_safe(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def write_summary(self, text: str) -> Path:
        """Optionally persist a short human-readable companion to the JSONL trace."""
        path = self.session_path / "summary.md"
        path.write_text(text, encoding="utf-8")
        return path

