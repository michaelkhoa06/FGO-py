"""Explicit, bounded ADB transport for a single local emulator.

No discovery operation launches FGO or sends input. Screens are normalized only
from landscape 16:9; an unexpected aspect ratio is rejected rather than cropped.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PACKAGE = "com.aniplex.fategrandorder.en"


def find_adb(explicit: str | None = None) -> str:
    candidates = [explicit, os.environ.get("FGO_ADB"), shutil.which("adb")]
    candidates += [
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Netease/MuMuPlayer/nx_main/adb.exe"),
        str(Path(sys.executable).parent / "Lib/site-packages/airtest/core/android/static/adb/windows/adb.exe"),
    ]
    if explicit:
        candidates = [explicit]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("ADB not found. Pass --adb PATH or set FGO_ADB.")


def adb_command(adb: str, *args: str, timeout: float = 10.0) -> bytes:
    result = subprocess.run(
        [adb, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ADB failed ({result.returncode}): {message}")
    return result.stdout


def list_devices(adb: str) -> list[dict]:
    lines = adb_command(adb, "devices", "-l").decode("utf-8", errors="replace").splitlines()
    return [dict(serial=parts[0], state=parts[1], details=" ".join(parts[2:]))
            for line in lines if len(parts := line.split()) >= 2
            and not line.startswith(("List of devices", "*"))]


def decode_raw_screencap(data: bytes):
    """BGR image from `screencap` without -p, or None when the bytes are not raw RGBA.

    The header is width, height and pixel format (1 = RGBA_8888), plus a colour
    space word on newer Android: 12 or 16 bytes before width*height*4 pixels.
    """
    import numpy as np
    import cv2
    if len(data) < 16:
        return None
    width, height, pixel_format = (int.from_bytes(data[i:i + 4], "little") for i in (0, 4, 8))
    header = len(data) - width * height * 4
    if pixel_format != 1 or header not in (12, 16) or not (0 < width <= 8192 and 0 < height <= 8192):
        return None
    pixels = np.frombuffer(data, dtype=np.uint8, offset=header).reshape(height, width, 4)
    return cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)


def resumed_package(output: str) -> str | None:
    """Only trust a resumed-activity record, never any historical package mention."""
    for marker in ("topResumedActivity", "mResumedActivity", "ResumedActivity"):
        for line in output.splitlines():
            if marker in line:
                match = re.search(r"\b([\w.]+)/[\w.$]+", line)
                if match:
                    return match.group(1)
    return None


class AdbEmulator:
    def __init__(self, serial: str, adb: str, *, allow_input: bool = False):
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d{1,5}|emulator-\d+", serial):
            raise ValueError("Select a local emulator serial, e.g. 127.0.0.1:16384.")
        self.serial, self.adb, self.allow_input = serial, adb, allow_input
        self.native_size: tuple[int, int] | None = None

    def connect(self) -> None:
        if ":" in self.serial:
            response = adb_command(self.adb, "connect", self.serial).decode("utf-8", errors="replace")
            if "connected to" not in response:
                raise RuntimeError(response.strip())
        if not any(d["serial"] == self.serial and d["state"] == "device" for d in list_devices(self.adb)):
            raise RuntimeError(f"Emulator {self.serial} is not online. Open that MuMu instance first.")
        self.check_foreground()

    def command(self, *args: str) -> bytes:
        return adb_command(self.adb, "-s", self.serial, *args)

    def check_foreground(self) -> None:
        actual = resumed_package(self.command("shell", "dumpsys", "activity", "activities").decode("utf-8", errors="replace"))
        if actual != PACKAGE:
            raise RuntimeError(f"FGO NA must be in the foreground; observed {actual or 'unknown'}. No input sent.")

    def screenshot(self):
        import cv2
        import numpy as np
        self.check_foreground()
        # Raw RGBA capture skips the device's PNG encoding: measured on MuMu at
        # 1920x1080, 0.35 s instead of 1.02 s per frame. PNG stays the fallback.
        image = decode_raw_screencap(self.command("exec-out", "screencap"))
        if image is None:
            image = cv2.imdecode(np.frombuffer(self.command("exec-out", "screencap", "-p"), dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("ADB did not return a valid PNG screenshot.")
        height, width = image.shape[:2]
        if width < 960 or height < 540 or abs(width / height - 16 / 9) > 0.005:
            raise RuntimeError(f"Expected a landscape 16:9 emulator display (prefer 1280x720), got {width}x{height}.")
        self.native_size = (width, height)
        return cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA if height > 720 else cv2.INTER_CUBIC)

    def tap(self, x: int, y: int) -> None:
        if not self.allow_input:
            raise RuntimeError("This connection is observation-only.")
        if self.native_size is None or not (0 <= x < 1280 and 0 <= y < 720):
            raise ValueError("A valid screenshot and in-bounds coordinates are required before input.")
        self.check_foreground()
        width, height = self.native_size
        self.command("shell", "input", "tap", str(round(x * width / 1280)), str(round(y * height / 720)))

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        """Press and hold one point: a swipe that starts and ends in the same place."""
        self.swipe(x, y, x, y, duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        if not self.allow_input:
            raise RuntimeError("This connection is observation-only.")
        if (self.native_size is None or not all(0 <= x < 1280 for x in (x1, x2))
                or not all(0 <= y < 720 for y in (y1, y2)) or not 100 <= duration_ms <= 3000):
            raise ValueError("A valid screenshot, in-bounds coordinates and a 100-3000 ms gesture are required.")
        self.check_foreground()
        width, height = self.native_size
        points = [str(round(x * width / 1280)) if i % 2 == 0 else str(round(x * height / 720))
                  for i, x in enumerate((x1, y1, x2, y2))]
        self.command("shell", "input", "swipe", *points, str(duration_ms))
