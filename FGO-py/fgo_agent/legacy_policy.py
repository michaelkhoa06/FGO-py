"""Observation-only adaptation of FGO-py's original :class:`Turn` policy.

The legacy turn loop mixed tactical decisions with device calls.  This module keeps
the decision rules, but only returns controller actions backed by an Observation.
"""
from __future__ import annotations

from itertools import permutations

from .models import Action, ActionResult, Observation


class LegacyPolicy:
    """A conservative adapter for the metadata policy used by ``fgoKernel.Turn``."""

    name = "legacy-fgo-py-turn"

    def __init__(self, servant_data: dict):
        self.servant_data = servant_data
        self.last_decision: dict = {}
        self._latest_allies: list[dict] = []
        self._pending: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        self._confirmed: set[tuple[int, int, int, int]] = set()

    def next_action(self, observation: Observation) -> Action:
        """Return one validated candidate action for the supplied screen state."""
        if observation.allies:
            self._latest_allies = list(observation.allies)
        if observation.phase == "tactical":
            return self._tactical(observation)
        if observation.phase == "cards":
            return self._cards(observation)
        return self._decision(observation, Action("attack", reason="unknown phase"),
                              ["unknown phase; conservative attack fallback"], ["unknown_phase"])

    def observe_result(self, action: Action, result: ActionResult) -> None:
        """Remember a skill only after the controller has confirmed consumption."""
        if action.kind != "skill" or action.servant is None or action.skill is None:
            return
        pending = self._pending.pop((action.servant, action.skill), None)
        if pending and result.skill_consumed is True:
            self._confirmed.add(pending)

    def _tactical(self, observation: Observation) -> Action:
        reasons, deviations = [], []
        key_prefix = self._turn_key(observation)
        if key_prefix is None:
            deviations.append("wave_or_turn_unknown: skill use deferred")
        else:
            for ally in self._live_allies(observation):
                servant, slot = ally.get("id"), ally.get("slot")
                if not isinstance(servant, int) or slot not in (1, 2, 3):
                    continue
                metadata = self.servant_data.get(servant)
                if not self._skills(metadata):
                    continue
                for skill in ally.get("skills", []):
                    skill_slot = skill.get("slot")
                    if skill_slot not in (1, 2, 3) or skill.get("ready") is not True:
                        continue
                    identity = (*key_prefix, slot, skill_slot)
                    if identity in self._confirmed:
                        continue
                    effect = self._skills(metadata)[skill_slot - 1]
                    action, why, deviation = self._skill_action(observation, ally, effect, skill_slot)
                    if deviation:
                        deviations.append(deviation)
                    if action is not None:
                        self._pending[(slot, skill_slot)] = identity
                        reasons.append(why)
                        return self._decision(observation, action, reasons, deviations)
        reasons.append("no observably ready legacy-eligible skill")
        return self._decision(observation, Action("attack", reason="; ".join(reasons)), reasons, deviations)

    def _skill_action(self, observation, ally, effect, skill_slot):
        if not isinstance(effect, (tuple, list)) or len(effect) < 2:
            return None, "", "unknown skill metadata skipped"
        effect_class, mode = effect[0], effect[1]
        if mode in {6, 7, 8}:
            return None, "", "special-dialog metadata mode skipped"
        if effect_class == 1:
            return self._skill(ally, skill_slot, ally.get("slot")), "effect class 1 self target", None
        if effect_class in {4, 5, 6}:
            target = self._target_for(mode, ally, observation)
            return self._skill(ally, skill_slot, target), self._skill_reason(effect_class, mode, target), None
        allies = self._live_allies(observation)
        if effect_class in {2, 3}:
            values = [(a.get("np"), a) for a in allies if isinstance(a.get("np"), (int, float))]
            if not values:
                return None, "", "NP state unknown; skill skipped"
            if effect_class == 2:
                below_full = [item for item in values if item[0] < 100]
                if mode == 1:
                    if not below_full:
                        return None, "", ""
                    target = min(below_full, key=lambda item: item[0])[1]
                elif mode == 2:
                    if not any(item[0] < 100 and item[1].get("slot") != ally.get("slot") for item in values):
                        return None, "", ""
                    target = None
                elif mode in {3, 4}:
                    stage_turn = observation.signals.get("stage_turn")
                    if not isinstance(stage_turn, int) or stage_turn <= 1:
                        return None, "", "stage_turn unknown or first stage turn; skill skipped"
                    target = None
                elif mode == 5:
                    if not isinstance(ally.get("np"), (int, float)) or ally["np"] >= 100:
                        return None, "", ""
                    target = ally
                elif mode == 0:
                    if not below_full:
                        return None, "", ""
                    target = None
                else:
                    target = None
            else:
                full = [item for item in values if item[0] >= 100]
                if mode == 1:
                    if not full:
                        return None, "", ""
                    target = max(full, key=lambda item: item[0])[1]
                elif mode == 2:
                    if not any(item[0] >= 100 and item[1].get("slot") != ally.get("slot") for item in values):
                        return None, "", ""
                    target = None
                elif mode in {0, 3, 4}:
                    if not full:
                        return None, "", ""
                    target = None
                elif mode == 5:
                    if not isinstance(ally.get("np"), (int, float)) or ally["np"] < 100:
                        return None, "", ""
                    target = ally
                else:
                    target = None
            final_target = self._target_for(mode, ally, observation, target)
            return self._skill(ally, skill_slot, final_target), self._skill_reason(effect_class, mode, final_target, "NP gate"), None
        if effect_class == 7:
            values = [(a.get("hp"), a) for a in allies if isinstance(a.get("hp"), (int, float))]
            low = [item for item in values if item[0] < 6600]
            if mode == 1:
                if not low:
                    return None, "", "HP state unknown or threshold not met; skill skipped"
                target = min(low, key=lambda item: item[0])[1]
            elif mode == 2:
                if not any(item[0] < 6600 and item[1].get("slot") != ally.get("slot") for item in values):
                    return None, "", "HP state unknown or threshold not met; skill skipped"
                target = None
            elif mode in {3, 4}:
                target = None
            elif mode == 5:
                if not isinstance(ally.get("hp"), (int, float)) or ally["hp"] >= 6600:
                    return None, "", "HP state unknown or threshold not met; skill skipped"
                target = ally
            elif mode == 0:
                if not low:
                    return None, "", "HP state unknown or threshold not met; skill skipped"
                target = None
            else:
                target = None
            final_target = self._target_for(mode, ally, observation, target)
            return self._skill(ally, skill_slot, final_target), self._skill_reason(7, mode, final_target, "HP gate"), None
        if effect_class in {8, 9}:
            threatening = any(self._enemy_charge_full(e.get("charge")) for e in observation.enemies)
            low_hp = isinstance(ally.get("hp"), (int, float)) and ally["hp"] < 3300
            if threatening or (effect_class == 9 and low_hp):
                return self._skill(ally, skill_slot, ally.get("slot")), f"effect class {effect_class} threat gate", None
            return None, "", "enemy NP state unknown or threshold not met; skill skipped"
        return None, "", "unknown effect class skipped"

    def _target_for(self, mode, actor, observation, selected=None):
        if mode == 5:
            return actor.get("slot")
        if mode == 1 and selected is not None:
            return selected.get("slot")
        # Legacy target 0 is the dialog's middle/left/only living target.  A supplied
        # target is ignored when no dialog appears, and makes a recognized dialog safe.
        living = sorted(a.get("slot") for a in self._live_allies(observation) if a.get("slot") in (1, 2, 3))
        return living[(len(living) - 1) // 2] if living else None

    @staticmethod
    def _skill_reason(effect_class, mode, target, suffix=""):
        reason = f"effect class {effect_class}" + (f" {suffix}" if suffix else "")
        if mode not in {1, 5} and target is not None:
            return f"{reason}; legacy generic target fallback slot {target}"
        if mode == 1 and target is not None and effect_class in {4, 5, 6}:
            return f"{reason}; legacy generic target fallback slot {target}"
        return reason

    def _skill(self, ally, skill_slot, target):
        return Action("skill", servant=ally["slot"], skill=skill_slot, target=target,
                      reason="legacy metadata policy")

    @staticmethod
    def _enemy_charge_full(charge):
        return (isinstance(charge, (tuple, list)) and len(charge) == 2
                and all(isinstance(value, (int, float)) for value in charge)
                and charge[1] > 0 and charge[0] == charge[1])

    def _cards(self, observation: Observation) -> Action:
        cards = {c.get("slot"): c for c in observation.cards if c.get("slot") in range(1, 9)}
        valid_faces = [slot for slot in range(1, 6) if self._face_valid(cards.get(slot))]
        np_slots = self._eligible_nps(cards, observation)
        nps = self._choose_nps(np_slots, observation)
        needed = 3 - len(nps)
        if needed < 0:
            nps, needed = nps[:3], 0
        missing_ownership = any(cards[s].get("servant") not in (1, 2, 3) for s in valid_faces)
        if len(valid_faces) < needed:
            available = [slot for slot in range(1, 9) if self._valid_card(cards.get(slot))]
            chosen = (nps + [s for s in available if s not in nps])[:3]
            return self._decision(observation, Action("cards", cards=tuple(chosen), reason="conservative valid cards"),
                                  ["insufficient known face cards; selected valid cards deterministically"], ["card_scoring_fallback"])
        selected = list(nps) + list(max(permutations(valid_faces, needed), key=lambda face: self._score(nps + list(face), cards, observation)))
        deviations = ["card ownership unavailable; visual-group scoring used"] if missing_ownership else []
        return self._decision(observation, Action("cards", cards=tuple(selected), reason="legacy card scorer"),
                              ["legacy permutation card score"], deviations)

    def _choose_nps(self, slots, observation):
        enemies = self._enemy_hps(observation)
        final = self._is_final_wave(observation)
        support, area, targeted = [], [], []
        for slot in slots:
            kind = self._np_kind(slot, observation)
            if kind == 2:
                support.append(slot)
            elif kind == 1 and (final or (sum(h > 0 for h in enemies) > 1 and sum(enemies) > 12000)):
                area.append(slot)
            elif kind == 0 and (final or (max(enemies, default=0) > 23000 + 8000 * len(area))):
                targeted.append(slot)
        return support + area + targeted

    def _score(self, selected, cards, observation):
        faces = [s for s in selected if s <= 5]
        if not faces:
            return 0.0
        color = {"arts": 0, "quick": 1, "buster": 2}
        types = [self._card_color(s, cards, observation, color) for s in selected]
        groups = [self._group(cards[s]) for s in selected]
        affinity = {"neutral": 1.0, "weak": 1.7, "resist": .6}
        resist = [affinity.get(str(cards[s].get("affinity", "neutral")).lower(), 1.0) for s in selected]
        crit = [self._number(cards[s].get("critical_rate")) / 100 for s in selected]
        chain_error = any(cards[s].get("sealed") is not False for s in faces)
        face_types = tuple(sorted(set(types[i] for i, s in enumerate(selected) if s <= 5)))
        color_chain = -1 if chain_error else {(0,): 0, (1,): 1, (2,): 2, (0, 1, 2): 3}.get(face_types, -1)
        first_bonus = 7 if color_chain == 3 else (1 << types[0] if types[0] >= 0 else 0)
        base = sum(((.3 * bool(first_bonus & 4) + .1 * bool(first_bonus & 1) + [1., 1.2, 1.4][i] * [1, .8, 1.1][types[j]]) *
                    (1 + min(1, crit[j] + .2 * bool(first_bonus & 2))) + bool(color_chain == 2)) * resist[j]
                   for i, j in enumerate(range(len(selected))) if selected[j] <= 5 and types[j] >= 0)
        enemy = self._enemy_hps(observation)
        target_hp = self._target_hp(observation, enemy)
        base += 4 * (sum(h > 0 for h in enemy) > 1) * (target_hp < 20000) * sum(groups[i] != groups[i + 1] for i in range(len(groups) - 1))
        base += (1.8 if color_chain == -1 else 3) * (not chain_error and len(set(groups)) == 1) * resist[0]
        base += 2.3 * (color_chain == 0) * len({groups[i] for i, s in enumerate(selected) if s <= 5 and self._np_below_full(cards[s], observation)})
        return base + 3 * (color_chain == 1)

    def _eligible_nps(self, cards, observation):
        return [s for s in range(6, 9) if self._valid_card(cards.get(s)) and str(cards[s].get("type")).lower() == "np"
                and cards[s].get("ready") is True and self._np_kind(s, observation) in {0, 1, 2}]

    def _np_kind(self, slot, observation):
        card = next((c for c in observation.cards if c.get("slot") == slot), {})
        ally = self._ally_for_slot(self._field_slot(card), observation)
        data = self.servant_data.get(ally.get("id")) if ally else None
        return data[4][0] if isinstance(data, (tuple, list)) and len(data) > 4 and isinstance(data[4], (tuple, list)) else None

    def _card_color(self, slot, cards, observation, colors):
        card = cards[slot]
        if str(card.get("type", "")).lower() != "np":
            return colors.get(str(card.get("type", "")).lower(), -1)
        ally = self._ally_for_slot(self._field_slot(card), observation)
        data = self.servant_data.get(ally.get("id")) if ally else None
        if isinstance(data, (tuple, list)) and len(data) > 4 and isinstance(data[4], (tuple, list)) and len(data[4]) > 1:
            return data[4][1] if data[4][1] in {0, 1, 2} else -1
        return -1

    def _is_final_wave(self, observation):
        total = observation.signals.get("stage_total")
        return isinstance(observation.wave, int) and isinstance(total, int) and observation.wave == total

    def _live_allies(self, observation):
        allies = observation.allies or self._latest_allies
        return [a for a in allies if a.get("slot") in (1, 2, 3) and a.get("hp") is not None and a.get("hp") > 0]

    def _ally_for_slot(self, slot, observation):
        return next((a for a in self._live_allies(observation) if a.get("slot") == slot), None)

    @staticmethod
    def _skills(data):
        return data[5] if isinstance(data, (tuple, list)) and len(data) > 5 and isinstance(data[5], (tuple, list)) else ()

    @staticmethod
    def _face_valid(card):
        return card is not None and str(card.get("type", "")).lower() in {"arts", "quick", "buster"} and card.get("sealed") is False

    @staticmethod
    def _valid_card(card):
        return card is not None and card.get("sealed") is False and str(card.get("type", "")).lower() in {"arts", "quick", "buster", "np"} and (str(card.get("type")).lower() != "np" or card.get("ready") is True)

    @classmethod
    def _group(cls, card):
        field_slot = cls._field_slot(card)
        if field_slot in (1, 2, 3):
            return field_slot
        visual = card.get("group", card.get("visualgroupid"))
        return ("visual", visual) if visual is not None else ("unknown", card.get("slot"))

    @staticmethod
    def _field_slot(card):
        for key in ("servant", "fieldslot"):
            if card.get(key) in (1, 2, 3):
                return card[key]
        return None

    def _np_below_full(self, card, observation):
        ally = self._ally_for_slot(self._field_slot(card), observation)
        return ally is not None and isinstance(ally.get("np"), (int, float)) and ally["np"] < 100

    @staticmethod
    def _enemy_hps(observation):
        return [e.get("hp") if isinstance(e.get("hp"), (int, float)) and e["hp"] > 0 else 0 for e in observation.enemies]

    def _target_hp(self, observation, enemies):
        selected = observation.signals.get("selected_enemy")
        if isinstance(selected, int):
            by_slot = next((e.get("hp") for e in observation.enemies if e.get("slot") == selected), None)
            if isinstance(by_slot, (int, float)):
                return by_slot
        return max(enemies, default=0)

    @staticmethod
    def _number(value):
        return value if isinstance(value, (int, float)) else 0

    @staticmethod
    def _turn_key(observation):
        return (observation.wave, observation.turn) if isinstance(observation.wave, int) and isinstance(observation.turn, int) else None

    def _decision(self, observation, action, reasons, deviations):
        self.last_decision = {"policy": self.name, "observation_id": observation.id, "reasons": reasons,
                              "deviations": deviations, "action": action.kind}
        return action
