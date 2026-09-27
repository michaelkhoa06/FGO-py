"""Translate validated actions to FGO-py's normalized battle coordinates."""
from __future__ import annotations

import time

from .models import Action
import re

from . import enemy_profile as ep
from . import order_change as oc


# The Close button of the game's skill-error dialog. Upstream FGO-py closes the
# same dialog (its isSkillCastFailed) by pressing 'J' at (704, 573), inside it.
SKILL_ERROR_CLOSE = (640, 563)


class BattleDriver:
    # Each inspection costs a few seconds of input; a swarm refills slots often.
    MAX_ENEMY_INSPECTIONS = 24
    MAX_ALLY_INSPECTIONS = 30
    # Mean grey-level change of the icon-row signature that counts as "changed".
    ALLY_ICON_CHANGE = 12
    # Buff icons change almost every turn; re-reading on each change cost about a
    # quarter of a story battle (92-122 s per run, 2026-09-24). Seal durations count
    # down from the read, and sealed/stunned cards are seen on the card screen, so
    # a changed row is re-read at most every this many turns.
    ALLY_REREAD_TURNS = 3
    # Boss stages inflict statuses on a script ("every 3 turns", "when attacking"),
    # which a fixed gap would read late. Once the battle shows such a mechanic, it
    # switches to re-reading changed allies every turn for the rest of the fight.
    _INFLICTS = re.compile(r"\binflict|\bapply\b.*\b(seal|stun|curse|poison|burn|charm)", re.IGNORECASE)
    _TRIGGER = re.compile(r"activate|when|each turn|every|start of|end of", re.IGNORECASE)
    # "Nullify Buff" / buff block and max-HP cuts come from Demeter (Olympus), whose
    # break strips party buffs and blocks the next three.
    _DEBUFF = re.compile(r"seal|stun|immobil|curse|poison|burn|charm|confus|sleep|petrif|down\b|decrease|unable"
                         r"|nullify|block|max hp|reduc", re.IGNORECASE)

    def __init__(self, device, observer, trace=None, *, order_change: bool = False, servant_index=None,
                 inspect_enemies: bool = False):
        self.device, self.observer, self.trace = device, observer, trace
        self.latest = None
        self.active_slots = []
        self.last_progress = None
        self.wave_first_turn = None
        self.tracked_wave = None
        # Order Change is opt-in: it assumes the Chaldea Combat Uniform and a
        # party whose back line the operator arranged. Battle-long facts a single
        # frame cannot carry are kept here and stamped onto each observation.
        self.order_change_enabled = order_change
        self.servant_index = servant_index or {}
        self.order_change_state = "unknown"
        self.backline: list[dict] | None = None
        self._field_ids: dict[int, int] = {}
        self._field_seen: dict[int, tuple] = {}
        self._field_pending: dict[int, tuple[int, int]] = {}
        self._swapped_slot: int | None = None
        # Enemy Status Changes, read by long-pressing an enemy. Kept per slot with
        # the wave/turn it was read, and stamped onto later observations.
        self.inspect_enemies_enabled = inspect_enemies
        self.enemy_profiles: dict[int, dict] = {}
        self.inspections_left = self.MAX_ENEMY_INSPECTIONS
        self._pending_profile_slot: int | None = None
        # True once the open dialog has been scrolled: later views merge, not replace.
        self._profile_scrolled = False
        self._profile_context = None
        self._enemy_last_hp: dict[int, int | None] = {}
        self._inspected_turn: dict[int, tuple] = {}
        # Ally Status tabs: seals, buffs and debuffs with remaining turns.
        self.ally_statuses: dict[int, dict] = {}
        self.ally_inspections_left = self.MAX_ALLY_INSPECTIONS
        self._pending_ally_slot: int | None = None
        self._ally_context = None
        self._ally_read_turn: dict[int, tuple] = {}
        # Enemy charge per slot, to notice an enemy NP (charge falling from full):
        # boss NPs are when most new statuses land, so allies are re-read then.
        self._enemy_charge: dict[int, tuple] = {}
        self._enemy_np_turn: tuple | None = None
        self.statuses_volatile: str | None = None  # why this battle is re-read every turn
        self._enemy_break_turn: tuple | None = None  # an enemy's HP rose: a break or revive

    def observe(self):
        observation = self.observer.observe(self.device.screenshot())
        if observation.phase == "tactical":
            self.active_slots = [a["slot"] for a in observation.allies
                                 if (a.get("hp") is not None and a["hp"] > 0)
                                 or (a.get("hp") is None and a.get("alive") is True)]
        if observation.phase in ("tactical", "cards") and observation.turn is not None and observation.wave is not None:
            current = (observation.wave, observation.turn)
            if self.tracked_wave != observation.wave:
                # Starting at a mid-wave turn cannot establish its true first turn.
                self.wave_first_turn = observation.turn if self.tracked_wave is not None or observation.turn == 1 else None
                self.tracked_wave = observation.wave
            observation.signals["stage_turn"] = (observation.turn - self.wave_first_turn + 1
                                                  if self.wave_first_turn is not None else None)
            observation.signals["turn_advanced"] = self.last_progress is not None and current > self.last_progress
            self.last_progress = current
        if self.order_change_enabled:
            self._track_order_change(observation)
        if self.inspect_enemies_enabled:
            self._track_enemy_profiles(observation)
        self.latest = observation
        return observation

    def _track_order_change(self, observation):
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        palette = signals.get("master_palette")
        if isinstance(palette, dict) and palette.get("order_change") in ("ready", "used"):
            # A palette read never un-spends Order Change within a battle.
            if self.order_change_state != "used":
                self.order_change_state = palette["order_change"]
        screen = signals.get("order_change")
        if observation.phase == "order_change" and isinstance(screen, dict):
            self.backline = [{"slot": m.get("slot"), "name": m.get("name"),
                              "servant_id": oc.resolve_servant_id(m.get("name"), self.servant_index)}
                             for m in screen.get("members", []) if m.get("group") == "sub"]
        if observation.phase == "tactical":
            self._track_reinforcements(observation)
        if observation.phase in ("tactical", "cards"):
            if self.backline is not None:
                observation.signals["backline"] = [dict(member) for member in self.backline]
            observation.signals["order_change_available"] = (
                self.order_change_state == "ready" and bool(self.backline)
                and all(member.get("name") for member in self.backline))

    def _track_enemy_profiles(self, observation):
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if observation.phase == "enemy_profile" and self._pending_profile_slot is not None:
            context = self._profile_context
            seen = signals.get("enemy_profile", {})
            previous = self.enemy_profiles.get(self._pending_profile_slot) if self._profile_scrolled else None
            enemy = next((e for e in getattr(context, "enemies", []) or []
                          if isinstance(e, dict) and e.get("slot") == self._pending_profile_slot), {})
            profile = {**seen, "slot": self._pending_profile_slot, "icons_at_read": enemy.get("status_icons"),
                       "read_at": {"wave": getattr(context, "wave", None), "turn": getattr(context, "turn", None)}}
            if previous is not None:
                # A scrolled view of the same open dialog: add what it newly shows.
                profile["effects"] = ep.merge_effects(previous.get("effects", []), seen.get("effects", []))
            self.enemy_profiles[self._pending_profile_slot] = profile
            for effect in profile.get("effects") or []:
                words = f"{effect.get('title', '')} {effect.get('text', '')}"
                if self.statuses_volatile is None and self._INFLICTS.search(words) and self._TRIGGER.search(words):
                    self._mark_volatile(f"enemy {self._pending_profile_slot} effect {effect.get('title')!r} "
                                        "inflicts statuses on a trigger")
        if observation.phase in ("tactical", "cards") and self.enemy_profiles:
            observation.signals["enemy_profiles"] = [
                {k: v for k, v in self.enemy_profiles[slot].items() if k != "icons_at_read"}
                for slot in sorted(self.enemy_profiles)]
        status = signals.get("ally_status")
        if (observation.phase == "ally_status" and self._pending_ally_slot is not None
                and isinstance(status, dict) and status.get("tab") == "status"):
            context = self._ally_context
            ally = next((a for a in getattr(context, "allies", []) if a.get("slot") == self._pending_ally_slot), {})
            previous = self.ally_statuses.get(self._pending_ally_slot) if self._profile_scrolled else None
            effects = status.get("effects", [])
            if previous is not None:
                effects = ep.merge_effects(previous.get("effects", []), effects)
            elif self.statuses_volatile is None:
                # First view of a re-read: a debuff the last read did not have means
                # the battle inflicts statuses as it goes.
                earlier = self.ally_statuses.get(self._pending_ally_slot)
                if earlier is not None:
                    known = [{"title": e.get("title", "")} for e in earlier.get("effects") or []]
                    for effect in effects:
                        title = str(effect.get("title", ""))
                        # merge_effects adds a title only if it is not already known.
                        is_new = len(ep.merge_effects(known, [{"title": title}])) > len(known)
                        if self._DEBUFF.search(title) and is_new:
                            self._mark_volatile(f"ally {self._pending_ally_slot} gained {title!r} since the last read")
                            break
            self.ally_statuses[self._pending_ally_slot] = {
                "slot": self._pending_ally_slot, "servant_id": ally.get("id"), "name": status.get("name"),
                "atk": status.get("atk"), "effects": effects,
                "possibly_truncated": status.get("possibly_truncated"),
                "icons_at_read": ally.get("status_icons"),
                "read_at": {"wave": getattr(context, "wave", None), "turn": getattr(context, "turn", None)}}
        if observation.phase in ("tactical", "cards") and self.ally_statuses:
            observation.signals["ally_statuses"] = [
                {k: v for k, v in self.ally_statuses[slot].items() if k != "icons_at_read"}
                for slot in sorted(self.ally_statuses)]

    def allies_to_inspect(self, observation) -> list[int]:
        """Living field allies whose Status tab should be (re)read now.

        Read when unread, when a different servant holds the slot, or when the
        ally's icon row visibly changed since the last read (a seal expired, a buff
        landed). At most once per slot per turn and ``MAX_ALLY_INSPECTIONS`` per battle.
        """
        if (not self.inspect_enemies_enabled or observation.phase != "tactical"
                or observation.wave is None or observation.turn is None):
            return []
        for enemy in observation.enemies:
            charge = enemy.get("charge") if isinstance(enemy, dict) else None
            if not (isinstance(charge, (list, tuple)) and len(charge) == 2 and all(type(v) is int for v in charge)):
                continue
            before = self._enemy_charge.get(enemy.get("slot"))
            if before is not None and before[1] > 0 and before[0] >= before[1] and charge[0] < before[0]:
                self._enemy_np_turn = (observation.wave, observation.turn)
            self._enemy_charge[enemy.get("slot")] = tuple(charge)
        now = (observation.wave, observation.turn)
        after_enemy_np = self._enemy_np_turn == now or self._enemy_break_turn == now
        wanted = []
        for ally in observation.allies:
            slot = ally.get("slot")
            living = (type(ally.get("hp")) is int and ally["hp"] > 0) or ally.get("alive") is True
            if slot not in (1, 2, 3) or not living:
                continue
            if self._ally_read_turn.get(slot) == (observation.wave, observation.turn):
                continue
            known = self.ally_statuses.get(slot)
            icons, before = ally.get("status_icons"), (known or {}).get("icons_at_read")
            changed = (isinstance(icons, list) and isinstance(before, list) and len(icons) == len(before)
                       and sum(abs(a - b) for a, b in zip(icons, before)) / len(icons) > self.ALLY_ICON_CHANGE)
            read_turn = ((known or {}).get("read_at") or {}).get("turn")
            gap = 1 if self.statuses_volatile else self.ALLY_REREAD_TURNS
            due = type(read_turn) is not int or observation.turn - read_turn >= gap or after_enemy_np
            if known is None or known.get("servant_id") != ally.get("id") or (changed and due):
                wanted.append(slot)
        return wanted[:max(0, self.ally_inspections_left)]

    def enemies_to_inspect(self, observation) -> list[int]:
        """Standard-layout enemy slots whose Status Changes should be (re)read now.

        A slot is read when it has no profile from this wave, when its HP rose (a
        new enemy took the slot, or the enemy refilled or revived), or when it was
        seen empty since the last read. At most once per slot per turn and at most
        ``MAX_ENEMY_INSPECTIONS`` per battle, so OCR noise cannot cause a loop.
        """
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if (not self.inspect_enemies_enabled or observation.phase != "tactical"
                or signals.get("enemy_layout") != "standard3" or observation.wave is None or observation.turn is None):
            return []
        wanted = []
        for enemy in observation.enemies:
            slot = enemy.get("slot") if isinstance(enemy, dict) else None
            if slot not in (1, 2, 3):
                continue
            hp = enemy.get("hp") if type(enemy.get("hp")) is int and enemy.get("hp") > 0 else None
            previous = self._enemy_last_hp.get(slot, "unseen")
            if enemy.get("present") is not True:
                self._enemy_last_hp[slot] = None
                continue
            if hp is None:
                continue
            self._enemy_last_hp[slot] = hp
            if type(previous) is int and hp > previous:
                # A break bar or revive: bosses apply their scripted statuses here.
                self._enemy_break_turn = (observation.wave, observation.turn)
            profile = self.enemy_profiles.get(slot)
            icons, before = enemy.get("status_icons"), (profile or {}).get("icons_at_read")
            # Seen in Demeter's fight: a boss cycles self-buffs (damage cut, charge,
            # debuff immunity) every turn, so a changed buff row is re-read, every
            # turn once the battle is volatile and every third turn otherwise.
            read_turn = ((profile or {}).get("read_at") or {}).get("turn")
            gap = 1 if self.statuses_volatile else self.ALLY_REREAD_TURNS
            cycled = (isinstance(icons, list) and isinstance(before, list) and len(icons) == len(before)
                      and sum(abs(a - b) for a, b in zip(icons, before)) / len(icons) > self.ALLY_ICON_CHANGE
                      and (type(read_turn) is not int or observation.turn - read_turn >= gap))
            stale = (profile is None or profile.get("read_at", {}).get("wave") != observation.wave
                     or previous is None or (type(previous) is int and hp > previous) or cycled)
            if stale and self._inspected_turn.get(slot) != (observation.wave, observation.turn):
                wanted.append(slot)
        return wanted[:max(0, self.inspections_left)]

    def _mark_volatile(self, reason: str) -> None:
        self.statuses_volatile = reason
        if self.trace:
            self.trace.record("statuses_volatile", {"reason": reason})

    def _track_reinforcements(self, observation):
        """A dead ally's slot refilled from the back line takes that member off it.

        Seen live (Morgan): Oberon, Ciel and Space Ereshkigal all came in as
        reinforcements, the back line was empty, yet Order Change was still
        offered and the game refused it. The back line fills slots in order, so a
        member matching the newcomer's id goes first, else the first member.

        Seen live (Cathedral Guards): a skill animation made Lady Avalon read as
        Oberon for single frames, and each flicker emptied the back line. A
        replacement only appears after an enemy phase, so the new servant must
        show in a later turn than the old one and hold for two frames.
        """
        turn = (observation.wave, observation.turn)
        current = {a.get("slot"): a.get("id") for a in observation.allies
                   if a.get("slot") in (1, 2, 3) and type(a.get("id")) is int}
        # Seen live 2026-09-27: a support Castoria was never identified (id None);
        # when Zhuge Liang replaced her, the slot looked like a first sighting and
        # the back line kept him, so a later Order Change met "no switchable Sub
        # Members". A living but unidentified servant holds the slot as "unknown".
        for ally in observation.allies:
            slot = ally.get("slot")
            if (slot in (1, 2, 3) and slot not in current and slot not in self._field_ids
                    and ally.get("id") is None and (ally.get("hp") or ally.get("alive"))):
                self._field_ids[slot], self._field_seen[slot] = "unknown", turn
        for slot, ident in current.items():
            settled = self._field_ids.get(slot)
            if settled is None or settled == ident:
                self._field_ids[slot] = ident
                self._field_seen[slot] = turn
                self._field_pending.pop(slot, None)
                continue
            if self._swapped_slot == slot:
                self._swapped_slot = None
                self._field_ids[slot], self._field_seen[slot] = ident, turn
                continue
            previous = self._field_pending.get(slot)
            if previous is None or previous[0] != ident:
                self._field_pending[slot] = (ident, 1)
                continue
            self._field_pending[slot] = (ident, previous[1] + 1)
            if None in turn or turn == self._field_seen.get(slot):
                continue  # same turn: a misread, not a death and a replacement
            self._field_pending.pop(slot, None)
            self._field_ids[slot], self._field_seen[slot] = ident, turn
            matching = [i for i, m in enumerate(self.backline or []) if self._member_is(m, ident)]
            if settled == "unknown" and not matching:
                continue  # the unidentified servant may just have been identified
            if self.backline:
                index = matching[0] if matching else 0
                entered = self.backline.pop(index)
                # Seen live 2026-09-27: after Zhuge Liang (sub 1) came in, the game
                # showed Space Ereshkigal as sub 2; the others move up a place.
                for place, member in enumerate(self.backline, start=1):
                    shift = (member.get("slot") or place) - place
                    member["slot"] = place
                    if type(member.get("position")) is int:
                        member["position"] -= shift
                if self.trace:
                    self.trace.record("backline_reinforcement", {"field_slot": slot, "servant_id": ident,
                                                                 "member": entered, "left": len(self.backline)})

    def _member_is(self, member: dict, ident: int) -> bool:
        """A back-line member is this servant: same id, or one name contains the other
        (the order-change screen shows "Zhuge Liang", the catalog "Zhuge Liang (Lord
        El-Melloi II)"; OCR adds stray letters, as in "YSpace Ereshkigal")."""
        if member.get("servant_id") == ident:
            return True
        from difflib import SequenceMatcher
        letters = lambda text: "".join(ch for ch in str(text or "").casefold() if ch.isalpha())
        shown = letters(member.get("name"))
        known = letters((self.servant_index or {}).get(ident, {}).get("name"))
        if len(shown) < 5 or len(known) < 5:
            return False
        # The shorter name, up to 3 stray OCR letters ("Zhuge LiangA"), is in the longer.
        short, long = sorted((shown, known), key=len)
        run = SequenceMatcher(None, short, long).find_longest_match(0, len(short), 0, len(long)).size
        return run >= max(5, len(short) - 3)

    def mark_order_change_unavailable(self) -> None:
        """The game refused Order Change (e.g. no switchable sub members) for this battle."""
        self.order_change_state = "used"

    def mark_order_change_used(self, field_slot: int, sub_slot: int, outgoing_name: str | None):
        """Record a confirmed swap: the skill is spent and the outgoing servant is now in the back."""
        self.order_change_state = "used"
        self._swapped_slot = field_slot
        if self.backline:
            for member in self.backline:
                if member.get("slot") == sub_slot:
                    member.update({"name": outgoing_name,
                                   "servant_id": oc.resolve_servant_id(outgoing_name, self.servant_index)})

    def _tap(self, x, y):
        if self.trace:
            self.trace.record("input", {"serial": self.device.serial, "x": x, "y": y,
                                        "observation_id": self.latest.id})
        self.device.tap(x, y)

    def can_target(self, target: int | None) -> bool:
        """Whether the current target dialog maps this field slot unambiguously."""
        if self.latest is None or self.latest.phase != "target_selection":
            return False
        count = self.latest.target_count
        if count not in (1, 2, 3) or target not in (1, 2, 3):
            return False
        slots = [1, 2, 3] if count == 3 else self.active_slots
        return len(slots) == count and target in slots

    def send(self, action: Action):
        if self.latest is None:
            raise RuntimeError("Observe the battle before sending an action.")
        kind = action.kind
        if kind == "skill":
            if self.latest.phase != "tactical":
                raise ValueError("Skills require the tactical screen.")
            if action.servant not in (1, 2, 3) or action.skill not in (1, 2, 3):
                raise ValueError("Invalid skill slot.")
            coordinates = (((73, 573), (163, 573), (257, 573)),
                           ((388, 573), (483, 573), (574, 573)),
                           ((704, 573), (801, 573), (891, 573)))
            self._tap(*coordinates[action.servant-1][action.skill-1])
        elif kind == "skill_target":
            count = self.latest.target_count
            if self.latest.phase != "target_selection" or count not in (1, 2, 3):
                raise ValueError("No recognized target dialog.")
            # Three detected targets necessarily occupy all three field slots;
            # unreadable HP does not create positional ambiguity in this layout.
            slots = [1, 2, 3] if count == 3 else self.active_slots
            if not self.can_target(action.target):
                raise ValueError("Target dialog does not match observed living allies; target mapping is uncertain.")
            index = slots.index(action.target)
            x = {1: [649], 2: [399, 875], 3: [399, 649, 875]}[count][index]
            self._tap(x, 440)
        elif kind == "attack":
            if self.latest.phase != "tactical":
                raise ValueError("Attack requires the tactical screen.")
            self._tap(1231, 687)
        elif kind == "enemy_target":
            if not self._can_change_enemy_target(action.target):
                raise ValueError("Enemy target requires a fresh, verifiable standard three-enemy tactical observation.")
            self._tap(*((142, 40), (342, 40), (542, 40))[action.target - 1])
        elif kind == "cards":
            # Fewer than three only when stunned cards leave fewer selectable
            # (the controller checks the count against the observation).
            if (self.latest.phase != "cards" or not 1 <= len(action.cards) <= 3 or len(set(action.cards)) != len(action.cards)
                    or any(type(c) is not int or c not in range(1, 9) for c in action.cards)):
                raise ValueError("Choose one to three distinct cards from the command screen.")
            coordinates = ((185, 440), (399, 440), (649, 440), (875, 440), (1101, 440),
                           (431, 203), (651, 203), (845, 203))
            for slot in action.cards:
                self._tap(*coordinates[slot-1])
                time.sleep(.25)
        elif kind == "enemy_profile_open":
            if (self.latest.phase != "tactical" or action.target not in (1, 2, 3)
                    or self.latest.signals.get("enemy_layout") != "standard3"
                    or not any(e.get("slot") == action.target and e.get("present") is True
                               for e in self.latest.enemies)):
                raise ValueError("Inspecting an enemy requires a tactical frame showing it in the standard row.")
            if self.inspections_left <= 0:
                raise ValueError("The per-battle enemy inspection budget is spent.")
            x, y = ep.ENEMY_ICONS[action.target - 1]
            self.inspections_left -= 1
            self._pending_profile_slot = action.target
            self._profile_scrolled = False
            self._profile_context = self.latest
            self._inspected_turn[action.target] = (self.latest.wave, self.latest.turn)
            if self.trace:
                self.trace.record("input", {"serial": self.device.serial, "x": x, "y": y,
                                            "hold_ms": ep.LONG_PRESS_MS, "observation_id": self.latest.id})
            self.device.long_press(x, y, ep.LONG_PRESS_MS)
        elif kind == "ally_status_open":
            if (self.latest.phase != "tactical" or action.target not in (1, 2, 3)
                    or not any(a.get("slot") == action.target for a in self.latest.allies)):
                raise ValueError("Inspecting an ally requires a tactical frame showing it.")
            if self.ally_inspections_left <= 0:
                raise ValueError("The per-battle ally inspection budget is spent.")
            self.ally_inspections_left -= 1
            self._pending_ally_slot = action.target
            self._profile_scrolled = False
            self._ally_context = self.latest
            self._ally_read_turn[action.target] = (self.latest.wave, self.latest.turn)
            self._tap(*ep.ALLY_PORTRAITS[action.target - 1])
        elif kind == "ally_status_tab":
            status = self.latest.signals.get("ally_status") if isinstance(self.latest.signals, dict) else None
            if self.latest.phase != "ally_status" or not isinstance(status, dict) or status.get("tab") != "details":
                raise ValueError("Switching tabs requires the ally dialog observed on Details.")
            self._tap(*ep.ALLY_STATUS_TAB)
        elif kind == "ally_status_close":
            if self.latest.phase != "ally_status":
                raise ValueError("Closing requires the observed ally dialog.")
            self._pending_ally_slot = None
            self._tap(*ep.ALLY_CLOSE_BUTTON)
        elif kind == "status_scroll":
            status = self.latest.signals.get("ally_status") if isinstance(self.latest.signals, dict) else None
            subject = ("enemy" if self.latest.phase == "enemy_profile" else
                       "ally" if self.latest.phase == "ally_status" and isinstance(status, dict)
                       and status.get("tab") == "status" else None)
            if subject is None:
                raise ValueError("Scrolling requires an observed status list.")
            (x1, y1), (x2, y2) = ep.SCROLL_SWIPES[subject]
            self._profile_scrolled = True
            if self.trace:
                self.trace.record("input", {"serial": self.device.serial, "swipe": [x1, y1, x2, y2],
                                            "ms": ep.SCROLL_MS, "observation_id": self.latest.id})
            self.device.swipe(x1, y1, x2, y2, ep.SCROLL_MS)
        elif kind == "cards_back":
            # The command-card Back button returns to the tactical screen without
            # committing anything.
            if self.latest.phase != "cards":
                raise ValueError("Back requires the observed command-card screen.")
            self._tap(1195, 683)
        elif kind == "dismiss_skill_error":
            if self.latest.phase != "skill_error":
                raise ValueError("Dismissing requires the observed skill-error dialog.")
            self._tap(*SKILL_ERROR_CLOSE)
        elif kind == "enemy_profile_close":
            if self.latest.phase != "enemy_profile":
                raise ValueError("Closing requires the observed enemy Status Changes dialog.")
            self._pending_profile_slot = None
            self._tap(*ep.CLOSE_BUTTON)
        elif kind == "master_menu":
            # A toggle: opens the palette, or closes it when already open.
            if self.latest.phase != "tactical":
                raise ValueError("The master skill button requires the tactical screen.")
            self._tap(*oc.MASTER_SKILL_BUTTON)
        elif kind == "master_skill":
            palette = self.latest.signals.get("master_palette") if isinstance(self.latest.signals, dict) else None
            if self.latest.phase != "tactical" or not isinstance(palette, dict):
                raise ValueError("A master skill requires an observed open palette.")
            if action.skill != oc.ORDER_CHANGE_SKILL or palette.get("order_change") != "ready":
                raise ValueError("Only a ready Order Change master skill is supported.")
            self._tap(*oc.PALETTE_SKILLS[action.skill - 1])
        elif kind == "order_change_member":
            if self.latest.phase != "order_change" or action.target not in range(1, 7):
                raise ValueError("Selecting a member requires the observed Order Change screen.")
            self._tap(oc.MEMBER_COLUMNS[action.target - 1], oc.MEMBER_Y)
        elif kind == "order_change_confirm":
            screen = self.latest.signals.get("order_change") if isinstance(self.latest.signals, dict) else None
            if self.latest.phase != "order_change" or not isinstance(screen, dict) or screen.get("replace_enabled") is not True:
                raise ValueError("Replace requires two observed selections and an enabled button.")
            self._tap(*oc.REPLACE_BUTTON)
        elif kind == "order_change_cancel":
            if self.latest.phase != "order_change":
                raise ValueError("Cancelling requires the observed Order Change screen.")
            self._tap(*oc.CLOSE_BUTTON)
        else:
            raise ValueError(f"Unsupported action: {kind}")

    def _can_change_enemy_target(self, target: object) -> bool:
        observation = self.latest
        if observation is None or observation.phase != "tactical" or type(target) is not int or target not in (1, 2, 3):
            return False
        if type(observation.wave) is not int or observation.wave <= 0 or type(observation.turn) is not int or observation.turn <= 0:
            return False
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if (signals.get("enemy_layout") != "standard3" or signals.get("enemy_target_verifiable") is not True
                or type(signals.get("selected_enemy")) is not int or signals["selected_enemy"] not in (1, 2, 3)
                or signals["selected_enemy"] == target):
            return False
        slots = {}
        for enemy in observation.enemies:
            if not isinstance(enemy, dict) or type(enemy.get("slot")) is not int or enemy["slot"] not in (1, 2, 3) or enemy["slot"] in slots:
                return False
            slots[enemy["slot"]] = enemy
        enemy, selected_enemy = slots.get(target), slots.get(signals["selected_enemy"])
        return (enemy is not None and selected_enemy is not None and type(enemy.get("hp")) is int and enemy["hp"] > 0
                and type(selected_enemy.get("hp")) is int and selected_enemy["hp"] > 0)
