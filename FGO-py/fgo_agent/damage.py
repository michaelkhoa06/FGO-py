"""Damage estimates from the published FGO damage formula and Atlas numbers.

Formula (Atlas Academy fgo-docs, "Damage Formula"):
  atk * npMult * (firstBonus + cardValue * (1 + cardMod)) * classRate * affinity * 0.23
      * (1 + atkMod + defDown) * extraMod * (1 + powerMod + npMod * isNP) + busterChainFlat

Estimates rank choices; they are not predictions of exact damage. Unknowns:
the account's ATK (max-level base ATK is used), Craft Essences, passives, enemy
defence buffs, critical hits, random variance (0.9-1.1) and bonuses that need an
enemy trait the screen does not show (reported separately, never assumed).
NP level 1 is assumed for every servant (user, 2026-09-26: "They're all 1").
Skill level 10 is assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CARD_VALUES = {"arts": (1.0, 1.2, 1.4), "buster": (1.5, 1.8, 2.1), "quick": (0.8, 0.96, 1.12)}
NP_LEVEL = 1
SKILL_LEVEL = 10


@dataclass
class Buffs:
    """Additive damage modifiers on one ally (fractions: 0.5 = +50%)."""
    card: dict = field(default_factory=lambda: {"arts": 0.0, "buster": 0.0, "quick": 0.0})
    atk: float = 0.0
    np: float = 0.0
    power: float = 0.0
    conditional: list = field(default_factory=list)  # (kind, value, enemy traits) not assumed


@dataclass
class Attacker:
    slot: int
    atk: int
    class_rate: float
    class_name: str | None
    np_card: str | None
    np_mult: float | None        # 9.0 = 900% at NP level 1
    np_area: bool = False
    np_trait_bonus: float | None = None  # multiplier vs an enemy trait, when that trait applies


def affinity(relation: dict, attacker: str | None, defender: str | None) -> float:
    try:
        return relation[attacker][defender] / 1000
    except (KeyError, TypeError):
        return 1.0


def chain_damage(chain: list[tuple[int, str, bool]], attackers: dict[int, Attacker], buffs: dict[int, Buffs],
                 *, defender_class: str | None = None, relation: dict | None = None,
                 def_down: float = 0.0) -> dict:
    """Estimated damage to one target from a 3-card chain.

    ``chain`` lists (ally slot, card type, is_np) in the chosen order. Returns the
    total and the per-card values, plus the extra attack when all three cards
    belong to one servant (Brave Chain).
    """
    if not 1 <= len(chain) <= 3:
        raise ValueError("a chain has one to three cards")
    first = chain[0][1]
    first_bonus = 0.5 if first == "buster" else 0.0
    mighty_buster = len(chain) == 3 and all(card == "buster" for _, card, _ in chain)
    per_card, total = [], 0.0
    for position, (slot, card, is_np) in enumerate(chain):
        attacker, buff = attackers.get(slot), buffs.get(slot, Buffs())
        if attacker is None or card not in CARD_VALUES:
            per_card.append(None)
            continue
        value = CARD_VALUES[card][0 if is_np else position]
        rate = attacker.class_rate * affinity(relation or {}, attacker.class_name, defender_class)
        common = attacker.atk * rate * 0.23 * max(1 + buff.atk + def_down, 0)
        if is_np:
            if not attacker.np_mult:
                per_card.append(None)
                continue
            damage = (common * attacker.np_mult * value * max(1 + buff.card.get(card, 0.0), 0)
                      * max(1 + buff.power + buff.np, 0.001))
        else:
            damage = common * (first_bonus + value * max(1 + buff.card.get(card, 0.0), 0)) * max(1 + buff.power, 0.001)
            if mighty_buster:
                damage += attacker.atk * 0.2
        per_card.append(round(damage))
        total += damage
    extra = None
    owners = {slot for slot, _, _ in chain}
    if len(owners) == 1 and len(chain) == 3:
        slot = chain[0][0]
        attacker, buff = attackers.get(slot), buffs.get(slot, Buffs())
        if attacker is not None:
            same_colour = len({card for _, card, _ in chain}) == 1
            rate = attacker.class_rate * affinity(relation or {}, attacker.class_name, defender_class)
            extra = (attacker.atk * rate * 0.23 * max(1 + buff.atk + def_down, 0)
                     * (first_bonus + 1.0) * (3.5 if same_colour else 2.0) * max(1 + buff.power, 0.001))
            total += extra
    return {"total": round(total), "cards": per_card, "extra": round(extra) if extra else None}


def attacker_from_catalog(slot: int, entry: dict, member: dict | None = None) -> Attacker | None:
    """ATK, class and NP numbers for one field servant.

    ``member`` is the Confirm Party read: its ATK (level, Fou and Craft Essence
    ATK included) and NP level replace the max-level base ATK and NP level 1.
    """
    if not isinstance(entry, dict) or not entry.get("atk_max"):
        return None
    np_level = (member or {}).get("np_level") or NP_LEVEL
    np_card = np_mult = trait_bonus = None
    area = False
    for n in entry.get("noble_phantasms") or []:
        for effect in n.get("effects") or []:
            if str(effect.get("fn", "")).startswith("damageNp"):
                values = [v for v in effect.get("values") or [] if isinstance(v, int)]
                if values:
                    np_mult = values[min(np_level, len(values)) - 1] / 1000
                    np_card = {"1": "arts", "2": "buster", "3": "quick"}.get(str(n.get("card")))
                    area = effect.get("to") == "enemyAll"
                    if effect.get("correction"):
                        trait_bonus = effect["correction"] / 1000
    atk = (member or {}).get("atk") or entry["atk_max"]
    return Attacker(slot=slot, atk=int(atk), class_rate=float(entry.get("class_attack_rate") or 1.0),
                    class_name=entry.get("class_name"), np_card=np_card, np_mult=np_mult, np_area=area,
                    np_trait_bonus=trait_bonus)


class BuffTracker:
    """Damage buffs the agent itself applied, with the turn each expires.

    Only effects from skills and NPs the controller confirmed are tracked, at the
    assumed skill level. Buffs from Craft Essences, passives, the enemy or the
    story are not in here; the ally Status tab shows those by name.
    """

    def __init__(self):
        self.entries: list[dict] = []
        self.states: list[dict] = []  # e.g. holdingHolyGrail, for "requires" effects

    def expire(self, turn: int | None) -> None:
        if type(turn) is not int:
            return
        self.entries = [e for e in self.entries if e["until"] >= turn]
        self.states = [s for s in self.states if s["until"] >= turn]

    def forget_slot(self, slot: int) -> None:
        self.entries = [e for e in self.entries if e["slot"] != slot]
        self.states = [s for s in self.states if s["slot"] != slot]

    def apply(self, effects: list[dict], *, caster: int, target: int | None, turn: int, level_index: int,
              field_slots: list[int], permanent: bool = False) -> None:
        for effect in effects or []:
            if str(effect.get("fn", "")).startswith("damageNp") or effect.get("fn") == "gainNp":
                continue
            requires = effect.get("requires") or []
            if requires and not any(s["slot"] == caster and s["trait"] in requires for s in self.states):
                continue
            if effect.get("grants_trait") is not None:
                turns = effect.get("turns")
                self.states.append({"slot": caster, "trait": effect["grants_trait"], "name": effect.get("state"),
                                    "until": turn + turns - 1 if isinstance(turns, int) and turns > 0 else turn + 99})
                continue
            to = effect.get("to")
            if to == "self":
                slots = [caster]
            elif to == "ptOne":
                slots = [target] if target in (1, 2, 3) else []
            elif to == "ptAll":
                slots = list(field_slots)
            elif to == "ptOther":
                slots = [s for s in field_slots if s != caster]
            elif to in ("enemy", "enemyAll"):
                slots = [0]  # enemy side
            else:
                continue
            values = [v for v in effect.get("values") or [] if isinstance(v, int)]
            value = values[min(level_index, len(values)) - 1] / 1000 if values else 0.0
            turns = effect.get("turns")
            until = (10_000 if permanent else
                     turn + turns - 1 if isinstance(turns, int) and turns > 0 else turn + 99)
            for slot in slots:
                self.entries.append({"slot": slot, "buff": effect.get("buff"), "card": effect.get("card"),
                                     "value": value, "until": until, "traits": effect.get("target_traits")})

    def buffs_for(self, slot: int) -> Buffs:
        buffs = Buffs()
        for e in self.entries:
            if e["slot"] != slot:
                continue
            if e.get("traits"):
                buffs.conditional.append((e["buff"], e["value"], e["traits"]))
                continue
            kind, value = e["buff"], e["value"]
            if kind == "upCommandall":
                for card in e.get("card") or []:
                    if card in buffs.card:
                        buffs.card[card] += value
            elif kind == "downCommandall":
                for card in e.get("card") or []:
                    if card in buffs.card:
                        buffs.card[card] -= value
            elif kind == "upAtk":
                buffs.atk += value
            elif kind == "downAtk":
                buffs.atk -= value
            elif kind == "upNpdamage":
                buffs.np += value
            elif kind == "downNpdamage":
                buffs.np -= value
            elif kind == "upDamage":
                buffs.power += value
        return buffs

    def enemy_def_down(self) -> float:
        return sum(e["value"] for e in self.entries if e["slot"] == 0 and e["buff"] == "downDefence"
                   and not e.get("traits"))

    def describe(self, slot: int) -> list[dict]:
        return [{"buff": e["buff"], "card": e.get("card"), "value_pct": round(e["value"] * 100),
                 "until_turn": e["until"], **({"needs_enemy_trait": e["traits"]} if e.get("traits") else {})}
                for e in self.entries if e["slot"] == slot]
