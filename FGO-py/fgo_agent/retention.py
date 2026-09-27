"""Keep only the newest run folders so ``agent-runs`` does not grow without bound.

Every observe/execute/battle and replay session writes a folder of screenshots,
traces and model calls; by 2026-09-24 they had reached 9.8 GB. Only direct child
folders named like a session are candidates (``20260924T063109Z-1da0e468`` and
``replay-...``); scripts, logs and any other file or folder there are never
touched. Battle sessions and replays are kept separately, the newest ``keep`` of
each. The session being written now is always kept, and a folder containing a
file named ``KEEP`` is never deleted.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat

SESSION = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
REPLAY = re.compile(r"^replay-\d{8}T\d{6}Z-[0-9a-f]{8}$")
KEEP_MARKER = "KEEP"
DEFAULT_KEEP = 5


def _writable_then_retry(function, path, _exc_info):
    # Windows leaves some files read-only; clear the flag and retry once.
    os.chmod(path, stat.S_IWRITE)
    function(path)


def prune_runs(root: Path, keep: int = DEFAULT_KEEP, current: Path | None = None) -> list[Path]:
    """Delete run folders beyond the newest ``keep`` of each kind; return what was deleted."""
    if type(keep) is not int or keep < 1:
        raise ValueError("keep must be a positive integer")
    root = Path(root).resolve()
    if not root.is_dir():
        return []
    current = Path(current).resolve() if current is not None else None
    deleted = []
    for pattern in (SESSION, REPLAY):
        runs = sorted((child for child in root.iterdir()
                       if pattern.match(child.name) and child.is_dir() and not child.is_symlink()),
                      key=lambda child: child.name, reverse=True)
        for old in runs[keep:]:
            if old.resolve() == current or (old / KEEP_MARKER).exists() or old.resolve().parent != root:
                continue
            shutil.rmtree(old, onerror=_writable_then_retry)
            deleted.append(old)
    return deleted
