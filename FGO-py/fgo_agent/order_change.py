"""Recognize the master-skill palette and the Order Change screen in one frame.

Neither upstream FGO-py nor FGA recognizes these screens: both tap them on fixed
delays. These templates and regions were cut from real NA frames captured on
2026-09-22 (``tests/fixtures/order_change``), and every threshold sits well inside
the separation measured on those frames:

- close X on the Order Change screen matches at 1.00, at most 0.29 elsewhere;
- a SELECT marker puts about 1,980 bright-blue pixels above its column, 0 without;
- the Replace button averages 95 grey when disabled and 183 when enabled;
- the palette's first icon matches at 1.00 when open, at most 0.23 when closed.

Only the Chaldea Combat Uniform palette is recognized. Any other mystic code
fails recognition, and every caller then refuses rather than guessing.
"""
from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).parent / "assets" / "order_change"

# Normalized 1280x720 input coordinates, confirmed on the captured frames and
# matching FGA's BattleScreenLocations halved from its 2560x1440 script space.
MASTER_SKILL_BUTTON = (1195, 305)
PALETTE_SKILLS = ((905, 312), (995, 312), (1083, 312))
ORDER_CHANGE_SKILL = 3
MEMBER_COLUMNS = (137, 337, 537, 737, 937, 1137)
MEMBER_Y = 345
REPLACE_BUTTON = (640, 625)
CLOSE_BUTTON = (1222, 137)

_CLOSE_REGION = (1160, 90, 1280, 190)
_ASSAULT_REGION = (860, 265, 955, 360)
_ORDER_CHANGE_REGION = (1035, 265, 1135, 360)
_REPLACE_REGION = (500, 600, 780, 650)
_REPLACE_BRIGHTNESS_REGION = (520, 605, 760, 645)
_NAME_Y = (526, 554)
_MARKER_Y = (150, 245)

_TEMPLATE_MATCH = 0.8
_ICON_MATCH = 0.85
_SELECTED_PIXELS = 800
_REPLACE_ENABLED_BRIGHTNESS = 140


@lru_cache(maxsize=None)
def _template(name: str):
    import cv2
    image = cv2.imread(str(ASSETS / name))
    if image is None:
        raise FileNotFoundError(ASSETS / name)
    return image


def _score(image, name: str, region) -> float:
    import cv2
    x0, y0, x1, y1 = region
    result = cv2.matchTemplate(image[y0:y1, x0:x1], _template(name), cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def read_palette(image) -> dict | None:
    """The open master-skill palette, or None when it is not open.

    ``order_change`` is ``ready``, ``used`` or ``unknown``. A used icon is darkened
    with its own countdown, which also matches the ready template at about 0.70,
    so the state is decided by which template wins clearly, never by one alone.
    """
    if _score(image, "palette_assault_order.png", _ASSAULT_REGION) < _TEMPLATE_MATCH:
        return None
    ready = _score(image, "palette_order_change.png", _ORDER_CHANGE_REGION)
    used = _score(image, "palette_order_change_used.png", _ORDER_CHANGE_REGION)
    state = ("ready" if ready >= _ICON_MATCH and ready > used else
             "used" if used >= _ICON_MATCH and used > ready else "unknown")
    return {"mystic_code": "chaldea_combat_uniform", "order_change": state,
            "scores": {"ready": round(ready, 3), "used": round(used, 3)}}


def normalize_name(text: object) -> str | None:
    """Clean OCR of a member name without inventing letters.

    Seen on real frames: a stray trailing single character ("Koyanskaya of Light y")
    and a dropped closing parenthesis ("Nitocris (Alter").
    """
    # Seen live: glyphs from the portrait edge read as "Oberon+++" and
    # "/Space Ereshkigal". Names never start or end in such punctuation.
    cleaned = re.sub(r"^[^A-Za-z0-9(]+|[^A-Za-z0-9)]+$", "", str(text or ""))
    words = re.sub(r"\s+", " ", cleaned).strip().split(" ")
    # Only a lone LOWERCASE letter is noise: real names end in capitals such as
    # "Mysterious Heroine X", which must survive.
    if len(words) > 1 and len(words[-1]) == 1 and words[-1].islower():
        words = words[:-1]
    name = " ".join(words).strip()
    if name.count("(") == name.count(")") + 1:
        name += ")"
    return name or None


def read_order_change(image, ocr) -> dict | None:
    """The Order Change member screen, or None when it is not showing.

    ``ocr`` takes a BGR crop and returns text. Requires BOTH the close X and the
    Replace label: the skill-target dialog also has a close X in that corner.
    """
    import cv2
    if _score(image, "close_x.png", _CLOSE_REGION) < _TEMPLATE_MATCH:
        return None
    x0, y0, x1, y1 = _REPLACE_REGION
    if "replace" not in str(ocr(image[y0:y1, x0:x1]) or "").lower():
        return None
    members = []
    for index, x in enumerate(MEMBER_COLUMNS):
        region = image[_MARKER_Y[0]:_MARKER_Y[1], x - 38:x + 38].astype(int)
        blue, red = region[..., 0], region[..., 2]
        selected = int(((blue > 170) & (blue - red > 60)).sum()) >= _SELECTED_PIXELS
        name = normalize_name(ocr(image[_NAME_Y[0]:_NAME_Y[1], x - 97:x + 97]))
        members.append({"position": index + 1, "group": "starting" if index < 3 else "sub",
                        "slot": index + 1 if index < 3 else index - 2,
                        "name": name, "selected": selected})
    bx0, by0, bx1, by1 = _REPLACE_BRIGHTNESS_REGION
    brightness = float(cv2.cvtColor(image[by0:by1, bx0:bx1], cv2.COLOR_BGR2GRAY).mean())
    return {"members": members, "replace_enabled": brightness >= _REPLACE_ENABLED_BRIGHTNESS}


def resolve_servant_id(name: str | None, index: dict[int, dict]) -> int | None:
    """A collection number for a displayed name, only when it is unambiguous.

    Several servants share a display name across classes (Altria Caster is both
    284 and 386), and some in-game names differ from the reference name. Either
    case returns None instead of a guess.
    """
    if not name or not index:
        return None
    folded = name.casefold()
    exact = [ident for ident, row in index.items() if str(row.get("name", "")).casefold() == folded]
    if exact:
        return exact[0] if len(exact) == 1 else None
    names = {ident: str(row.get("name", "")).casefold() for ident, row in index.items()}
    close = difflib.get_close_matches(folded, list(set(names.values())), n=2, cutoff=0.88)
    if len(close) != 1:
        return None
    matches = [ident for ident, value in names.items() if value == close[0]]
    return matches[0] if len(matches) == 1 else None


def names_match(observed: str | None, expected: str | None) -> bool:
    """Same member despite OCR noise: normalized equality, or a very close match."""
    a, b = normalize_name(observed), normalize_name(expected)
    if not a or not b:
        return False
    a, b = a.casefold(), b.casefold()
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9:
        return True
    # Seen live: one member read "OberonW" in the reconnaissance and "Oberon XX" on
    # the member screen (ascension marks at the name's edge), and the swap backed
    # out. Accept one shared run of 5+ letters when each reading adds at most 3
    # stray characters to it; "Arjuna" vs "Arjuna (Alter)" still differ.
    a, b = re.sub(r"[^0-9a-z]", "", a), re.sub(r"[^0-9a-z]", "", b)
    match = difflib.SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return match.size >= 5 and len(a) - match.size <= 3 and len(b) - match.size <= 3
