"""The Confirm Party screen: each member's real level, HP, ATK, skill and NP levels.

User idea (2026-09-26): start the battle from Confirm Party so the agent reads the
account's actual numbers instead of guessing. The screen shows ATK with Fou and
Craft Essence ATK included, skill levels as "10/10/10" and the NP level after a
small icon. Columns 1-3 are the starting members (field slots 1-3); 4-6 are the
sub members (back line 1-3).
"""
from __future__ import annotations

import re

COLUMN_LEFT = (38, 238, 438, 652, 852, 1052)


def _integer(text) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(text or ""))
    return int(digits) if digits else None


def parse_levels(text: str) -> tuple[list[int] | None, int | None]:
    """("10410/10 1") -> ([10, 10, 10], 1). The NP level is the last digit; a "/"
    misread as "4" or "7" is split only where both sides are valid levels."""
    compact = re.sub(r"[^0-9/]", " ", str(text or "")).strip()
    match = re.search(r"(\d)\s*$", compact)
    if not match:
        return None, None
    np_level = int(match.group(1))
    body = compact[:match.start()].replace(" ", "")
    if not body:
        # The NP digit was glued to the last skill level ("10/5/101").
        body, np_level = compact[:-1].replace(" ", ""), int(compact[-1])
    parts = [p for p in body.split("/") if p]
    valid = lambda value: value.isdigit() and 1 <= int(value) <= 10

    def split(part):
        if valid(part):
            return [part]
        for i, ch in enumerate(part):
            if ch in "47" and 0 < i < len(part) - 1 and valid(part[:i]):
                rest = split(part[i + 1:])
                if rest:
                    return [part[:i]] + rest
        return None
    levels = []
    for part in parts:
        pieces = split(part)
        if pieces is None:
            return None, np_level if 1 <= np_level <= 5 else None
        levels += pieces
    if len(levels) == 4 and not (1 <= np_level <= 5):
        levels, np_level = levels[:3], None
    if len(levels) != 3:
        return None, np_level if 1 <= np_level <= 5 else None
    return [int(v) for v in levels], (np_level if 1 <= np_level <= 5 else None)


def read_confirm_party(image, ocr, detector=None) -> dict | None:
    """Members of the Confirm Party screen, or None when it is not showing."""
    title = str(ocr(image[15:70, 930:1270]) or "")
    button = str(ocr(image[655:705, 1095:1250]) or "")
    if "confirm" not in title.lower() or "battle" not in button.lower():
        return None
    classes = None
    if detector is not None:
        try:
            classes = detector.getTeamServantClassRank()
        except Exception:
            classes = None
    members = []
    for index, left in enumerate(COLUMN_LEFT):
        crop = lambda y0, y1, x0, x1: image[y0:y1, left + x0:left + x1]
        skills, np_level = parse_levels(ocr(crop(410, 434, 20, 190)))
        klass = classes[index] if classes and index < len(classes) else None
        members.append({
            "position": index + 1,
            "group": "starting" if index < 3 else "sub",
            "slot": index + 1 if index < 3 else index - 2,
            "level": _integer(ocr(crop(526, 549, 5, 75))),
            "hp": _integer(ocr(crop(551, 572, 95, 190))),
            "atk": _integer(ocr(crop(573, 595, 95, 190))),
            "skill_levels": skills,
            "np_level": np_level,
            "class_index": klass[0] if klass else None,
        })
    return {"members": members}
