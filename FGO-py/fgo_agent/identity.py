"""Identify a field servant from its battle name label when the portrait fails.

The portrait match only compares servants of the class the icon shows, using
FGO-py's class for each servant. Story guests break that: live on 2026-09-23 a
guest Senji Muramasa showed an Alter Ego icon while FGO-py lists #302 as a Saber,
so no portrait could match and the ally, with every skill, was dropped from the
AI's options. The name under the NP bar is read instead and resolved against the
NA servant index, using the class icon only to break ties.

Battle labels are short forms ("Da Vinci" for "Leonardo da Vinci", "Altria" for
"Altria Caster"), and OCR can drop trailing letters, so a label matches a name
that contains it from a word start. Anything ambiguous stays unidentified.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# FGO-py class numbers (``servantData[id][0]`` and the class icon) to Atlas class
# names, derived from every servant present in both on 2026-09-23 with no conflict.
FGOPY_CLASS_NAMES = {0: "shielder", 1: "saber", 2: "archer", 3: "lancer", 4: "rider", 5: "caster",
                     6: "assassin", 7: "berserker", 8: "ruler", 9: "avenger", 10: "alterEgo",
                     11: "moonCancer", 12: "foreigner", 13: "pretender"}

NAME_LABEL_REGION = (158, 693, 332, 718)
_SLOT_STRIDE = 318


def name_label_region(slot_index: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = NAME_LABEL_REGION
    return x0 + _SLOT_STRIDE * slot_index, y0, x1 + _SLOT_STRIDE * slot_index, y1


def fold(text: object) -> str:
    """Lowercase ASCII words: accents and punctuation removed."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def resolve_battle_name(label: str | None, class_name: str | None,
                        index: dict[int, dict]) -> tuple[int | None, str | None]:
    """(collection number, how it was resolved), or (None, None) when not unique."""
    name = fold(label)
    if len(name) < 3 or not index:
        return None, None
    exact, contained = [], []
    for ident, row in index.items():
        full = fold(row.get("name"))
        if not full:
            continue
        if full == name:
            exact.append(ident)
        # Word boundaries: "Rama" must not match inside "Senji Muramas".
        elif (re.search(rf"(?:^| ){re.escape(name)}", full)
              or re.search(rf"(?:^| ){re.escape(full)}(?: |$)", name)
              or difflib.SequenceMatcher(None, name, full).ratio() >= 0.9):
            contained.append(ident)
    for candidates in (exact, contained):
        if not candidates:
            continue
        same_class = [i for i in candidates if index[i].get("class_name") == class_name]
        if len(same_class) == 1:
            return same_class[0], "name_label_and_class"
        if not same_class and len(candidates) == 1:
            return candidates[0], "name_label_class_differs"
        return None, None
    return None, None
