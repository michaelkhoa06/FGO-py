"""Recognize and read the battle Status Changes dialogs in one frame.

Two read-only dialogs list active status effects with their in-game wording:

- Enemy: long-pressing an enemy's class icon opens its sub-attribute and effects
  (boss gimmicks, triggers on defeat or damage, revives, charge gain).
- Ally: tapping a field servant's portrait opens a two-tab dialog. Its Status tab
  lists seals, buffs and debuffs with remaining turns; the header carries the
  servant's name and real ATK. The dialog remembers the last tab, so it can open
  on Details and needs a tap on Status.

Neither upstream FGO-py nor FGA opens these dialogs. Templates and regions come
from real NA frames captured on 2026-09-23 (``tests/fixtures/enemy_profile``):
the header and close X match those dialogs at 0.98 or more and at most 0.43 on
382 other saved battle frames, so the 0.8 threshold sits far inside the gap; the
active tab is about 66% saturated blue and the inactive one 0%. Opening, reading
and closing did not change the selected target on those frames.

The text is OCR: close to the game's wording, but not exact. Callers must present
it as evidence, never as authoritative numbers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).parent / "assets" / "enemy_profile"

# Class icons of the standard three-enemy row; single bosses use the same spots.
ENEMY_ICONS = ((42, 40), (292, 40), (542, 40))
LONG_PRESS_MS = 1200
CLOSE_BUTTON = (1058, 117)
# Ally dialog: portrait taps, its close X and its Status tab.
ALLY_PORTRAITS = ((160, 450), (478, 450), (796, 450))
ALLY_CLOSE_BUTTON = (1087, 64)
ALLY_STATUS_TAB = (567, 70)

# Dragging a long list up by about two entries, slowly enough not to fling it.
SCROLL_SWIPES = {"enemy": ((680, 540), (680, 330)), "ally": ((680, 610), (680, 330))}
SCROLL_MS = 700

_HEADER_SEARCH = (480, 90, 800, 240)
_CLOSE_SEARCH = (1000, 20, 1130, 180)
_ALLY_CLOSE_MAX_Y = 90
_TABS = {"details": (235, 55, 400, 88), "status": (485, 55, 650, 88)}
_ACTIVE_TAB_BLUE = 0.3
_NAME_REGION = (240, 100, 700, 132)
_ATTRIBUTE_REGION = (197, 138, 900, 164)
_ALLY_NAME_REGION = (700, 30, 1052, 62)
_ALLY_ATK_REGION = (960, 64, 1052, 96)
# Stops short of the scrollbar (x~1060) that long lists show; it made every row look like text.
_TEXT_X = (330, 1045)
_TEXT_BOTTOM = {"enemy": 600, "ally": 690}
_DURATION_MIN_X = 560  # relative to the text region: the right-hand "N Turn(s)" column
_ICON_COLUMN = (258, 318)
# The list's scrollbar thumb: a long bright bar only when the list overflows.
# Measured: absent on lists that fit; 238-470 of a 605 track on a long enemy list.
_SCROLLBAR_X = (1060, 1084)
_SCROLL_TRACK_BOTTOM = {"enemy": 605, "ally": 675}
_SCROLL_END_MARGIN = 25
_TEMPLATE_MATCH = 0.8
# Words on a line sit a few pixels apart; background labels that bleed through
# the translucent panel sit much further away and are cut off here.
_WORD_GAP = 30


@lru_cache(maxsize=None)
def _template(name: str):
    import cv2
    image = cv2.imread(str(ASSETS / name))
    if image is None:
        raise FileNotFoundError(ASSETS / name)
    return image


def _find(image, name: str, region) -> tuple[float, tuple[int, int]]:
    """Best match score and the matched template's centre in frame coordinates."""
    import cv2
    x0, y0, x1, y1 = region
    template = _template(name)
    result = cv2.matchTemplate(image[y0:y1, x0:x1], template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    return float(score), (x0 + x + template.shape[1] // 2, y0 + y + template.shape[0] // 2)


def _blue_fraction(image, region) -> float:
    import cv2
    x0, y0, x1, y1 = region
    hsv = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 0] > 95) & (hsv[..., 0] < 125) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).mean())


def _dialog(image) -> dict | None:
    """Which status dialog is open, where its close X is and, for allies, the tab."""
    close_score, close = _find(image, "close_x.png", _CLOSE_SEARCH)
    if close_score < _TEMPLATE_MATCH:
        return None
    header_score, header = _find(image, "status_header.png", _HEADER_SEARCH)
    if close[1] < _ALLY_CLOSE_MAX_Y:
        tabs = {name: _blue_fraction(image, region) for name, region in _TABS.items()}
        active = [name for name, value in tabs.items() if value >= _ACTIVE_TAB_BLUE]
        if len(active) != 1:
            return None
        if active[0] == "status" and header_score < _TEMPLATE_MATCH:
            return None
        return {"subject": "ally", "tab": active[0], "close": close, "header_y": header[1]}
    if header_score < _TEMPLATE_MATCH:
        return None
    return {"subject": "enemy", "tab": "status", "close": close, "header_y": header[1]}


def is_profile_dialog(image) -> bool:
    found = _dialog(image)
    return found is not None and found["subject"] == "enemy"


def _white_text(image):
    import cv2
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return (hsv[..., 2] > 185) & (hsv[..., 1] < 50)


def _runs(flags, minimum_gap=1):
    """(start, end) runs of truthy entries, merging gaps shorter than ``minimum_gap``."""
    runs, start, gap = [], None, 0
    for index, flag in enumerate(list(flags) + [False] * minimum_gap):
        if flag:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= minimum_gap:
                runs.append((start, index - gap + 1))
                start, gap = None, 0
    return runs


def _ocr(image, y0, y1, x0, x1, ocr_text):
    crop = image[max(0, y0 - 3):y1 + 3, max(0, x0 - 4):x1 + 4]
    # Seen live: an empty crop crashed the OCR model and lost the whole dialog.
    # One unreadable line must only blank that line.
    if crop.shape[0] < 4 or crop.shape[1] < 4:
        return ""
    try:
        return str(ocr_text(crop)).strip()
    except Exception:
        return ""


def _line(image, mask, y0, y1, x_offset, ocr_text):
    """(text, duration) of one line: the first word cluster, and a right-column one."""
    columns = mask[y0:y1].sum(axis=0) > 0
    clusters = _runs(columns, _WORD_GAP)
    if not clusters:
        return "", None
    left, right = clusters[0]
    text = "" if left >= _DURATION_MIN_X else _ocr(image, y0, y1, x_offset + left, x_offset + right, ocr_text)
    duration = next((_ocr(image, y0, y1, x_offset + a, x_offset + b, ocr_text)
                     for a, b in clusters if a >= _DURATION_MIN_X), None)
    return text, duration or None


def _field(image, region, ocr_text):
    x0, y0, x1, y1 = region
    mask = _white_text(image[y0:y1, x0:x1])
    text, _ = _line(image[y0:y1], mask, 0, y1 - y0, x0, ocr_text)
    return text or None


def _scrollbar(image, dialog) -> dict:
    import cv2
    y0, y1 = dialog["header_y"] + 35, _SCROLL_TRACK_BOTTOM[dialog["subject"]]
    hsv = cv2.cvtColor(image[y0:y1, _SCROLLBAR_X[0]:_SCROLLBAR_X[1]], cv2.COLOR_BGR2HSV)
    bright = ((hsv[..., 2] > 200) & (hsv[..., 1] < 70)).sum(axis=1) >= 8
    thumbs = [(a, b) for a, b in _runs(bright, 1) if b - a >= 20]
    if not thumbs:
        return {"scrollable": False, "at_end": True, "thumb": None}
    top, bottom = max(thumbs, key=lambda run: run[1] - run[0])
    return {"scrollable": True, "at_end": y0 + bottom >= y1 - _SCROLL_END_MARGIN,
            "thumb": [y0 + top, y0 + bottom]}


def merge_effects(known: list[dict], seen: list[dict]) -> list[dict]:
    """Add effects from a scrolled view, skipping repeats and cut-off fragments.

    After a scroll the top entry can be cut, so its "title" is really the tail of
    an effect already read. Anything whose words already appear is dropped.
    """
    import difflib
    merged = [dict(e) for e in known]
    corpus = " ".join(f"{e.get('title', '')} {e.get('text', '')}" for e in merged).lower()
    for effect in seen:
        title, text = str(effect.get("title", "")).strip(), str(effect.get("text", "")).strip()
        if not title and not text:
            continue
        if title and any(difflib.SequenceMatcher(None, title.lower(), str(e.get("title", "")).lower()).ratio() >= 0.8
                         for e in merged):
            continue
        if (title and title.lower() in corpus) and (not text or text.lower() in corpus):
            continue
        if not text and title and title.lower() in corpus:
            continue
        merged.append(dict(effect))
        corpus += f" {title} {text}".lower()
    return merged


def _effects(image, dialog, ocr_text):
    import cv2
    x0, x1 = _TEXT_X
    y0, y1 = dialog["header_y"] + 35, _TEXT_BOTTOM[dialog["subject"]]
    region = image[y0:y1, x0:x1]
    mask = _white_text(region)
    # A row touching the top edge is cut off by the header after a scroll (seen live:
    # "Damage Cut" read as "Damage cui"), so it is left to the view that shows it whole.
    lines = [(a, b) for a, b in _runs(mask.sum(axis=1) > 2, 2) if b - a >= 6 and a > 2]
    # Status icons are saturated squares left of the text; each starts an entry.
    hsv = cv2.cvtColor(image[y0:y1, _ICON_COLUMN[0]:_ICON_COLUMN[1]], cv2.COLOR_BGR2HSV)
    icon_rows = ((hsv[..., 1] > 120) & (hsv[..., 2] > 150)).sum(axis=1) > 3
    icon_tops = [a for a, b in _runs(icon_rows, 4) if b - a >= 35]
    effects: list[dict] = []
    for a, b in lines:
        text, duration = _line(image[y0:y1], mask, a, b, x0, ocr_text)
        if not text and not duration:
            continue
        # An entry starts at its icon; in the ally list its title line also carries
        # the remaining turns on the right, which is the steadier signal there.
        starts_entry = duration is not None or any(top - 6 <= a <= top + 14 for top in icon_tops)
        if starts_entry or not effects:
            effects.append({"title": text, "text": "", "duration": duration})
        else:
            effects[-1]["text"] = (effects[-1]["text"] + " " + text).strip()
    scroll = _scrollbar(image, dialog)
    truncated = (scroll["scrollable"] and not scroll["at_end"]) or (bool(lines) and lines[-1][1] > (y1 - y0) - 15)
    return effects, truncated, scroll


def read_status_dialog(image, ocr_text) -> dict | None:
    """The open enemy or ally status dialog, or None when neither is open."""
    dialog = _dialog(image)
    if dialog is None:
        return None
    result = {"subject": dialog["subject"], "tab": dialog["tab"], "close": list(dialog["close"])}
    if dialog["subject"] == "ally":
        atk = _field(image, _ALLY_ATK_REGION, ocr_text)
        result.update(name=_field(image, _ALLY_NAME_REGION, ocr_text),
                      atk=int(atk.replace(",", "")) if atk and atk.replace(",", "").isdigit() else None)
    else:
        result.update(name=_field(image, _NAME_REGION, ocr_text),
                      sub_attribute=_field(image, _ATTRIBUTE_REGION, ocr_text))
    if dialog["tab"] == "status":
        result["effects"], result["possibly_truncated"], result["scroll"] = _effects(image, dialog, ocr_text)
    return result


def read_profile(image, ocr_text) -> dict | None:
    """The open ENEMY Status Changes dialog, or None (kept for existing callers)."""
    found = read_status_dialog(image, ocr_text)
    if found is None or found["subject"] != "enemy":
        return None
    return {key: found[key] for key in ("name", "sub_attribute", "effects", "possibly_truncated", "scroll")}
