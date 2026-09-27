"""Constrained model policy for the observable FGO battle controller.

The model receives a compact replayable observation and can select only an action
whose exact id was enumerated from the current screen.  It never receives a
screenshot, a device handle, or general controller commands.
"""
from __future__ import annotations

import hashlib
import json
from itertools import permutations
from pathlib import Path
import re
from typing import Any, Protocol

from .legacy_policy import LegacyPolicy
from .models import Action, ActionResult, Observation


OBJECTIVES = ("farm_fast", "survive_and_win")


class PlannerError(RuntimeError):
    """Raised when a planner backend returns an unusable decision."""


class PlannerBackend(Protocol):
    def choose(self, request: dict, schema: dict) -> dict: ...


class PlannerPolicy:
    """Allow an AI backend to choose only from observable, controller-safe actions.

    Legacy skill tuples are exposed as coarse eligibility/targeting gates only.
    They do not describe effect magnitudes, duration, max HP, or complete skill
    semantics, so the request explicitly calls out that provenance gap.
    """

    name = "constrained-planner"
    _MAX_REASON = 400
    _MAX_HISTORY = 24
    _SPECIAL_MODES = {6, 7, 8}
    _MAX_PLAN = 8
    _MAX_CARD_CANDIDATES = 10

    _MAX_NOTES = 6000

    # Plan-ahead mode: at most this many whole-battle (re)planning calls.
    _MAX_BATTLE_PLANS = 5
    _MAX_PLAN_TURNS = 12
    _PLANNED_STEP = re.compile(r"^(s[1-3]-[1-3]-t[1-3]|oc-f[1-3]-b[1-3]|e[1-3])$")

    def __init__(self, servant_data: dict, backend: PlannerBackend, trace=None, strategy_catalog=None, playbook_path=None,
                 party: list | None = None,
                 objective: str | None = None, notes: str | None = None, plan_ahead: bool = False):
        self.servant_data = servant_data
        self.backend = backend
        self.trace = trace
        self.strategy_catalog = strategy_catalog or {}
        if objective is not None and objective not in OBJECTIVES:
            raise PlannerError(f"objective must be one of {OBJECTIVES}")
        self.objective = objective or "survive_and_win"
        # The upstream heuristic is reused read-only, as a card scorer and as a
        # declared baseline.  It never sends input and never sees the backend.
        self._scorer = LegacyPolicy(servant_data)
        path = Path(playbook_path) if playbook_path is not None else Path(__file__).parent / 'assets/strategy/PLAYBOOK.md'
        self._guide = path.read_text(encoding='utf-8')
        if not self._guide.strip() or len(self._guide) > 24000:
            raise PlannerError('Strategy guide must contain 1..24000 characters')
        # Each objective sees only its sections: story calls skip the farming
        # procedure and farming calls skip the story priorities.
        self._guide = self._guide_for(self._guide, self.objective)
        self._guide_hash = hashlib.sha256(self._guide.encode('utf-8')).hexdigest()
        # Written after an earlier attempt at THIS battle failed. Loaded once and
        # sent on every decision, like the guide; never accumulated in a session.
        notes = (notes or "").strip()
        if len(notes) > self._MAX_NOTES:
            raise PlannerError(f"Stage notes must be at most {self._MAX_NOTES} characters")
        self._notes = notes or None
        self.last_decision: dict = {}
        self._pending: dict[tuple[int, int], tuple[Any, ...]] = {}
        self._pending_enemy_targets: dict[int, tuple[int, int]] = {}
        self._confirmed: set[tuple[Any, ...]] = set()
        self._confirmed_enemy_target_turns: set[tuple[int, int]] = set()
        self._history: list[dict] = []
        self._known_actor_ids: dict[int, int] = {}
        self._plan_queue: list[str] = []
        self._plan_key: tuple | None = None
        self._plan_reason = ""
        self._plan_request_id: str | None = None
        # Whole-battle schedule (plan-ahead mode). ``_deviation`` holds why the
        # schedule no longer matches the screen; the next tactical frame re-plans.
        self.plan_ahead = plan_ahead
        self._battle_plan: dict | None = None
        self._battle_plans_made = 0
        self._deviation: str | None = None
        self._deviation_crisis = False
        self._replanned_turns: set[tuple] = set()
        self._plan_issued: set[tuple] = set()
        self._plan_enemy_hp: dict[int, int] = {}
        self._plan_enemy_wave: int | None = None
        self._plan_enemy_charge: dict[int, tuple[int, int]] = {}
        self._charge_gain: dict[int, int] = {}
        self._last_remaining: int | None = None
        self._early_np_flagged: dict[int, set[int]] = {}
        # Damage estimates (user request 2026-09-26): buffs this agent applied,
        # tracked with their Atlas values, and the official class affinity table.
        from .damage import BuffTracker
        from .catalog import load_class_relation
        self._tracker = BuffTracker()
        # Confirm Party read (user idea 2026-09-26): real ATK, skill and NP levels.
        self._party = [m for m in party or [] if isinstance(m, dict)]
        self._party_by_slot: dict[int, dict] = {}
        self._party_seen_slots: set[int] = set()
        self._tracker_ids: dict[int, int] = {}
        self._last_obs: Observation | None = None
        # Live 2026-09-26 (Avenger): estimates ran 1.94x and 1.93x below the real HP
        # drop on two clean turns (unseen ATK, Craft Essences, crits). The ratio is
        # learned per battle and applied to reported estimates.
        self._calibration: list[float] = []
        # HP at the last tactical frame of the current turn, and the losses over
        # the last enemy turn (see _track_incoming).
        self._hp_turn, self._hp_last, self._incoming = None, {}, None
        self._pending_hit: dict | None = None
        try:
            self._class_relation = load_class_relation()
        except Exception:
            self._class_relation = {}

    def next_action(self, obs: Observation) -> Action:
        """Ask the backend to select one exact action from this observation's enum."""
        self._track_field(obs)
        self._clear_replaced_actors(obs)
        self._confirmed = {key for key in self._confirmed if key[:2] == (obs.wave, obs.turn)}
        self._confirmed_enemy_target_turns = {key for key in self._confirmed_enemy_target_turns
                                              if key == (obs.wave, obs.turn)}
        request, allowed = self._request_and_actions(obs)
        if self.plan_ahead:
            scheduled = self._from_battle_plan(obs, request, allowed)
            if scheduled is not None:
                return scheduled
        queued = self._dequeue(obs, allowed)
        if queued is not None:
            return queued
        if len(allowed) == 1:
            # Nothing to decide (seen live: nine calls answering "attack is the only
            # allowed action" while every skill was spent).
            (identifier, action), = allowed.items()
            self._register_pending(obs, action)
            self.last_decision = {"policy": self.name, "observation_id": obs.id,
                                  "request_id": request["request_id"], "chosen_action_id": identifier,
                                  "chosen_reason": "only allowed action", "source": "only_allowed_action",
                                  "model_call_metadata": None}
            self._trace("planner_scheduled_action", self.last_decision)
            return Action(action.kind, action.servant, action.skill, action.target, action.cards,
                          "only allowed action")
        schema = self._response_schema(list(allowed))
        self._trace("planner_request", request)
        try:
            response = self.backend.choose(request, schema)
        except Exception as exc:
            self._trace("planner_error", {"observation_id": obs.id, "request_id": request["request_id"],
                                          "error": type(exc).__name__})
            raise PlannerError(f"planner backend failed: {type(exc).__name__}") from exc
        self._trace("planner_response", self._safe_response(response))
        try:
            action, reason, uncertainty, plan = self._validate_response(response, request, allowed)
        except PlannerError as exc:
            self._trace("planner_error", {"observation_id": obs.id, "request_id": request["request_id"],
                                          "error": str(exc)})
            raise
        self._register_pending(obs, action)
        # Remaining planned ids are served without another model call, but only
        # after being re-derived from a fresh observation's allowed actions.
        self._plan_queue = list(plan[1:])
        self._plan_key = (obs.phase, self._turn_key(obs)) if self._plan_queue else None
        self._plan_reason, self._plan_request_id = reason, request["request_id"]
        metadata = self._model_metadata()
        self.last_decision = {
            "policy": self.name,
            "observation_id": obs.id,
            "request_id": request["request_id"],
            "valid_action_ids": list(allowed),
            "chosen_action_id": plan[0],
            "chosen_reason": reason,
            "planned_action_ids": list(plan),
            "source": "model_call",
            "uncertainty": uncertainty,
            "model_call_metadata": metadata,
        }
        return Action(action.kind, action.servant, action.skill, action.target, action.cards, reason)

    # Guide sections that apply to one objective only; unlisted sections are shared.
    _SECTION_SCOPES = (("## Farming", "farm_fast"), ("## The standard three-turn procedure", "farm_fast"),
                       ("## Before committing to the NP plan", "farm_fast"), ("## Story battles", "survive_and_win"))

    @classmethod
    def _guide_for(cls, guide: str, objective: str) -> str:
        parts = re.split(r"(?m)^(?=## )", guide)
        kept = [part for part in parts
                if next((scope for prefix, scope in cls._SECTION_SCOPES if part.startswith(prefix)), objective)
                == objective]
        return "".join(kept)

    # ---- Plan-ahead mode -------------------------------------------------

    # Planning mechanics only. What to do comes from the guide's priorities for the
    # objective, so strategy is not repeated (or contradicted) here.
    _PLAN_INSTRUCTIONS = (
        "Schedule EVERY remaining turn of this battle now, one entry per turn from the current turn, "
        "so the battle can be played without asking you again. Each entry: turn (battle turn number), "
        "wave you expect to be fighting that turn, skills (ordered ids to use before Attack) and np "
        "(field slots whose Noble Phantasm to fire that turn, in firing order). "
        "Skill ids use the allowed_actions format s{field slot}-{skill}-t{target}; for a self or party "
        "skill any listed target is fine, for a skill aimed at one ally the target slot matters. "
        "oc-f{field}-b{backline} is Order Change (after it, that field slot holds the incoming servant, "
        "whose skills are all fresh); e{slot} changes the enemy target. Only the CURRENT turn's ids must "
        "appear in allowed_actions; later turns are predictions. "
        "Count cooldowns yourself: a skill used on turn T with cooldown C is ready again on turn T+C. "
        "An observed cooldown outranks catalog cooldown_by_level, whose skill level is unknown (its last "
        "value is the max level). Apply cooldown reductions and NP gauge charges from skill descriptions. "
        "focus (a field slot, or null) names the servant whose face cards fill the rest of that turn's "
        "chain; cards are otherwise picked by a card-chain heuristic that knows nothing about buffs. "
        "An NP can fire only on a turn its servant's gauge has reached 100%. Waves advance only when every "
        "enemy dies; schedule until the last wave is planned cleared. Choose every action by the guide's "
        "priorities for this objective. If the screen later differs from the schedule (a skill or NP not "
        "ready, a different wave, an enemy HP refill or charge jump, an ally death), the battle is "
        "re-planned with that deviation and this schedule.")

    def _from_battle_plan(self, obs: Observation, request: dict, allowed: dict[str, Action]) -> Action | None:
        """Serve the next scheduled step without a model call, or None to fall back."""
        turn_key = self._turn_key(obs)
        if turn_key is None:
            return None
        if obs.phase == "tactical":
            self._note_enemy_refills(obs)
            if self._battle_plan is None or self._deviation is not None:
                if turn_key in self._replanned_turns or self._battle_plans_made >= self._MAX_BATTLE_PLANS:
                    return None
                self._replanned_turns.add(turn_key)
                try:
                    self._make_battle_plan(obs, request, allowed)
                except PlannerError as exc:
                    self._trace("battle_plan_error", {"observation_id": obs.id, "error": str(exc)})
                    self._battle_plan = None
                    return None
            entry = self._battle_plan["turns"].get(obs.turn)
            if entry is None or entry["wave"] != obs.wave:
                self._flag_deviation(obs, f"turn {obs.turn} is wave {obs.wave}, but the plan "
                                          + (f"expected wave {entry['wave']}" if entry else "has no entry for it"))
                return self._from_battle_plan(obs, request, allowed) if turn_key not in self._replanned_turns else None
            # Steps name field slots. Seen live: the front line died, the back line
            # took the slots, and "slot 1 skill 3" cast Oberon's skill instead of the
            # planned Koyanskaya's. A slot holding a different servant voids the plan.
            changed = self._roster_change(obs) or self._vitals_change(obs)
            if changed:
                self._flag_deviation(obs, changed, crisis=True)
                return self._from_battle_plan(obs, request, allowed) if turn_key not in self._replanned_turns else None
            # Seen live (Gareth, then Red Dragon): gauges filled a turn early, the
            # schedule fired nothing, and two full damaging NPs sat unused for three
            # turns while the party died. A ready offensive NP the schedule does not
            # fire this turn is a deviation, so the held-NP check can apply.
            made = self._battle_plan.get("made_at") or {}
            early = (self._early_offensive_nps(obs, entry)
                     if (made.get("wave"), made.get("turn")) != (obs.wave, obs.turn) else [])
            if early and not any(step for step in entry["skills"] if self._issue_key(obs, step) in self._plan_issued):
                self._flag_deviation(obs, f"offensive NP of slot(s) {early} is ready on turn {obs.turn}, "
                                          "but the schedule does not fire it")
                return self._from_battle_plan(obs, request, allowed) if turn_key not in self._replanned_turns else None
            for step in entry["skills"]:
                if self._issue_key(obs, step) in self._plan_issued:
                    continue
                action_id = self._match_planned_step(step, allowed)
                if action_id is None:
                    if step.startswith("e"):
                        self._plan_issued.add(self._issue_key(obs, step))
                        continue
                    self._flag_deviation(obs, f"planned {step} on turn {obs.turn} is not available")
                    return self._from_battle_plan(obs, request, allowed) if turn_key not in self._replanned_turns else None
                self._plan_issued.add(self._issue_key(obs, step))
                if allowed[action_id].kind == "order_change":
                    self._expect_order_change(obs, allowed[action_id])
                return self._scheduled(obs, action_id, allowed[action_id], f"battle plan turn {obs.turn}: {step}")
            return self._scheduled(obs, "attack", allowed["attack"], f"battle plan turn {obs.turn}: attack")
        if obs.phase == "cards" and self._battle_plan is not None and self._deviation is None:
            entry = self._battle_plan["turns"].get(obs.turn)
            if entry is None:
                return None
            wanted = {5 + slot for slot in entry["np"]}
            # Rank every legal triple, not the model shortlist, which can omit the planned NP.
            legal = {i: a for i, a in self._allowed_actions(obs).items() if a.kind == "cards"}
            by_card = {c.get("slot"): c for c in obs.cards if c.get("slot") in range(1, 9)}
            try:
                ranked = sorted(legal, key=lambda i: (-float(self._scorer._score(list(legal[i].cards), by_card, obs)), i))
            except Exception:
                return None
            nps = lambda i: {slot for slot in legal[i].cards if slot >= 6}
            # User request (2026-09-26): choose by estimated damage, so a buffed
            # servant's cards beat an unbuffed NP when they hit harder.
            if self.objective == "survive_and_win":
                estimate = {i: self._chain_estimate(obs, legal[i].cards) for i in ranked}
                if any(v is not None for v in estimate.values()):
                    ranked = sorted(ranked, key=lambda i: -(estimate[i] or 0))
            # The planned focus servant (the one whose time-limited buffs matter this
            # turn) fills the chain's face-card slots first; the scorer breaks ties.
            focus = entry.get("focus")
            if focus in (1, 2, 3):
                owned = lambda i: sum(1 for slot in legal[i].cards
                                      if slot <= 5 and by_card.get(slot, {}).get("servant") == focus)
                ranked = sorted(ranked, key=lambda i: -owned(i))
            # Cards run left to right. Seen live (Morgan): Oberon's NP, whose own text
            # applies Invincible [Demerit] to all enemies, fired first and the two NPs
            # after it did almost nothing. The plan's NP list is the firing order, and
            # an NP that buffs the enemies as a demerit always goes last.
            order = [5 + slot for slot in entry["np"]]
            demerit = {5 + slot for slot in entry["np"] if self._np_helps_enemies(slot, obs)}
            in_order = lambda i: ([c for c in legal[i].cards if c >= 6]
                                  == [c for c in order if c in legal[i].cards])
            demerit_last = lambda i: all(legal[i].cards[-1] == c for c in demerit if c in legal[i].cards)
            ranked = sorted(ranked, key=lambda i: (not demerit_last(i), not in_order(i)))
            exact = [i for i in ranked if nps(i) == wanted]
            if not exact:
                self._flag_deviation(obs, f"planned NP slots {sorted(entry['np'])} on turn {obs.turn} are not all ready")
                exact = [i for i in ranked if nps(i) <= wanted]
            if not exact:
                return None
            choice = exact[0]
            return self._scheduled(obs, choice, legal[choice], f"battle plan turn {obs.turn}: NP {sorted(entry['np'])}")
        return None

    # A skill whose benefit ends with the turn or whose demerit takes its target
    # out right after: NP gauge drained at the end of the turn, Eternal Slumber,
    # or a sleep/stun demerit (not one delayed by 2+ turns). In the NA catalog:
    # Oberon S2/S3, Tamamo (Lancer) S3, Ishtar (Rider) S3, Passionlip S3.
    _NOW_OR_NEVER_TEXT = re.compile(
        r"decrease NP Gauge at the end of the turn|Eternal Slumber"
        r"|(?:Sleep|Stun)(?![^&+]*(?:after [2-9] turns|[2-9] turns later))[^&+]*\[Demerit\]", re.IGNORECASE)

    def _now_or_never_issue(self, turns: dict[int, dict], obs: Observation) -> str | None:
        """A now-or-never skill aimed at an ally whose NP does not fire that same turn."""
        ids = {a.get("slot"): a.get("id") for a in obs.allies if type(a.get("id")) is int}
        problems = []
        for turn, entry in sorted(turns.items()):
            for step in entry["skills"]:
                if step.startswith("oc-"):
                    return self._now_or_never_text(problems)  # the field changes after a swap
                match = re.fullmatch(r"s([123])-([123])-t([123])", step)
                if not match:
                    continue
                caster, skill, target = (int(v) for v in match.groups())
                catalog = self.strategy_catalog.get(ids.get(caster), self.strategy_catalog.get(str(ids.get(caster)), {}))
                texts = [str(v.get("description", "")) for row in (catalog or {}).get("skills") or []
                         if isinstance(row, dict) and row.get("slot") == skill
                         for v in row.get("variants") or [] if isinstance(v, dict)]
                if any(self._NOW_OR_NEVER_TEXT.search(text) for text in texts) and target not in entry["np"]:
                    problems.append((turn, step, target))
        return self._now_or_never_text(problems)

    @staticmethod
    def _now_or_never_text(problems) -> str | None:
        if not problems:
            return None
        listed = ", ".join(f"{step} on turn {turn}" for turn, step, _ in problems[:4])
        return (f"Your plan casts {listed}, whose own text ends its target's turn (NP gauge drained, buffs "
                "removed, or the target put to sleep or stunned), but that target's NP does not fire that same "
                "turn. Such a skill is a final attack: fire the target's NP that turn and stack the caster's "
                "other buffs and charges on the same target with it, or keep the skill for the turn when that "
                "all-in attack is possible.")

    _DAMAGE_TEXT = re.compile(r"\bdeal\b[^+&]*?damage", re.IGNORECASE)
    @staticmethod
    def _np_demerits(catalog: dict) -> list[str]:
        """The [Demerit] clauses of the servant's NP text, pulled out so they are not missed.

        User feedback 2026-09-25: some NPs kill their own user (Arash), sacrifice an
        ally (Chen Gong) or buff the enemies (Oberon's Invincible). The full text was
        already sent, but buried in a long description.
        """
        found = []
        for n in (catalog or {}).get("noble_phantasms") or []:
            if not isinstance(n, dict):
                continue
            for clause in re.split(r"\s+[&+]\s+", str(n.get("description", ""))):
                clause = " ".join(clause.replace("\u25b2", "").split())
                if "[Demerit]" in clause and clause not in found:
                    found.append(clause)
        return found[:4]

    _ENEMY_DEMERIT_TEXT = re.compile(r"\[Demerit\]\s*(?:to|for)\s+(?:all\s+)?enem", re.IGNORECASE)

    def _np_helps_enemies(self, slot: int, obs: Observation) -> bool:
        """The NP's own text applies a [Demerit] effect to enemies (e.g. Invincible)."""
        ally = next((a for a in obs.allies if a.get("slot") == slot), None)
        if not ally or type(ally.get("id")) is not int:
            return False
        catalog = self.strategy_catalog.get(ally["id"], self.strategy_catalog.get(str(ally["id"]), {}))
        return any(self._ENEMY_DEMERIT_TEXT.search(str(n.get("description", "")))
                   for n in (catalog or {}).get("noble_phantasms") or [] if isinstance(n, dict))

    @classmethod
    def _np_role(cls, catalog: dict) -> str | None:
        """"offensive" when any NP version deals damage, "support" otherwise, None if unknown."""
        texts = [str(n.get("description", "")) for n in (catalog or {}).get("noble_phantasms") or []
                 if isinstance(n, dict)]
        texts = [t for t in texts if t.strip() and t.strip().lower() != "unknown"]
        if not texts:
            return None
        return "offensive" if any(cls._DAMAGE_TEXT.search(t) for t in texts) else "support"

    # Share of the HP bar lost since the plan that means the plan misjudged the danger.
    _HEAVY_HP_LOSS = 0.5

    def _vitals_change(self, obs: Observation) -> str | None:
        """An ally the plan counted on died, or lost half its HP bar since the plan.

        Seen live: Mash died on turn 3/4 and the executor kept following a plan that
        reserved her whole kit for turn 6; a dead slot reads id None, so the roster
        check alone never noticed.
        """
        vitals = self._battle_plan.get("vitals", {})
        for ally in obs.allies:
            slot, before = ally.get("slot"), vitals.get(ally.get("slot"))
            if not isinstance(before, (int, float)) or before <= 0:
                continue
            now = ally.get("hp_fraction")
            dead = ally.get("alive") is False or now == 0 or (ally.get("id") is None and ally.get("hp") is None
                                                                and not now)
            if dead:
                return f"ally in slot {slot} died since the plan was made"
            if isinstance(now, (int, float)) and before - now >= self._HEAVY_HP_LOSS:
                return (f"ally in slot {slot} fell from {round(before * 100)}% to {round(now * 100)}% HP "
                        "since the plan was made")
        return None

    def _held_np_issue(self, turns: dict[int, dict], obs: Observation) -> str | None:
        """A charged OFFENSIVE NP left unfired this turn and next.

        Support NPs (invincibility, stun, damage cut, buffs) are meant to be saved
        for their moment, so only NPs that deal damage are checked.
        """
        from .card_rules import np_sealed_allies
        sealed = np_sealed_allies(obs.signals if isinstance(obs.signals, dict) else {}, obs.turn)
        danger = [turn for turn, _ in self._danger_turns(obs)]
        soon = {n for t in (obs.turn, obs.turn + 1) for n in turns.get(t, {}).get("np", [])}
        held = []
        for ally in self._living_allies(obs):
            slot, gauge = ally["slot"], ally.get("np")
            if not isinstance(gauge, (int, float)) or gauge < 100 or slot in sealed or slot in soon:
                continue
            catalog = self.strategy_catalog.get(ally["id"], self.strategy_catalog.get(str(ally["id"]), {}))
            if self._np_role(catalog if isinstance(catalog, dict) else {}) != "offensive":
                continue
            first = min((t for t, e in turns.items() if slot in e["np"]), default=None)
            if first is not None and danger and first == danger[0]:
                continue  # a defensive NP kept for the enemy's NP turn
            held.append(slot)
        if not held:
            return None
        return (f"Slot(s) {held} have a full OFFENSIVE NP gauge now, but your plan does not fire it this turn or "
                "next. Delayed damage is lost damage, especially against an enemy that heals. Fire it at the best "
                "moment within the next two turns, or say in reason why a later turn is clearly better.")

    @staticmethod
    def _issue_key(obs: Observation, step: str) -> tuple:
        """A planned step once issued this turn, tied to the servant in its slot.

        Seen live (triple Berserker): "s1-3-t3" was Koyanskaya's skill before an
        Order Change and Oberon's after it; keyed by slot alone, Oberon's was
        treated as already done.
        """
        ident = None
        if step.startswith("s") and step[1:2].isdigit():
            ident = next((a.get("id") for a in obs.allies if a.get("slot") == int(step[1])), None)
        return (obs.turn, step, ident)

    def _early_offensive_nps(self, obs: Observation, entry: dict) -> list[int]:
        from .card_rules import np_sealed_allies
        sealed = np_sealed_allies(obs.signals if isinstance(obs.signals, dict) else {}, obs.turn)
        early = []
        for ally in self._living_allies(obs):
            slot, gauge = ally["slot"], ally.get("np")
            if (not isinstance(gauge, (int, float)) or gauge < 100 or slot in sealed or slot in entry["np"]
                    or slot in self._early_np_flagged.get(obs.turn, set())):
                continue
            catalog = self.strategy_catalog.get(ally["id"], self.strategy_catalog.get(str(ally["id"]), {}))
            if self._np_role(catalog if isinstance(catalog, dict) else {}) == "offensive":
                early.append(slot)
        if early:
            self._early_np_flagged.setdefault(obs.turn, set()).update(early)
        return early

    def _roster_change(self, obs: Observation) -> str | None:
        roster = self._battle_plan.get("roster", {})
        for ally in obs.allies:
            slot, ident = ally.get("slot"), ally.get("id")
            expected = roster.get(slot)
            if expected is not None and type(ident) is int and ident != expected:
                return f"field slot {slot} now holds servant {ident}; the plan was made for servant {expected}"
        return None

    def _expect_order_change(self, obs: Observation, action: Action) -> None:
        """A planned swap legitimately changes the slot's servant; expect the incoming one."""
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        incoming = next((m.get("servant_id") for m in signals.get("backline") or []
                         if isinstance(m, dict) and m.get("slot") == action.target), None)
        roster = self._battle_plan.setdefault("roster", {})
        if type(incoming) is int:
            roster[action.servant] = incoming
        else:
            roster.pop(action.servant, None)

    @staticmethod
    def _match_planned_step(step: str, allowed: dict[str, Action]) -> str | None:
        if step in allowed:
            return step
        if step.startswith("s"):
            # A self or party skill has one fixed target; accept it whatever the plan wrote.
            prefix = step.rsplit("-t", 1)[0] + "-t"
            options = [identifier for identifier in allowed if identifier.startswith(prefix)]
            return options[0] if len(options) == 1 else None
        return None

    def _note_enemy_refills(self, obs: Observation) -> None:
        # Seen live (Mors horde): each new spawn in a slot read as an HP refill and
        # forced a crisis re-plan. When the game's enemy count fell since the last
        # frame, enemies died and new ones filled the slots; that is no refill.
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        remaining = signals.get("enemies_remaining")
        respawned = (type(remaining) is int and type(self._last_remaining) is int
                     and remaining < self._last_remaining)
        if type(remaining) is int:
            self._last_remaining = remaining
        for enemy in obs.enemies:
            slot, hp = enemy.get("slot"), enemy.get("hp")
            if type(hp) is not int or hp <= 0:
                continue
            previous = self._plan_enemy_hp.get(slot)
            if previous is not None and hp > previous and self._battle_plan is not None:
                # A new wave replaces enemies legitimately; within a planned wave a
                # rise means a refill, revive or reinforcement the plan did not know.
                entry = self._battle_plan["turns"].get(obs.turn)
                if (entry is not None and entry["wave"] == obs.wave and self._plan_enemy_wave == obs.wave
                        and not respawned):
                    self._flag_deviation(obs, f"enemy {slot} HP rose from {previous} to {hp} within wave {obs.wave}",
                                         crisis=True)
            self._plan_enemy_hp[slot] = hp
            charge = enemy.get("charge")
            if isinstance(charge, (list, tuple)) and len(charge) == 2 and type(charge[0]) is int:
                seen = self._plan_enemy_charge.get(slot)
                # Seen in Demeter's fight: a skill adds 3 charge at once, so the enemy
                # NP comes turns earlier than the plan counted.
                # Seen live (3x Morgan): a +3 jump every turn re-planned every turn and
                # spent the plan budget by turn 6. Once measured, that rate is expected.
                if (seen is not None and self._battle_plan is not None and self._plan_enemy_wave == obs.wave
                        and obs.turn == seen[0] + 1 and charge[0] - seen[1] >= 2
                        and charge[0] - seen[1] > self._charge_gain.get(slot, 1)):
                    self._flag_deviation(obs, f"enemy {slot} charge jumped from {seen[1]} to {charge[0]} in one "
                                              "turn, so its NP comes sooner than planned", crisis=True)
                if (seen is not None and self._plan_enemy_wave == obs.wave and obs.turn == seen[0] + 1
                        and charge[0] - seen[1] > 1):
                    self._charge_gain[slot] = max(self._charge_gain.get(slot, 1), charge[0] - seen[1])
                if seen is None or seen[0] != obs.turn:
                    self._plan_enemy_charge[slot] = (obs.turn, charge[0])
        if self._plan_enemy_wave != obs.wave:
            self._charge_gain = {}
        self._plan_enemy_wave = obs.wave
        if self._charge_gain and isinstance(obs.signals, dict):
            obs.signals["enemy_charge_gain_per_turn"] = dict(self._charge_gain)

    def _flag_deviation(self, obs: Observation, reason: str, crisis: bool = False) -> None:
        """Record why the plan no longer fits. A crisis (an ally died or lost half its
        HP, a slot holds a new servant, an enemy refilled) gets a medium-effort
        re-plan; a minor slip (a skill or NP not ready, the plan ran out) gets low."""
        if self._deviation is None:
            self._deviation = reason
            self._deviation_crisis = crisis
            self._trace("battle_plan_deviation", {"observation_id": obs.id, "reason": reason, "crisis": crisis})

    def _scheduled(self, obs: Observation, identifier: str, action: Action, reason: str) -> Action:
        self._register_pending(obs, action)
        self.last_decision = {"policy": self.name, "observation_id": obs.id,
                              "request_id": self._battle_plan.get("request_id") if self._battle_plan else None,
                              "chosen_action_id": identifier, "chosen_reason": reason,
                              "source": "battle_plan", "model_call_metadata": None}
        self._trace("planner_scheduled_action", self.last_decision)
        return Action(action.kind, action.servant, action.skill, action.target, action.cards, reason)

    def _make_battle_plan(self, obs: Observation, request: dict, allowed: dict[str, Action]) -> None:
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        # Medium reasoning is kept for writing a plan from scratch and for crises;
        # patching an existing plan after a minor slip is cheaper at low.
        opening = self._battle_plan is None
        planning = {"current_turn": obs.turn, "current_wave": obs.wave,
                    "total_waves": signals.get("stage_total"),
                    "instructions": self._PLAN_INSTRUCTIONS,
                    "reasoning": "medium" if opening or self._deviation_crisis else "low"}
        if signals.get("enemy_charge_gain_per_turn"):
            planning["enemy_charge_gain_per_turn"] = signals["enemy_charge_gain_per_turn"]
        if self._battle_plan is not None:
            planning["previous_plan"] = [{"turn": t, **{k: v for k, v in e.items()}}
                                         for t, e in sorted(self._battle_plan["turns"].items())]
            planning["deviation"] = self._deviation
        request = {key: value for key, value in request.items()
                   if key not in ("request_id", "card_candidates")}
        request.update(task="battle_plan", planning=planning)
        request["request_id"] = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                                          ensure_ascii=True).encode()).hexdigest()
        schema = self._battle_plan_schema()
        # The budget counts plans, not calls: a revision belongs to its plan. Seen
        # live: two crises plus revisions spent all five by turn 5, and every later
        # turn fell back to one model call per action, which costs more.
        self._battle_plans_made += 1
        for attempt in (1, 2):
            self._trace("planner_request", request)
            try:
                response = self.backend.choose(request, schema)
            except Exception as exc:
                raise PlannerError(f"battle plan backend failed: {type(exc).__name__}") from exc
            self._trace("planner_response", self._safe_response(response))
            turns = self._validate_battle_plan(response, request, obs, allowed)
            # Seen live: a plan left a whole wave to face cards and it survived an
            # extra turn. Ask once for a revision; a second answer stands, since an
            # NP genuinely may not be chargeable in time (seals, empty gauges).
            idle = sorted({e["wave"] for e in turns.values()}
                          - {e["wave"] for e in turns.values() if e["np"]})
            # Seen live (Black Wolf): the opening plan spent the only invincibility
            # and every buff on turn 1, and nothing defensive was left when the
            # boss reached full charge. Story battles are lost to that.
            defense = self._defense_issue(turns, obs) if self.objective == "survive_and_win" else None
            # Seen live (Black Wolf): the plan ended at turn 4 expecting two NPs to kill
            # a regenerating, damage-cutting boss, which then out-lasted the party.
            durable = self._durability_issue(turns, obs) if self.objective == "survive_and_win" else None
            # Seen live (Black Wolf rematch): Castoria at 115% and Draco at 100% NP were
            # held for turns while normal attacks killed the party.
            held = self._held_np_issue(turns, obs) if self.objective == "survive_and_win" else None
            # User feedback (Morgan): Oberon's kit is a final attack. Its charge drains
            # and its buff ends in Eternal Slumber at the end of the turn, yet half of
            # it was cast without the target firing its NP that turn.
            now_or_never = self._now_or_never_issue(turns, obs)
            if (not idle and not defense and not durable and not held and not now_or_never) or attempt == 2:
                break
            issues = []
            if idle:
                issues.append(f"Your plan fires no Noble Phantasm in wave(s) {idle}. Face cards rarely clear a "
                              "wave, and a surviving wave costs extra turns and HP. Revise so each wave has the NPs "
                              "that clear it, or keep the schedule only if no NP can be charged in time and say "
                              "why in reason.")
            if defense:
                issues.append(defense)
            if durable:
                issues.append(durable)
            if held:
                issues.append(held)
            if now_or_never:
                issues.append(now_or_never)
            # A revision answers explicit feedback, so it does not need medium effort.
            planning = dict(request["planning"], feedback=" ".join(issues), reasoning="low")
            request = {key: value for key, value in request.items() if key not in ("request_id", "planning")}
            request["planning"] = planning
            request["request_id"] = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                                              ensure_ascii=True).encode()).hexdigest()
            self._trace("battle_plan_revision", {"observation_id": obs.id, "idle_waves": idle,
                                                 "defense": defense, "durable": durable, "held": held,
                                                 "now_or_never": now_or_never})
        self._battle_plan = {"turns": turns, "reason": response["reason"], "request_id": request["request_id"],
                             "made_at": {"wave": obs.wave, "turn": obs.turn},
                             "roster": {a["slot"]: a["id"] for a in obs.allies
                                        if a.get("slot") in (1, 2, 3) and type(a.get("id")) is int},
                             "vitals": {a["slot"]: a.get("hp_fraction") for a in self._living_allies(obs)
                                        if isinstance(a.get("hp_fraction"), (int, float))}}
        self._deviation = None
        self._deviation_crisis = False
        self._trace("battle_plan", {"observation_id": obs.id, "request_id": request["request_id"],
                                    "reason": response["reason"], "uncertainty": response["uncertainty"],
                                    "turns": [{"turn": t, **e} for t, e in sorted(turns.items())],
                                    "model_call_metadata": self._model_metadata()})

    # Catalog wording for effects that keep the party alive through an enemy NP.
    _DEFENSIVE_TEXT = re.compile(r"invincib|evade|evasion|damage cut|decrease damage taken|reduce damage taken"
                                 r"|target focus|taunt|guts|increase def|def state", re.IGNORECASE)
    # A multi-turn damage buff in the same skill. Seen live (Red Dragon): Castoria's
    # Sword of Selection (Arts up 3 turns + special attack 3 turns + single-ally
    # Invincible 1 turn) was held as "the defensive answer" and never boosted Draco.
    _LASTING_DAMAGE_BUFF = re.compile(r"(?:card effectiveness|\bATK\b|NP Strength|Special Attack)[^&+]*\([2-9] turns?\)",
                                      re.IGNORECASE)
    _AVOIDANCE_ONLY = re.compile(r"invincib|evade|evasion", re.IGNORECASE)
    _CHARGE_RAISE_TEXT = re.compile(r"increase Charge[^&+]*enem", re.IGNORECASE)

    def _defensive_options(self, obs: Observation) -> tuple[set[str], set[int]]:
        """("s{slot}-{skill}" defensive skills ready now, field slots with a defensive NP)."""
        skills, nps = set(), set()
        for ally in self._living_allies(obs):
            catalog = self.strategy_catalog.get(ally["id"], self.strategy_catalog.get(str(ally["id"]), {}))
            catalog = catalog if isinstance(catalog, dict) else {}
            ready = {s.get("slot") for s in ally.get("skills", []) if isinstance(s, dict) and s.get("ready") is True}
            for skill in catalog.get("skills") or []:
                texts = " ".join(str(v.get("description", "")) for v in skill.get("variants", []) if isinstance(v, dict))
                if skill.get("slot") in ready and self._DEFENSIVE_TEXT.search(texts):
                    # Only avoidance (invincible/evade) plus a lasting damage buff: its
                    # value is mainly offensive, so it is not the reserve answer.
                    if (self._LASTING_DAMAGE_BUFF.search(texts)
                            and not self._DEFENSIVE_TEXT.search(self._AVOIDANCE_ONLY.sub("", texts))):
                        continue
                    skills.add(f"s{ally['slot']}-{skill['slot']}")
            # Clauses that buff the ENEMIES (Oberon: "apply Invincible (1 turn)
            # [Demerit] to all enemies") are not the team's protection.
            own_text = lambda text: " & ".join(c for c in re.split(r"\s+[&+]\s+", text)
                                               if not self._ENEMY_DEMERIT_TEXT.search(c))
            if any(self._DEFENSIVE_TEXT.search(own_text(str(n.get("description", ""))))
                   for n in catalog.get("noble_phantasms") or [] if isinstance(n, dict)):
                nps.add(ally["slot"])
        return skills, nps

    # Enemy-profile wording for effects that make a kill slower than its HP suggests.
    _DURABLE_TEXT = re.compile(r"restore hp|recover hp|hp recovery|regenerat|damage cut|decrease damage taken"
                               r"|resistance to all|resist|guts|revive|invincib|evade|evasion", re.IGNORECASE)

    @staticmethod
    def _danger_turns(obs: Observation, raises: dict[int, int] | None = None) -> list[tuple[int, int]]:
        """(turn its NP fires, slot) for each living enemy with a readable charge meter.

        An enemy at charge c of m at the start of turn T is full at the start of
        turn T + (m - c) and fires in that turn's enemy phase.
        """
        # Seen live (Morgan): her charge rose by 2-3 a turn, so "+1 a turn" put her
        # NP a turn or two late and the saved defence came too late, repeatedly.
        # The largest gain observed this wave is used instead.
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        rates = signals.get("enemy_charge_gain_per_turn") or {}
        danger = []
        for enemy in obs.enemies:
            charge = enemy.get("charge")
            if (isinstance(charge, (list, tuple)) and len(charge) == 2 and all(type(v) is int for v in charge)
                    and charge[1] > 0 and type(enemy.get("hp")) is int and enemy["hp"] > 0):
                rate = max(1, rates.get(enemy.get("slot")) or rates.get(str(enemy.get("slot"))) or 1)
                # Seen live (Red Dragon): the plan's own "increase Charge for all
                # enemies" skill filled the dragon a turn early, and the support NP
                # saved for the next turn came too late. Planned raises count.
                level, turn = charge[0], obs.turn
                for _ in range(30):
                    level += (raises or {}).get(turn, 0)
                    if level >= charge[1]:
                        break
                    level, turn = level + rate, turn + 1
                danger.append((turn, enemy.get("slot")))
        return sorted(danger)

    def _planned_charge_raises(self, turns: dict[int, dict], obs: Observation) -> dict[int, int]:
        """Turn -> number of planned skills whose text raises every enemy's charge."""
        ids = {a.get("slot"): a.get("id") for a in obs.allies if type(a.get("id")) is int}
        raises: dict[int, int] = {}
        for turn, entry in turns.items():
            for step in entry["skills"]:
                if step.startswith("oc-"):
                    return raises
                match = re.fullmatch(r"s([123])-([123])-t[123]", step)
                if not match:
                    continue
                caster, skill = int(match.group(1)), int(match.group(2))
                catalog = self.strategy_catalog.get(ids.get(caster), self.strategy_catalog.get(str(ids.get(caster)), {}))
                texts = " ".join(str(v.get("description", "")) for row in (catalog or {}).get("skills") or []
                                 if isinstance(row, dict) and row.get("slot") == skill
                                 for v in row.get("variants") or [] if isinstance(v, dict))
                if self._CHARGE_RAISE_TEXT.search(texts):
                    raises[turn] = raises.get(turn, 0) + 1
        return raises

    def _durability_issue(self, turns: dict[int, dict], obs: Observation) -> str | None:
        """Why the plan ends before a durable enemy's first NP, or None."""
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        durable = {}
        for profile in signals.get("enemy_profiles") or []:
            if not isinstance(profile, dict):
                continue
            hits = [str(e.get("title", "")) for e in profile.get("effects") or [] if isinstance(e, dict)
                    and self._DURABLE_TEXT.search(f"{e.get('title', '')} {e.get('text', '')}")]
            if hits:
                durable[profile.get("slot")] = hits
        last = max(turns)
        for turn, slot in self._danger_turns(obs):
            if slot in durable and turn > last:
                return (f"Enemy {slot} has {durable[slot][:4]}, which make it outlast its HP, but your plan ends on "
                        f"turn {last} as if it dies by then, before its Noble Phantasm on turn {turn}. Extend the "
                        f"schedule through turn {turn} with sustain and defence in case it survives.")
        return None

    def _defense_issue(self, turns: dict[int, dict], obs: Observation) -> str | None:
        """Why the plan leaves no defence for the first expected enemy NP, or None.

        An enemy at charge c of m at the start of turn T is full at the start of
        turn T + (m - c) and fires in that turn's enemy phase. Only a plan that
        spends every ready defensive skill and defensive NP before that turn AND
        schedules none on it is sent back; one answer kept or used then is enough.
        """
        raises = self._planned_charge_raises(turns, obs)
        danger = self._danger_turns(obs, raises)
        if not danger:
            return None
        turn, slot = danger[0]
        if turn not in turns and turn <= max(turns):
            return None
        skills, nps = self._defensive_options(obs)
        if not skills and not nps:
            return None
        prefix = lambda step: step.rsplit("-t", 1)[0]
        spent_skills = {prefix(step) for t, e in turns.items() if t < turn for step in e["skills"]} & skills
        spent_nps = {n for t, e in turns.items() if t < turn for n in e["np"]} & nps
        # A danger turn past the plan's end still counts: everything planned is spent before it.
        entry = turns.get(turn, {"skills": [], "np": []})
        on_turn = ({prefix(step) for step in entry["skills"]} & skills) or (set(entry["np"]) & nps)
        if on_turn or spent_skills != skills or spent_nps != nps:
            return None
        raised = " (counting your own planned skills that raise enemy charge)" if raises.get(turn) else ""
        return (f"Enemy {slot} reaches full charge and fires its Noble Phantasm on turn {turn}{raised}, but your plan spends "
                f"every defensive option (skills {sorted(skills)}, NPs of slots {sorted(nps)}) before then and "
                f"schedules none on turn {turn}. Keep one defensive answer for turn {turn} or use it then, unless "
                "that enemy will certainly be dead first; if so, say why in reason.")

    @classmethod
    def _battle_plan_schema(cls) -> dict:
        step = {"type": "string", "pattern": cls._PLANNED_STEP.pattern}
        entry = {"type": "object", "additionalProperties": False,
                 "required": ["turn", "wave", "skills", "np", "focus"],
                 "properties": {"turn": {"type": "integer", "minimum": 1, "maximum": 99},
                                "wave": {"type": "integer", "minimum": 1, "maximum": 9},
                                "skills": {"type": "array", "maxItems": 12, "items": step},
                                "np": {"type": "array", "maxItems": 3,
                                       "items": {"type": "integer", "minimum": 1, "maximum": 3}},
                                "focus": {"type": ["integer", "null"], "minimum": 1, "maximum": 3}}}
        return {"type": "object", "additionalProperties": False,
                "required": ["observation_id", "request_id", "turns", "reason", "uncertainty"],
                "properties": {"observation_id": {"type": "string"},
                               "request_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                               "turns": {"type": "array", "minItems": 1, "maxItems": cls._MAX_PLAN_TURNS,
                                         "items": entry},
                               "reason": {"type": "string", "maxLength": cls._MAX_REASON},
                               "uncertainty": {"type": "array", "maxItems": 8,
                                               "items": {"type": "string", "maxLength": 160}}}}

    def _validate_battle_plan(self, response: object, request: dict, obs: Observation,
                              allowed: dict[str, Action]) -> dict[int, dict]:
        if not isinstance(response, dict) or set(response) != {"observation_id", "request_id", "turns",
                                                               "reason", "uncertainty"}:
            raise PlannerError("battle plan has missing or extraneous keys")
        if response["observation_id"] != request["observation_id"] or response["request_id"] != request["request_id"]:
            raise PlannerError("battle plan binding does not match request")
        if not isinstance(response["reason"], str) or len(response["reason"]) > self._MAX_REASON:
            raise PlannerError("battle plan reason is invalid")
        uncertainty = response["uncertainty"]
        if (not isinstance(uncertainty, list) or len(uncertainty) > 8
                or any(not isinstance(item, str) or len(item) > 160 for item in uncertainty)):
            raise PlannerError("battle plan uncertainty is invalid")
        rows = response["turns"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= self._MAX_PLAN_TURNS:
            raise PlannerError("battle plan turns are invalid")
        turns: dict[int, dict] = {}
        previous_turn, previous_wave = obs.turn - 1, obs.wave
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"focus"} != {"turn", "wave", "skills", "np"}:
                raise PlannerError("battle plan entry is malformed")
            focus = row.get("focus")
            if focus is not None and (type(focus) is not int or focus not in (1, 2, 3)):
                raise PlannerError("battle plan focus is invalid")
            turn, wave, skills, nps = row["turn"], row["wave"], row["skills"], row["np"]
            if type(turn) is not int or turn != previous_turn + 1:
                raise PlannerError("battle plan turns must be consecutive from the current turn")
            if type(wave) is not int or wave < previous_wave or wave > 9:
                raise PlannerError("battle plan waves must not go backwards")
            if (not isinstance(skills, list) or len(skills) > 12
                    or any(not isinstance(s, str) or not self._PLANNED_STEP.match(s) for s in skills)):
                raise PlannerError("battle plan skills are invalid")
            # A slot's skill may repeat only after an Order Change put a new servant there.
            segment: list[str] = []
            for step in skills:
                if step.startswith("oc-"):
                    segment = []
                elif step in segment:
                    raise PlannerError("battle plan skills are invalid")
                else:
                    segment.append(step)
            if (not isinstance(nps, list) or len(set(nps)) != len(nps)
                    or any(type(n) is not int or n not in (1, 2, 3) for n in nps)):
                raise PlannerError("battle plan NP slots are invalid")
            turns[turn] = {"wave": wave, "skills": list(skills), "np": list(nps), "focus": focus}
            previous_turn, previous_wave = turn, wave
        first = turns[obs.turn]
        if first["wave"] != obs.wave:
            raise PlannerError("battle plan's first turn is not the current wave")
        for step in first["skills"]:
            if step.startswith("oc-"):
                break  # after Order Change the field changes; later steps are re-derived
            if not step.startswith("e") and self._match_planned_step(step, allowed) is None:
                raise PlannerError(f"battle plan's first step {step} is not available now")
        return turns

    def _register_pending(self, obs: Observation, action: Action) -> None:
        """Record the repeat-prevention key the controller will later confirm."""
        if action.kind == "skill":
            self._pending[(action.servant, action.skill)] = self._history_key(obs, action)
        elif action.kind == "enemy_target" and action.target is not None:
            turn_key = self._turn_key(obs)
            if turn_key is not None:
                self._pending_enemy_targets[action.target] = turn_key

    def _dequeue(self, obs: Observation, allowed: dict[str, Action]) -> Action | None:
        """Serve the next planned action for this same phase and turn, if still legal."""
        if not self._plan_queue or self._plan_key != (obs.phase, self._turn_key(obs)):
            self._plan_queue, self._plan_key = [], None
            return None
        while self._plan_queue:
            identifier = self._plan_queue.pop(0)
            action = allowed.get(identifier)
            if action is None:
                # Already confirmed, or no longer offered by this screen. Skip it
                # rather than sending an action the current observation disallows.
                self._trace("planner_plan_skipped", {"observation_id": obs.id, "action_id": identifier})
                continue
            self._register_pending(obs, action)
            self.last_decision = {
                "policy": self.name,
                "observation_id": obs.id,
                "request_id": self._plan_request_id,
                "chosen_action_id": identifier,
                "chosen_reason": self._plan_reason,
                "remaining_plan": list(self._plan_queue),
                "source": "queued_turn_plan",
                "model_call_metadata": None,
            }
            self._trace("planner_queued_action", self.last_decision)
            return Action(action.kind, action.servant, action.skill, action.target, action.cards, self._plan_reason)
        self._plan_key = None
        return None

    def _track_field(self, obs: Observation) -> None:
        """Keep the buff tracker in step with the turn and the field's servants."""
        if obs.phase not in ("tactical", "cards"):
            return
        self._calibrate(obs)
        self._track_incoming(obs)
        self._tracker.expire(obs.turn)
        for ally in obs.allies:
            slot, ident = ally.get("slot"), ally.get("id")
            if slot in (1, 2, 3) and type(ident) is int:
                if self._tracker_ids.get(slot) not in (None, ident):
                    self._tracker.forget_slot(slot)
                if self._tracker_ids.get(slot) != ident:
                    self._assign_party_member(slot, ident)
                if self._tracker_ids.get(slot) != ident and type(obs.turn) is int:
                    # A servant entering the field brings its class passives (permanent).
                    self._tracker.apply(self._catalog_entry(ident).get("passives"), caster=slot, target=slot,
                                        turn=obs.turn, level_index=1, field_slots=[slot], permanent=True)
                self._tracker_ids[slot] = ident
        self._last_obs = obs

    def _assign_party_member(self, slot: int, ident: int) -> None:
        """Which Confirm Party column now stands in a field slot.

        Starting members hold slots 1-3 at the start. A new servant in a slot came
        from the back line: the unused sub member of the same class, else the first.
        """
        if not self._party:
            return
        used = {id(m) for m in self._party_by_slot.values()}
        if slot not in self._party_seen_slots:
            self._party_seen_slots.add(slot)
            starting = next((m for m in self._party if m.get("group") == "starting" and m.get("slot") == slot), None)
            from .identity import FGOPY_CLASS_NAMES
            read_class = FGOPY_CLASS_NAMES.get((starting or {}).get("class_index"))
            field_class = self._catalog_entry(ident).get("class_name")
            if starting is not None and read_class and field_class and read_class != field_class:
                # A saved read from another party: stale numbers would mislead. Drop it.
                self._trace("party_mismatch", {"slot": slot, "party_class": read_class, "field_class": field_class})
                self._party, self._party_by_slot = [], {}
                return
            if starting is not None and id(starting) not in used:
                self._party_by_slot[slot] = starting
                return
        self._party_by_slot.pop(slot, None)
        used = {id(m) for m in self._party_by_slot.values()}
        from .identity import FGOPY_CLASS_NAMES
        wanted = self._catalog_entry(ident).get("class_name")
        subs = [m for m in self._party if m.get("group") == "sub" and id(m) not in used]
        match = next((m for m in subs if FGOPY_CLASS_NAMES.get(m.get("class_index")) == wanted), None)
        chosen = match or (subs[0] if subs else None)
        if chosen is not None:
            self._party_by_slot[slot] = chosen
        else:
            self._party_by_slot.pop(slot, None)

    def _party_member(self, slot) -> dict | None:
        return self._party_by_slot.get(slot)

    def _catalog_entry(self, ident) -> dict:
        entry = self.strategy_catalog.get(ident, self.strategy_catalog.get(str(ident), {}))
        return entry if isinstance(entry, dict) else {}

    def _calibrate(self, obs: Observation) -> None:
        """Compare the last card turn's estimate with the selected enemy's HP drop."""
        pending = self._pending_hit
        if pending is None or obs.phase != "tactical" or type(obs.turn) is not int or obs.turn <= pending["turn"]:
            return
        self._pending_hit = None
        after = next((e.get("hp") for e in obs.enemies if e.get("slot") == pending["slot"]), None)
        # Only a clean hit: same wave, target alive after (no overkill), HP fell.
        if (obs.wave == pending["wave"] and type(after) is int and 1 < after < pending["hp"]
                and pending["estimate"] > 0):
            ratio = (pending["hp"] - after) / pending["estimate"]
            if 0.5 <= ratio <= 5:
                self._calibration.append(ratio)
                del self._calibration[:-5]

    def _track_incoming(self, obs: Observation) -> None:
        """HP each ally lost between its turn's last tactical frame and the next turn.

        Seen live 2026-09-27 (Cernunnos, a Berserker): normal attacks took 9-14k
        from every ally on turn 1 and killed the damage dealer, while party
        invincibility was held for the enemy NP expected on turn 6. The request
        showed current HP and enemy charge but not how hard the enemy hits.
        """
        if obs.phase != "tactical" or type(obs.wave) is not int or type(obs.turn) is not int:
            return
        key = (obs.wave, obs.turn)
        now = {a.get("slot"): (a.get("id"), a.get("hp")) for a in obs.allies if a.get("slot") in (1, 2, 3)}
        if key != self._hp_turn:
            if (self._hp_turn is not None and self._hp_turn[0] == obs.wave
                    and obs.turn == self._hp_turn[1] + 1 and self._hp_last):
                rows = []
                for slot, (ident, hp) in sorted(self._hp_last.items()):
                    if type(ident) is not int or type(hp) is not int:
                        continue
                    new_id, new_hp = now.get(slot, (None, None))
                    if new_id != ident or new_hp == 0:
                        rows.append({"slot": slot, "servant_id": ident, "hp_before": hp, "died": True})
                    elif type(new_hp) is int:
                        rows.append({"slot": slot, "servant_id": ident, "hp_before": hp, "hp_after": new_hp,
                                     "lost": hp - new_hp})
                self._incoming = {"enemy_turn_after_turn": self._hp_turn[1], "allies": rows} if rows else None
            elif self._hp_turn is not None and self._hp_turn[0] != obs.wave:
                self._incoming = None
            self._hp_turn, self._hp_last = key, {}
        for slot, (ident, hp) in now.items():
            if type(ident) is int and type(hp) is int:
                self._hp_last[slot] = (ident, hp)

    def _enemy_pressure_view(self, obs: Observation) -> dict | None:
        """Observed damage taken last enemy turn, and class multipliers enemy -> ally."""
        from .damage import affinity
        view = {}
        if self._incoming:
            view["last_enemy_turn"] = self._incoming
        classes = {a.get("slot"): self._catalog_entry(a.get("id")).get("class_name")
                   for a in obs.allies if a.get("slot") in (1, 2, 3) and type(a.get("id")) is int}
        multipliers = []
        for enemy in obs.enemies:
            if enemy.get("hp") and enemy.get("class_name") and self._class_relation:
                multipliers.append({"enemy_slot": enemy.get("slot"), "enemy_class": enemy["class_name"],
                                    "vs_ally_slot": {str(slot): affinity(self._class_relation, enemy["class_name"], klass)
                                                     for slot, klass in sorted(classes.items()) if klass}})
        if multipliers:
            view["enemy_class_damage_multiplier"] = multipliers
        return view or None

    def _calibration_factor(self) -> float:
        if not self._calibration:
            return 1.0
        ordered = sorted(self._calibration)
        return ordered[len(ordered) // 2]

    def _record_buffs(self, action: Action, result: ActionResult) -> None:
        """Add the damage buffs of a confirmed skill or fired NP to the tracker."""
        from .damage import NP_LEVEL, SKILL_LEVEL
        obs = self._last_obs
        if result.status != "confirmed" or obs is None or type(obs.turn) is not int:
            return
        field = [slot for slot, ident in self._tracker_ids.items() if type(ident) is int]
        if action.kind == "skill" and action.servant in (1, 2, 3) and action.skill in (1, 2, 3):
            entry = self._catalog_entry(self._tracker_ids.get(action.servant))
            rows = [row for row in entry.get("skills") or [] if row.get("slot") == action.skill]
            variants = rows[0].get("variants") if rows else []
            if variants:
                # The account's unlocked upgrade is unknown; the latest variant is assumed.
                levels = (self._party_member(action.servant) or {}).get("skill_levels") or []
                level = levels[action.skill - 1] if len(levels) == 3 else SKILL_LEVEL
                self._tracker.apply(variants[-1].get("effects"), caster=action.servant, target=action.target,
                                    turn=obs.turn, level_index=level, field_slots=field)
        elif action.kind == "cards":
            target = self._estimate_target(obs)
            estimate = self._chain_estimate(obs, action.cards)
            living = [e for e in obs.enemies if type(e.get("hp")) is int and e["hp"] > 0]
            if target is not None and estimate and len(living) == 1:
                # Single living enemy: the whole chain hits it, so its HP drop is the
                # chain's real damage (area NPs included).
                self._pending_hit = {"turn": obs.turn, "wave": obs.wave, "slot": target.get("slot"),
                                     "hp": target.get("hp"), "estimate": estimate}
            for card in action.cards:
                if card in (6, 7, 8):
                    entry = self._catalog_entry(self._tracker_ids.get(card - 5))
                    nps = entry.get("noble_phantasms") or []
                    if nps:
                        np_level = (self._party_member(card - 5) or {}).get("np_level") or NP_LEVEL
                        self._tracker.apply(nps[-1].get("effects"), caster=card - 5, target=None, turn=obs.turn,
                                            level_index=np_level, field_slots=field)

    def _estimate_target(self, obs: Observation) -> dict | None:
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        living = [e for e in obs.enemies if type(e.get("hp")) is int and e["hp"] > 0]
        chosen = next((e for e in living if e.get("slot") == signals.get("selected_enemy")), None)
        return chosen or (living[0] if living else None)

    def _attackers(self, obs: Observation) -> dict:
        from .damage import attacker_from_catalog
        attackers = {}
        for ally in obs.allies:
            if ally.get("slot") in (1, 2, 3) and type(ally.get("id")) is int:
                attacker = attacker_from_catalog(ally["slot"], self._catalog_entry(ally["id"]),
                                                 self._party_member(ally["slot"]))
                if attacker is not None:
                    attackers[ally["slot"]] = attacker
        return attackers

    def _chain_estimate(self, obs: Observation, cards: tuple | list) -> int | None:
        """Estimated damage of one card selection to the selected enemy, or None."""
        from itertools import product
        from .damage import chain_damage
        attackers = self._attackers(obs)
        by_card = {c.get("slot"): c for c in obs.cards if c.get("slot") in range(1, 9)}
        ids = {a.get("slot"): a.get("id") for a in obs.allies if a.get("slot") in attackers}
        options = []  # per card: the (owner slot, type, is_np) choices
        for slot in cards:
            if slot >= 6:
                attacker = attackers.get(slot - 5)
                if attacker is None or attacker.np_card is None:
                    return None
                options.append([(slot - 5, attacker.np_card, True)])
                continue
            card = by_card.get(slot, {})
            if card.get("type") not in ("arts", "buster", "quick"):
                return None
            if card.get("servant") in attackers:
                options.append([(card["servant"], card["type"], False)])
                continue
            # Seen live (double Koyanskaya): two copies make a face card's owner
            # ambiguous. Both have the same stats; take the lower estimate, since
            # their buffs may differ.
            owners = [s for s, ident in ids.items() if type(ident) is int and ident == card.get("servant_id")]
            if not owners:
                return None
            options.append([(owner, card["type"], False) for owner in owners])
        target = self._estimate_target(obs) or {}
        buffs = {slot: self._tracker.buffs_for(slot) for slot in attackers}
        totals = []
        for chain in product(*options):
            try:
                totals.append(chain_damage(list(chain), attackers, buffs, defender_class=target.get("class_name"),
                                           relation=self._class_relation,
                                           def_down=self._tracker.enemy_def_down())["total"])
            except Exception:
                return None
        return min(totals) if totals else None

    def _damage_view(self, obs: Observation) -> dict | None:
        """Per-ally estimates Codex can compare: NP now vs that servant's own cards."""
        from .damage import chain_damage
        attackers = self._attackers(obs)
        target = self._estimate_target(obs)
        if not attackers or target is None:
            return None
        order = {"buster": 0, "arts": 1, "quick": 2}
        buffs = {slot: self._tracker.buffs_for(slot) for slot in attackers}
        kwargs = dict(defender_class=target.get("class_name"), relation=self._class_relation,
                      def_down=self._tracker.enemy_def_down())
        rows = []
        for ally in obs.allies:
            slot = ally.get("slot")
            attacker = attackers.get(slot)
            if attacker is None:
                continue
            deck = sorted([c for c in self._catalog_entry(ally["id"]).get("cards") or [] if c], key=order.get)
            member = self._party_member(slot)
            row = {"slot": slot, "np_ready": isinstance(ally.get("np"), (int, float)) and ally["np"] >= 100,
                   "stats": ({"atk": member.get("atk"), "np_level": member.get("np_level"),
                              "skill_levels": member.get("skill_levels"), "source": "Confirm Party"}
                             if member else {"atk": attacker.atk, "source": "catalog max-level base ATK, NP1"}),
                   "np_hits_all_enemies": attacker.np_area,
                   "tracked_buffs": self._tracker.describe(slot)}
            if len(deck) >= 3:
                row["three_own_cards_with_extra"] = chain_damage(
                    [(slot, deck[0], False), (slot, deck[1], False), (slot, deck[2], False)],
                    attackers, buffs, **kwargs)["total"]
                if attacker.np_card:
                    row["np_then_two_own_cards_with_extra"] = chain_damage(
                        [(slot, attacker.np_card, True), (slot, deck[0], False), (slot, deck[1], False)],
                        attackers, buffs, **kwargs)["total"]
                    row["np_alone"] = chain_damage(
                        [(slot, attacker.np_card, True), (slot, deck[0], False), (slot, deck[1], False)],
                        attackers, buffs, **kwargs)["cards"][0]
            if attacker.np_trait_bonus:
                row["np_bonus_if_enemy_has_its_trait"] = attacker.np_trait_bonus
            conditional = buffs[slot].conditional
            if conditional:
                row["bonuses_needing_an_enemy_trait"] = [{"buff": k, "value_pct": round(v * 100), "traits": t}
                                                         for k, v, t in conditional]
            rows.append(row)
        factor = self._calibration_factor()
        if factor != 1.0:
            for row in rows:
                for key in ("np_alone", "np_then_two_own_cards_with_extra", "three_own_cards_with_extra"):
                    if isinstance(row.get(key), (int, float)):
                        row[key] = round(row[key] * factor)
        return {"target": {"slot": target.get("slot"), "class": target.get("class_name")},
                "calibration": ({"factor": round(factor, 2), "from_turns": len(self._calibration),
                                 "note": "scaled by the median ratio of real HP drops to estimates this battle"}
                                if self._calibration else None),
                "assumptions": "Formula estimates for ranking, not exact damage. ATK, NP and skill levels come "
                               "from the Confirm Party screen when `stats.source` says so, else max-level base "
                               "ATK, NP level 1 and skill level 10. No Craft Essence effects, no crits, enemy "
                               "defence unknown. Buffs listed are only those this agent applied. An area NP's "
                               "value is per enemy.",
                "allies": rows}

    def observe_result(self, action: Action, result: ActionResult) -> None:
        """Persist a repeat-prevention fact only when the controller confirmed it."""
        try:
            self._record_buffs(action, result)
        except Exception:
            pass  # estimates are advisory; never let them block a battle
        if result.status != "confirmed":
            # An unconfirmed action invalidates the rest of a batched plan.
            self._plan_queue, self._plan_key = [], None
        if action.kind == "enemy_target" and action.target is not None:
            key = self._pending_enemy_targets.pop(action.target, None)
            if key is not None and result.status == "confirmed":
                self._confirmed_enemy_target_turns.add(key)
                self._history.append({"event": "confirmed_enemy_target_change", "target_slot": action.target,
                                      "wave": key[0], "turn": key[1]})
                del self._history[:-self._MAX_HISTORY]
            return
        if action.kind != "skill" or action.servant is None or action.skill is None:
            return
        key = self._pending.pop((action.servant, action.skill), None)
        if key is not None and result.status in ("uncertain", "rejected"):
            # Possibly cast, possibly not: never offer it again this turn, so a
            # misread cannot turn into a repeated tap or a proposal loop.
            self._confirmed.add(key)
            self._history.append({"event": "unconfirmed_skill_attempt", "actor_slot": action.servant,
                                  "skill_slot": action.skill, "wave": key[0], "turn": key[1],
                                  "status": result.status})
            del self._history[:-self._MAX_HISTORY]
            return
        if key is not None and result.skill_consumed is True:
            self._confirmed.add(key)
            self._history.append({"event": "confirmed_skill_consumed", "actor_slot": action.servant,
                                  "skill_slot": action.skill, "wave": key[0], "turn": key[1],
                                  "actor_id": key[2]})
            del self._history[:-self._MAX_HISTORY]

    def build_request(self, obs: Observation) -> dict:
        """Build the public, JSON-safe replay request without invoking the backend."""
        request, _ = self._request_and_actions(obs)
        return request

    def _request_and_actions(self, obs: Observation) -> tuple[dict, dict[str, Action]]:
        allowed = self._allowed_actions(obs)
        if not allowed:
            raise PlannerError("no legal observable actions")
        allowed, candidates = self._rank_card_actions(obs, allowed)
        request = {
            "observation_id": obs.id,
            "phase": obs.phase,
            "wave": obs.wave,
            "turn": obs.turn,
            "quest_context": {"objective": self.objective},
            "allies": [self._ally_view(ally) for ally in obs.allies],
            "enemies": [self._enemy_view(enemy) for enemy in obs.enemies],
            "cards": [self._card_view(card) for card in obs.cards],
            "salient_signals": self._salient_signals(obs),
            "confirmed_history": list(self._history),
            "servant_metadata": self._metadata_view(obs),
            "allowed_actions": [self._action_view(identifier, action) for identifier, action in allowed.items()],
            "instructions": self._guide,
            "strategy_guide_sha256": self._guide_hash,
        }
        if candidates:
            request["card_candidates"] = candidates
        if obs.phase in ("tactical", "cards") and self.objective == "survive_and_win":
            try:
                estimates = self._damage_view(obs)
            except Exception:
                estimates = None
            if estimates:
                request["damage_estimates"] = estimates
            try:
                pressure = self._enemy_pressure_view(obs)
            except Exception:
                pressure = None
            if pressure:
                request["enemy_pressure"] = pressure
        if self._notes:
            request["stage_notes"] = {
                "source": "operator notes written after an earlier attempt at this battle failed",
                "status": "guidance about this battle, not an observation of the current screen",
                "text": self._notes,
            }
        profiles = self._enemy_profiles_view(obs)
        if profiles:
            request["enemy_profiles"] = profiles
        statuses = self._ally_statuses_view(obs)
        if statuses:
            request["ally_statuses"] = statuses
        backline = self._backline_view(obs)
        if backline is not None:
            request["backline"] = backline
            request["order_change_available"] = bool(
                (obs.signals or {}).get("order_change_available") is True)
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        request["request_id"] = digest
        return request, allowed

    def _allowed_actions(self, obs: Observation) -> dict[str, Action]:
        allowed: dict[str, Action] = {}
        if obs.phase == "tactical":
            allowed["attack"] = Action("attack")
            turn_key = self._turn_key(obs)
            if turn_key is None:
                return allowed
            if turn_key not in self._confirmed_enemy_target_turns:
                for enemy in self._eligible_enemy_targets(obs):
                    allowed[f"e{enemy['slot']}"] = Action("enemy_target", target=enemy["slot"])
            living = self._living_allies(obs)
            for ally in living:
                actor_id, actor_slot = ally["id"], ally["slot"]
                metadata = self._skills_for(actor_id)
                for observed in ally.get("skills", []):
                    skill_slot = observed.get("slot")
                    if skill_slot not in (1, 2, 3) or observed.get("ready") is not True or len(metadata) < skill_slot:
                        continue
                    if isinstance(observed.get('cooldown'), (int, float)) and observed['cooldown'] > 0:
                        continue
                    effect = metadata[skill_slot - 1]
                    if not self._known_effect(effect) or effect[1] in self._SPECIAL_MODES:
                        continue
                    key = (*turn_key, actor_id, actor_slot, skill_slot)
                    if key in self._confirmed:
                        continue
                    mode = effect[1]
                    targets = ([actor_slot] if effect[0] == 1 else
                               [a["slot"] for a in living] if mode == 1 else [self._generic_target(mode, actor_slot, living)])
                    for target in targets:
                        identifier = f"s{actor_slot}-{skill_slot}-t{target}"
                        allowed[identifier] = Action("skill", servant=actor_slot, skill=skill_slot, target=target)
            for field_slot, back_slot in self._order_change_pairs(obs, living):
                allowed[f"oc-f{field_slot}-b{back_slot}"] = Action("order_change", servant=field_slot, target=back_slot)
            return allowed
        if obs.phase == "cards":
            # Sealed face cards fill the chain only when too few usable cards exist,
            # which passes the turn while a seal wears off (see card_rules).
            from .card_rules import chain_length, filler_slots, selectable_slots
            valid = selectable_slots(obs.cards, obs.signals, obs.turn)
            size = chain_length(obs.cards, obs.signals, obs.turn)
            fillers = filler_slots(obs.cards, obs.signals, obs.turn)
            real = min(size, len([slot for slot in valid if slot not in fillers]))
            for triple in permutations(valid, size) if size else ():
                # Fillers (sealed or stunned cards) only make up the count, after
                # every real card that fits.
                if any(slot in fillers for slot in triple[:real]):
                    continue
                identifier = "c" + "-".join(str(slot) for slot in triple)
                allowed[identifier] = Action("cards", cards=triple)
        return allowed

    def _rank_card_actions(self, obs: Observation, allowed: dict[str, Action]) -> tuple[dict[str, Action], list[dict]]:
        """Rank enumerated card triples with the upstream scorer and keep the best.

        The permutation enumeration is complete but opaque: it can exceed three
        hundred ids that all look alike, which both bloats the request and hides
        real card mechanics.  Scoring them with the heuristic the baseline uses
        gives the model the same chain/position/affinity arithmetic and trims the
        enum to a comparable shortlist.  Every kept id is still an action this
        observation enumerated, so legality is unchanged.
        """
        card_actions = {i: a for i, a in allowed.items() if a.kind == "cards"}
        if not card_actions:
            return allowed, []
        cards = {c.get("slot"): c for c in obs.cards if c.get("slot") in range(1, 9)}
        scored = []
        for identifier, action in card_actions.items():
            try:
                score = self._scorer._score(list(action.cards), cards, obs)
            except Exception:
                # A scorer failure must not remove legal choices.
                return allowed, []
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                return allowed, []
            scored.append((identifier, action, float(score)))
        baseline = self._baseline_card_id(obs)
        scored.sort(key=lambda row: (-row[2], row[0]))
        keep = scored[:self._MAX_CARD_CANDIDATES]
        if baseline is not None and baseline in card_actions and all(row[0] != baseline for row in keep):
            keep.append(next(row for row in scored if row[0] == baseline))
        # Seen live (Morgan): two NPs were ready but every NP triple scored below the
        # top ten, so the model could not fire either. Keep the best triple for each
        # distinct set of NPs, so firing any ready NP is always a listed choice.
        np_set = lambda action: frozenset(slot for slot in action.cards if slot >= 6)
        # Seen live (3x Morgan): the only listed Oberon + Ciel triple fired Oberon's
        # NP (Invincible [Demerit] to all enemies) first, so the model rightly
        # skipped it and Oberon's drained charge was wasted. An NP that buffs the
        # enemies must be last; a triple breaking that is dropped when its NP set
        # has an ordering that keeps it.
        demerit = {5 + a.get("slot") for a in obs.allies
                   if a.get("slot") in (1, 2, 3) and self._np_helps_enemies(a.get("slot"), obs)}
        last_ok = lambda action: all(action.cards[-1] == c for c in demerit if c in action.cards)
        orderable = {np_set(action) for _, action, _ in scored if last_ok(action)}
        keep = [row for row in keep if last_ok(row[1]) or np_set(row[1]) not in orderable]
        listed = {np_set(action) for _, action, _ in keep}
        for row in scored:
            if np_set(row[1]) not in listed and (last_ok(row[1]) or np_set(row[1]) not in orderable):
                keep.append(row)
                listed.add(np_set(row[1]))
        kept = {identifier: action for identifier, action, _ in keep}
        others = {i: a for i, a in allowed.items() if a.kind != "cards"}
        views = [self._candidate_view(identifier, action, score, cards, obs, baseline)
                 for identifier, action, score in keep]
        return {**others, **kept}, views

    def _baseline_card_id(self, obs: Observation) -> str | None:
        """The card action the upstream heuristic would take, as an allowed id."""
        try:
            action = self._scorer._cards(obs)
        except Exception:
            return None
        if action.kind != "cards" or not action.cards:
            return None
        return "c" + "-".join(str(slot) for slot in action.cards)

    def _candidate_view(self, identifier: str, action: Action, score: float, cards: dict,
                        obs: Observation, baseline: str | None) -> dict:
        colors = [self._card_color_name(slot, cards, obs) for slot in action.cards]
        faces = [slot for slot in action.cards if slot <= 5]
        face_colors = {color for slot, color in zip(action.cards, colors) if slot <= 5}
        groups = [self._scorer._group(cards[slot]) for slot in action.cards if slot in cards]
        estimate = self._chain_estimate(obs, action.cards) if self.objective == "survive_and_win" else None
        return {
            "id": identifier,
            "cards": list(action.cards),
            "score": round(score, 2),
            **({"estimated_damage": estimate} if estimate is not None else {}),
            "baseline_choice": identifier == baseline,
            "card_colors": colors,
            "np_slots": [slot for slot in action.cards if slot >= 6],
            "color_chain": (face_colors.pop() if len(face_colors) == 1 else
                            "rainbow" if face_colors == {"arts", "quick", "buster"} else None) if faces else None,
            "brave_chain": len(groups) == 3 and len(set(groups)) == 1,
            "distinct_servant_groups": len(set(groups)),
            "affinities": [cards[slot].get("affinity") if slot in cards else None for slot in action.cards],
            "critical_rates": [cards[slot].get("critical_rate") if slot in cards else None for slot in action.cards],
        }

    def _card_color_name(self, slot: int, cards: dict, obs: Observation) -> str | None:
        value = self._scorer._card_color(slot, cards, obs, {"arts": 0, "quick": 1, "buster": 2})
        return {0: "arts", 1: "quick", 2: "buster"}.get(value)

    @staticmethod
    def _order_change_pairs(obs: Observation, living: list[dict]) -> list[tuple[int, int]]:
        """(field slot, back-line slot) pairs, only when the frame says it is possible."""
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        if signals.get("order_change_available") is not True:
            return []
        backline = [m for m in signals.get("backline") or []
                    if isinstance(m, dict) and m.get("slot") in (1, 2, 3) and m.get("name")]
        return [(ally["slot"], member["slot"]) for ally in living for member in backline]

    def _backline_view(self, obs: Observation) -> list[dict] | None:
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        backline = signals.get("backline")
        if not isinstance(backline, list):
            return None
        rows = []
        for member in backline:
            if not isinstance(member, dict):
                continue
            ident = member.get("servant_id")
            catalog = self.strategy_catalog.get(ident, {}) if type(ident) is int else {}
            catalog = catalog if isinstance(catalog, dict) else {}
            rows.append({
                "backline_slot": member.get("slot"),
                "displayed_name": member.get("name"),
                "servant_id": ident,
                "identity": "resolved" if type(ident) is int else "unresolved_name_only",
                "class_name": catalog.get("class_name"),
                "catalog_effective_atk_max": catalog.get("effective_atk_max"),
                "catalog_skills": self._catalog_skills(catalog),
                "catalog_noble_phantasms": [{k: n.get(k) for k in ("name", "description", "card", "np_type")}
                                            for n in catalog.get("noble_phantasms", []) if isinstance(n, dict)],
                "np_demerits": self._np_demerits(catalog),
            })
        return rows

    @staticmethod
    def _valid_card(card: dict) -> bool:
        kind = str(card.get("type", "")).lower()
        if card.get("sealed") is not False:
            return False
        if kind in {"arts", "quick", "buster"}:
            return True
        return kind == "np" and card.get("ready") is True

    @staticmethod
    def _living_allies(obs: Observation) -> list[dict]:
        def alive(ally):
            hp = ally.get("hp")
            if isinstance(hp, (int, float)) and not isinstance(hp, bool):
                return hp > 0
            # Unreadable HP with observed HP digits: alive, value still unknown.
            return hp is None and ally.get("alive") is True
        return [ally for ally in obs.allies if ally.get("slot") in (1, 2, 3)
                and type(ally.get("id")) is int and ally['id'] > 0 and alive(ally)]

    @staticmethod
    def _eligible_enemy_targets(obs: Observation) -> list[dict]:
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        selected = signals.get("selected_enemy")
        if (PlannerPolicy._turn_key(obs) is None or signals.get("enemy_layout") != "standard3"
                or signals.get("enemy_target_verifiable") is not True
                or type(selected) is not int or selected not in (1, 2, 3)):
            return []
        slots: dict[int, dict] = {}
        for enemy in obs.enemies:
            if not isinstance(enemy, dict) or type(enemy.get("slot")) is not int or enemy["slot"] not in (1, 2, 3) or enemy["slot"] in slots:
                return []
            slots[enemy["slot"]] = enemy
        selected_enemy = slots.get(selected)
        if selected_enemy is None or type(selected_enemy.get("hp")) is not int or selected_enemy["hp"] <= 0:
            return []
        return [enemy for slot, enemy in slots.items() if slot != selected and type(enemy.get("hp")) is int and enemy["hp"] > 0]

    def _skills_for(self, servant_id: int) -> tuple:
        data = self.servant_data.get(servant_id)
        if not isinstance(data, (tuple, list)) or len(data) <= 5 or not isinstance(data[5], (tuple, list)):
            return ()
        return tuple(data[5])

    @staticmethod
    def _known_effect(effect: object) -> bool:
        """Whether a legacy tuple gives a usable eligibility and targeting gate.

        Effect class 0 means upstream never classified the skill's semantics, not
        that the skill is unusable.  Two servants' skills are class 0, including
        Oberon's one-turn NP damage amplifier, which is exactly the kind of skill
        a farming plan needs.  The target mode still has to be known and ordinary,
        so targeting stays unambiguous; the model is told to read the English
        description instead of a missing class.
        """
        return (isinstance(effect, (tuple, list)) and len(effect) >= 2
                and type(effect[0]) is int and effect[0] in range(0, 10)
                and type(effect[1]) is int and effect[1] in range(9))

    @staticmethod
    def _generic_target(mode: int, actor_slot: int, living: list[dict]) -> int:
        # Upstream target mapping: mode 5 is self; all other non-mode-1 values
        # use the legacy dialog's deterministic middle/left/only living target.
        if mode == 5:
            return actor_slot
        slots = sorted(ally["slot"] for ally in living)
        return slots[(len(slots) - 1) // 2]

    @staticmethod
    def _turn_key(obs: Observation) -> tuple[int, int] | None:
        return ((obs.wave, obs.turn) if type(obs.wave) is int and obs.wave > 0
                and type(obs.turn) is int and obs.turn > 0 else None)

    def _history_key(self, obs: Observation, action: Action) -> tuple[Any, ...]:
        actor = next((a for a in obs.allies if a.get("slot") == action.servant), {})
        return (obs.wave, obs.turn, actor.get("id"), action.servant, action.skill)

    def _clear_replaced_actors(self, obs: Observation) -> None:
        current = {a.get("slot"): a.get("id") for a in obs.allies if a.get("slot") in (1, 2, 3) and isinstance(a.get("id"), int)}
        replaced = {slot for slot, ident in current.items() if slot in self._known_actor_ids and self._known_actor_ids[slot] != ident}
        if replaced:
            self._confirmed = {key for key in self._confirmed if key[3] not in replaced}
            self._pending = {key: value for key, value in self._pending.items() if key[0] not in replaced}
        self._known_actor_ids.update(current)

    @staticmethod
    def _ally_view(ally: dict) -> dict:
        view = {key: ally.get(key) for key in ("slot", "id", "hp", "hp_fraction", "np")}
        view["skills"] = [{key: skill.get(key) for key in ("slot", "ready", "cooldown")}
                          for skill in ally.get("skills", []) if isinstance(skill, dict)]
        return view

    @staticmethod
    def _ally_statuses_view(obs: Observation) -> list[dict]:
        """Ally Status tabs read in battle: seals, buffs and debuffs with turns left."""
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        statuses = signals.get("ally_statuses")
        if not isinstance(statuses, list):
            return []
        view = []
        for status in statuses[:3]:
            if not isinstance(status, dict):
                continue
            view.append({"slot": status.get("slot"), "servant_id": status.get("servant_id"),
                         "name": status.get("name"), "atk_observed": status.get("atk"),
                         "effects": [{"title": str(e.get("title", ""))[:120], "text": str(e.get("text", ""))[:300],
                                      "duration": e.get("duration")}
                                     for e in status.get("effects", [])[:16] if isinstance(e, dict)],
                         "possibly_truncated": status.get("possibly_truncated") is True,
                         "read_at": status.get("read_at"),
                         "source": "OCR of the ally's in-game Status tab; durations count down each turn "
                                   "after read_at"})
        return view

    @staticmethod
    def _enemy_profiles_view(obs: Observation) -> list[dict]:
        """Enemy Status Changes read from the in-game dialog, bounded in size."""
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        profiles = signals.get("enemy_profiles")
        if not isinstance(profiles, list):
            return []
        view = []
        for profile in profiles[:3]:
            if not isinstance(profile, dict):
                continue
            effects = [{"title": str(e.get("title", ""))[:120], "text": str(e.get("text", ""))[:400]}
                       for e in profile.get("effects", [])[:12] if isinstance(e, dict)]
            view.append({"slot": profile.get("slot"), "name": profile.get("name"),
                         "sub_attribute": profile.get("sub_attribute"), "effects": effects,
                         "possibly_truncated": profile.get("possibly_truncated") is True,
                         "read_at": profile.get("read_at"),
                         "source": "OCR of the enemy's in-game Status Changes dialog; wording is close "
                                   "but not exact, and effects may have changed since read_at"})
        return view

    @staticmethod
    def _enemy_view(enemy: dict) -> dict:
        return {key: enemy.get(key) for key in ("slot", "hp", "charge", "priority_label", "bars_remaining")}

    @staticmethod
    def _card_view(card: dict) -> dict:
        result = {key: card.get(key) for key in ("slot", "type", "servant", "group", "visualgroupid", "sealed", "ready", "affinity", "critical_rate")}
        result['affinity_meaning'] = {'weak': 'enemy_vulnerable_bonus_damage',
                                      'resist': 'enemy_resists_reduced_damage',
                                      'neutral': 'neutral_damage'}.get(card.get('affinity'), 'unknown')
        return result

    @staticmethod
    def _salient_signals(obs: Observation) -> dict:
        signals = obs.signals if isinstance(obs.signals, dict) else {}
        # ``stage_turn`` is how many turns this wave has already taken. A value
        # above one is observed evidence that the wave survived a card turn, which
        # the model otherwise has no way to learn from within a battle.
        return {key: signals.get(key) for key in ("stage_turn", "stage_total", "allies_source", "turn_source",
                                                   "selected_enemy", "enemy_layout", "enemy_target_verifiable")
                if key in signals}

    def _metadata_view(self, obs: Observation) -> list[dict]:
        rows = []
        for ally in self._living_allies(obs):
            servant_id = ally["id"]
            catalog = self.strategy_catalog.get(servant_id, self.strategy_catalog.get(str(servant_id), {}))
            catalog = catalog if isinstance(catalog, dict) else {}
            data = self.servant_data.get(servant_id)
            np_data = data[4] if isinstance(data, (list, tuple)) and len(data) > 4 else ()
            limitations = list(catalog.get('limitations', ['No reference descriptions available']))
            if ally.get("identity_source") == "name_label_class_differs":
                limitations.append("Identified from the battle name label, but its in-battle class differs "
                                   "from this catalog entry: likely a story variant whose skills may "
                                   "differ from this text. Trust the observed skill readiness over it.")
            rows.append({"slot": ally["slot"], "servant_id": servant_id,
                         "identity_source": ally.get("identity_source"),
                         "provenance": "legacy_tuple_coarse_gate_not_full_effect",
                         "english_name": catalog.get("name") if isinstance(catalog.get("name"), str) else None,
                         "catalog_skills": self._catalog_skills(catalog),
                         "class_name": catalog.get('class_name'),
                         # Reference ranking stats, not this account's real values.
                         "catalog_atk_max": catalog.get('atk_max'),
                         "catalog_effective_atk_max": catalog.get('effective_atk_max'),
                         "catalog_class_attack_rate": catalog.get('class_attack_rate'),
                         "catalog_hp_max": catalog.get('hp_max'),
                         "catalog_source": catalog.get('source_url'),
                         "catalog_fetched_at": catalog.get('fetched_at'),
                         "catalog_limitations": limitations,
                         "np_kind": {0: 'single_target', 1: 'area', 2: 'support'}.get(np_data[0]) if len(np_data) >= 2 else None,
                         "np_role": self._np_role(catalog),
                         "np_demerits": self._np_demerits(catalog),
                         "np_color": {0: 'arts', 1: 'quick', 2: 'buster'}.get(np_data[1]) if len(np_data) >= 2 else None,
                         "catalog_noble_phantasms": [{k: n.get(k) for k in ('id', 'name', 'description', 'card', 'np_type')}
                                                     for n in catalog.get('noble_phantasms', []) if isinstance(n, dict)],
                         "legacy_skills": [{"slot": index + 1, "effect_class": effect[0], "mode": effect[1],
                                            "effect_class_semantics": ("unknown_upstream_rely_on_description"
                                                                       if effect[0] == 0 else "coarse_upstream_category")}
                                           for index, effect in enumerate(self._skills_for(servant_id)) if self._known_effect(effect)]})
        return rows

    @staticmethod
    def _catalog_skills(catalog: dict) -> list[dict] | None:
        """Keep read-only English catalog text, including unchosen upgrade variants."""
        skills = catalog.get("skills")
        if not isinstance(skills, list):
            return None
        normalized = []
        for skill in skills:
            if not isinstance(skill, dict) or skill.get("slot") not in (1, 2, 3):
                continue
            variants = skill.get("variants")
            if not isinstance(variants, list):
                continue
            normalized.append({"slot": skill["slot"], "variants": [
                {key: variant.get(key) for key in ("id", "name", "description", "cooldown_by_level")}
                for variant in variants if isinstance(variant, dict)]})
        return normalized

    @staticmethod
    def _action_view(identifier: str, action: Action) -> dict:
        payload = {"id": identifier, "kind": action.kind}
        if action.kind == "skill":
            payload.update({"servant": action.servant, "skill": action.skill, "target": action.target})
        elif action.kind == "enemy_target":
            payload["target"] = action.target
        elif action.kind == "cards":
            payload["cards"] = list(action.cards)
        elif action.kind == "order_change":
            payload.update({"field_slot": action.servant, "backline_slot": action.target})
        return payload

    @classmethod
    def _response_schema(cls, identifiers: list[str]) -> dict:
        return {"type": "object", "additionalProperties": False, "required": ["observation_id", "request_id", "plan", "reason", "uncertainty"],
                "properties": {"observation_id": {"type": "string"}, "request_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                               "plan": {"type": "array", "minItems": 1, "maxItems": cls._MAX_PLAN,
                                        "items": {"type": "string", "enum": identifiers}},
                               "reason": {"type": "string", "maxLength": cls._MAX_REASON},
                               "uncertainty": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 160}}}}

    def _validate_response(self, response: object, request: dict, allowed: dict[str, Action]) -> tuple[Action, str, list[str], list[str]]:
        if not isinstance(response, dict):
            raise PlannerError("planner response is not an object")
        base = {"observation_id", "request_id", "reason", "uncertainty"}
        keys = set(response)
        # ``action_id`` remains accepted so a single-action backend stays valid.
        choice = keys & {"plan", "action_id"}
        if len(choice) != 1 or keys != base | choice:
            raise PlannerError("planner response has missing or extraneous keys")
        if response["observation_id"] != request["observation_id"] or response["request_id"] != request["request_id"]:
            raise PlannerError("planner response binding does not match request")
        if "plan" in response:
            plan = response["plan"]
            if (not isinstance(plan, list) or not 1 <= len(plan) <= self._MAX_PLAN
                    or any(not isinstance(item, str) for item in plan)):
                raise PlannerError("planner response plan is invalid")
            if len(set(plan)) != len(plan):
                raise PlannerError("planner response plan repeats an action")
        else:
            if not isinstance(response["action_id"], str):
                raise PlannerError("planner response action_id is invalid")
            plan = [response["action_id"]]
        if any(identifier not in allowed for identifier in plan):
            raise PlannerError("planner response selected an unavailable action")
        kinds = [allowed[identifier].kind for identifier in plan]
        if "attack" in kinds and kinds.index("attack") != len(kinds) - 1:
            raise PlannerError("planner response plans an action after attack")
        if "cards" in kinds and len(plan) != 1:
            raise PlannerError("planner response plans a cards action with other actions")
        if kinds.count("enemy_target") > 1:
            raise PlannerError("planner response plans more than one enemy target change")
        if "order_change" in kinds and kinds.index("order_change") != len(kinds) - 1:
            raise PlannerError("planner response plans an action after order_change")
        reason, uncertainty = response["reason"], response["uncertainty"]
        if not isinstance(reason, str) or len(reason) > self._MAX_REASON:
            raise PlannerError("planner response reason is invalid")
        if (not isinstance(uncertainty, list) or len(uncertainty) > 8
                or any(not isinstance(item, str) or len(item) > 160 for item in uncertainty)):
            raise PlannerError("planner response uncertainty is invalid")
        return allowed[plan[0]], reason, uncertainty, plan

    def _model_metadata(self):
        return self._safe_metadata(getattr(self.backend, "last_call", None))

    @classmethod
    def _safe_metadata(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): cls._safe_metadata(item) for key, item in value.items()
                    if not any(word in str(key).lower() for word in ("secret", "token", "authorization", "api_key", "password"))}
        if isinstance(value, (list, tuple)):
            return [cls._safe_metadata(item) for item in value[:16]]
        return {"type": type(value).__name__}

    def _safe_response(self, response):
        return self._safe_metadata(response)

    def _trace(self, event: str, payload: object) -> None:
        if self.trace:
            self.trace.record(event, payload)
