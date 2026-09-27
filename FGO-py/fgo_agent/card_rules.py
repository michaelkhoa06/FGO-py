"""Which command cards may be chosen on the card screen.

A sealed card cannot attack, but the game still lets it be selected. Seen live on
2026-09-23: a story fight sealed every card type and every NP for two turns, the
card screen showed five sealed cards, and tapping one selected it ("1st Attack").
Picking sealed cards is then the only way to pass the turn and wait the seal out,
so sealed face cards are allowed only when fewer than three usable cards exist,
and only to fill the chain.

An NP under NP Seal must never be picked: it spends the gauge for nothing. The
same fight showed a sealed NP card read as ready on one frame, so an NP Seal read
from the ally's Status tab (``ally_statuses``) outranks the card image.
"""
from __future__ import annotations

import re

FACE_TYPES = {"arts", "quick", "buster"}


def usable(card: dict) -> bool:
    kind = str(card.get("type", "")).lower()
    if card.get("sealed") is not False:
        return False
    return kind in FACE_TYPES or (kind == "np" and card.get("ready") is True)


def sealed_face(card: dict) -> bool:
    # A stunned servant's cards are only a last resort (see selectable_slots).
    return (card.get("sealed") is True and card.get("stunned") is not True
            and str(card.get("type", "")).lower() in FACE_TYPES and card.get("slot") in range(1, 6))


def np_sealed_allies(signals: dict | None, turn: int | None) -> set[int]:
    """Field slots whose NP Seal, read from their Status tab, has not yet expired.

    A duration of N turns read on turn R lasts through turn R+N-1. An unreadable
    duration counts as still active: using a sealed NP wastes it.
    """
    sealed = set()
    for status in (signals or {}).get("ally_statuses") or []:
        if not isinstance(status, dict) or status.get("slot") not in (1, 2, 3):
            continue
        read_turn = (status.get("read_at") or {}).get("turn")
        for effect in status.get("effects") or []:
            if not isinstance(effect, dict) or "np seal" not in str(effect.get("title", "")).lower():
                continue
            match = re.search(r"(\d+)", str(effect.get("duration") or ""))
            if (match is None or type(read_turn) is not int or type(turn) is not int
                    or turn < read_turn + int(match.group(1))):
                sealed.add(status["slot"])
    return sealed


# Effects that stop a servant acting at all. Only Stun has an NA card overlay we
# recognize; Charm, Sleep, Petrify, Pigify and Terror's stuns (Avalon le Fae's
# Transhuman Duke, 2026-09-24 survey) are known from the ally's Status tab.
_INCAPACITATING = re.compile(r"stun|charm|sleep|petrif|pigif|paraly|immobil|unable to act", re.IGNORECASE)


def _active_titles(signals: dict | None, turn: int | None, pattern) -> set[int]:
    """Field slots with a matching Status-tab effect that has not yet expired.

    A duration of N turns read on turn R lasts through turn R+N-1. An unreadable
    duration counts as still active: acting on it wrongly wastes the turn.
    """
    active = set()
    for status in (signals or {}).get("ally_statuses") or []:
        if not isinstance(status, dict) or status.get("slot") not in (1, 2, 3):
            continue
        read_turn = (status.get("read_at") or {}).get("turn")
        for effect in status.get("effects") or []:
            if not isinstance(effect, dict) or not pattern(str(effect.get("title", ""))):
                continue
            match = re.search(r"(\d+)", str(effect.get("duration") or ""))
            if (match is None or type(read_turn) is not int or type(turn) is not int
                    or turn < read_turn + int(match.group(1))):
                active.add(status["slot"])
    return active


# Buffs that merely name such an effect ("Stun Resist Up", "Charm Immunity").
_PROTECTIVE = re.compile(r"resist|immun|up|success|nullif|removal", re.IGNORECASE)


def incapacitated_allies(signals: dict | None, turn: int | None) -> set[int]:
    """Field slots that cannot act this turn (stun, charm, sleep, petrify, ...)."""
    return _active_titles(signals, turn, lambda title: bool(_INCAPACITATING.search(title))
                          and not _PROTECTIVE.search(title))


def selectable_slots(cards: list[dict], signals: dict | None = None, turn: int | None = None) -> list[int]:
    """Usable card slots, plus sealed face cards when too few usable ones remain."""
    blocked = {5 + slot for slot in np_sealed_allies(signals, turn)}
    down = incapacitated_allies(signals, turn)
    blocked |= {5 + slot for slot in down}
    blocked |= {c.get("slot") for c in cards if c.get("servant") in down and c.get("slot") in range(1, 6)}
    slots = sorted({c.get("slot") for c in cards if usable(c) and c.get("slot") in range(1, 9)} - blocked)
    if len(slots) < 3:
        slots += sorted({c.get("slot") for c in cards if sealed_face(c)} - set(slots))
    if len(slots) < 3:
        # Seen live 2026-09-27 (Black Wolf): with Draco and Arjuna stunned, Mash's
        # Arts card and NP were picked and the game waited for a third card. FGA
        # taps stunned cards last to fill the chain; they are fillers only.
        slots += sorted({c.get("slot") for c in cards if c.get("slot") in range(1, 6)
                         and str(c.get("type", "")).lower() in FACE_TYPES
                         and (c.get("stunned") is True or c.get("servant") in down)} - set(slots))
    return slots


def filler_slots(cards: list[dict], signals: dict | None = None, turn: int | None = None) -> set[int]:
    """Selectable slots that only fill a chain (sealed or stunned); tap them last."""
    return {slot for slot in selectable_slots(cards, signals, turn)
            if not usable(next((c for c in cards if c.get("slot") == slot), {}))
            or next((c for c in cards if c.get("slot") == slot), {}).get("stunned") is True}


def chain_length(cards: list[dict], signals: dict | None = None, turn: int | None = None) -> int:
    """Cards to pick this turn: three, or every selectable card when fewer remain.

    Stunned cards now fill a short hand, so this is three whenever all five face
    cards were read; it stays short only for a hand with nothing left to fill.
    """
    selectable = selectable_slots(cards, signals, turn)
    if len(selectable) >= 3:
        return 3
    # Short only when all five face cards were read and the rest are observed
    # stunned or belong to an incapacitated ally; a partial read stays at three.
    down = incapacitated_allies(signals, turn)
    faces = {c.get("slot"): c for c in cards if c.get("slot") in range(1, 6) and c.get("type") is not None}
    if len(faces) == 5 and all(slot in selectable or card.get("stunned") is True or card.get("servant") in down
                               for slot, card in faces.items()):
        return len(selectable)
    return 3
