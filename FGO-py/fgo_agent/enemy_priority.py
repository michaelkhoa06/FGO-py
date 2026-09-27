"""Observed FGA English enemy-priority labels for the NA three-enemy HUD.

Labels are context for the planner only.  They never create an action, select a
target, or make an absent/unknown enemy actionable.
"""
from __future__ import annotations

from pathlib import Path


_TEMPLATE_NAMES = ("danger", "servant")
_SCORE_THRESHOLD = 0.88
# FGA's 1440p script regions have height 220, converted to 110 at 720p.
# Horizontal boundaries use this observer's established 250px standard HUD slots.
_SLOT_REGIONS = {slot: ((slot - 1) * 250, 0, slot * 250, 110) for slot in (1, 2, 3)}


def _templates():
    import cv2

    directory = Path(__file__).parent / "assets" / "fga-en"
    return {
        name: cv2.imread(str(directory / f"target_{name}.png"), cv2.IMREAD_GRAYSCALE)
        for name in _TEMPLATE_NAMES
    }


def observe_priority_labels(image, enemies, *, grid):
    """Attach optional, score-backed labels to known living standard-three enemies.

    A missed template is unknown, never evidence that an enemy is ordinary.
    """
    import cv2

    templates = _templates()
    observed = []
    for enemy in enemies:
        item = dict(enemy)
        slot = item.get("slot")
        valid = (grid == 0 and image.shape[:2] == (720, 1280)
                 and type(slot) is int and slot in _SLOT_REGIONS
                 and item.get("present") is True
                 and type(item.get("hp")) is int and item["hp"] > 0)
        scores = {}
        if valid:
            x1, y1, x2, y2 = _SLOT_REGIONS[slot]
            crop = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            for name, template in templates.items():
                if template is not None and template.shape[0] <= crop.shape[0] and template.shape[1] <= crop.shape[1]:
                    scores[name] = float(cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1])
        matches = [name for name in _TEMPLATE_NAMES if scores.get(name, -1.0) >= _SCORE_THRESHOLD]
        label = max(matches, key=scores.get) if matches else None
        item["priority_label"] = label
        item["priority_label_evidence"] = {
            "source": "FGA En target_danger.png/target_servant.png exact template match",
            "score_threshold": _SCORE_THRESHOLD,
            "scores": scores,
            "eligible": valid,
        }
        observed.append(item)
    return observed
