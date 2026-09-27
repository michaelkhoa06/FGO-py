"""Evidence-first, bounded battle action controller.

This module deliberately knows nothing about ADB or image recognition.  It only sends
validated actions through the supplied driver and evaluates the observations returned.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from .models import Action, ActionResult, Observation


TACTICAL = "tactical"
CARDS = "cards"
# A skill whose readiness could not be read at all.  Distinct from a skill that
# was observed to be on cooldown, which is a real refusal.
UNOBSERVED_READINESS = "skill readiness is unobserved"
_READINESS_REREADS = 3
# Consecutive same-turn tactical frames showing the used skill dark before that
# darkness alone confirms consumption: two when corroborated, three otherwise.
_DARK_FRAMES_CORROBORATED = 2
_DARK_FRAMES_SPECIFIC = 3
TERMINAL_OR_ERROR = {"result", "defeat", "network_error"}


class BattleDriver(Protocol):
    def observe(self) -> Observation: ...

    def send(self, action: Action) -> None: ...


class TraceSink(Protocol):
    def record(self, event: str, payload: object) -> None: ...


class _ObservationFailure:
    def __init__(self, error: Exception) -> None:
        self.error = error


class BattleController:
    # Seconds to wait for the master skill palette after tapping its button.
    _PALETTE_WAIT = 8.0
    # A decision frame younger than this is used as the "before" frame as is.
    _FRESH_FRAME_SECONDS = 1.5

    @classmethod
    def _fresh(cls, observation: Observation | None) -> bool:
        from datetime import datetime, timezone
        if observation is None:
            return False
        try:
            captured = datetime.fromisoformat(observation.captured_at)
        except (TypeError, ValueError):
            return False
        if captured.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        return 0 <= age <= cls._FRESH_FRAME_SECONDS

    def __init__(
        self,
        driver: BattleDriver,
        *,
        max_polls: int = 3,
        timeout: float | None = 20.0,
        poll_interval: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        trace: TraceSink | None = None,
    ) -> None:
        if max_polls < 1:
            raise ValueError("max_polls must be at least one")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        self.driver = driver
        self.max_polls = max_polls
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._monotonic = monotonic
        self._sleep = sleeper
        self.trace = trace

    def run(self, actions: list[Action]) -> list[ActionResult]:
        results: list[ActionResult] = []
        for action in actions:
            result = self.execute(action)
            results.append(result)
            if result.status != "confirmed":
                break
        return results

    def execute(self, action: Action, *, expected: Observation | None = None,
                time_budget: float | None = None) -> ActionResult:
        """Execute one action, without beginning any input after ``time_budget``.

        A driver call already in progress cannot be cancelled, but every later
        observation, primary input, target input, and poll sleep is deadline-gated.
        """
        if time_budget is not None and (not isinstance(time_budget, (int, float))
                                        or isinstance(time_budget, bool) or time_budget < 0):
            raise ValueError("time_budget must be a non-negative number or None")
        deadline = None if time_budget is None else self._monotonic() + time_budget
        if self._deadline_expired(deadline):
            return self._result(action, "uncertain", None, None,
                                reason="execution time budget exhausted before input")
        try:
            # The policy's own frame is reused when it is only a moment old: a
            # scheduled plan step is decided instantly, and re-capturing the same
            # screen cost a full observation per action (seen live: ~2 s each).
            before = expected if self._fresh(expected) else self._observe()
        except Exception as exc:
            return self._result(action, "error", None, None, reason=f"initial observation failed: {exc}")
        if expected is not None and before is not expected:
            if (before.phase, before.wave, before.turn) != (expected.phase, expected.wave, expected.turn):
                return self._result(action, "rejected", before, None, reason="battle context changed since policy decision")
            if action.kind in ("skill", "order_change"):
                old = next((a.get("id") for a in expected.allies if a.get("slot") == action.servant), None)
                new = next((a.get("id") for a in before.allies if a.get("slot") == action.servant), None)
                if old is None or new != old:
                    return self._result(action, "rejected", before, None, reason="acting servant changed or became unknown")
            if action.kind == "cards":
                signature = lambda obs: [(c.get("slot"), c.get("type"), c.get("servant")) for c in obs.cards]
                if signature(before) != signature(expected):
                    return self._result(action, "rejected", before, None, reason="command cards changed since policy decision")
        validation = self._validate(action, before)
        if validation == UNOBSERVED_READINESS:
            before, validation = self._reread_readiness(action, before, deadline)
        if validation:
            return self._result(action, "rejected", before, None, reason=validation)
        if self._deadline_expired(deadline):
            return self._result(action, "uncertain", before, None,
                                reason="execution time budget exhausted before input")
        if action.kind == "order_change":
            return self._run_order_change(action, before, deadline)
        try:
            self._send(action)
        except Exception as exc:  # driver failures are observable controller errors
            return self._result(action, "error", before, None, reason=f"send failed: {exc}")

        if action.kind == "skill":
            return self._finish_skill(action, before, deadline)
        if action.kind == "attack":
            return self._finish_attack(action, before, deadline)
        if action.kind == "enemy_target":
            return self._finish_enemy_target(action, before, deadline)
        return self._finish_cards(action, before, deadline)

    def _reread_readiness(self, action: Action, before: Observation,
                          deadline: float | None) -> tuple[Observation, str | None]:
        """Re-read a skill whose readiness was unreadable, rather than refusing it.

        Skill readiness comes from an icon brightness heuristic that can fail on a
        single frame while the skill really is usable.  Treating that silence as a
        cooldown makes a policy propose the same action forever and stall the
        battle.  An observed ``False`` still refuses immediately; only an
        unreadable value is re-read, a bounded number of times, inside the same
        deadline as every other controller input.
        """
        for _ in range(_READINESS_REREADS):
            if self._deadline_expired(deadline):
                break
            self._sleep(self.poll_interval)
            try:
                fresh = self._observe()
            except Exception as exc:
                return before, f"observation failed while re-reading readiness: {exc}"
            if (fresh.phase, fresh.wave, fresh.turn) != (before.phase, before.wave, before.turn):
                return fresh, "battle context changed while re-reading skill readiness"
            validation = self._validate(action, fresh)
            if validation != UNOBSERVED_READINESS:
                return fresh, validation
            before = fresh
        return before, UNOBSERVED_READINESS

    def _await(self, predicate, deadline):
        """Poll until an observation satisfies ``predicate``; return (match, last, failure)."""
        last = None
        for observation in self._poll(deadline):
            if isinstance(observation, _ObservationFailure):
                return None, last, observation.error
            last = observation
            if predicate(observation):
                return observation, last, None
        return None, last, None

    @staticmethod
    def _selected_positions(observation: Observation) -> set[int]:
        screen = observation.signals.get("order_change") if isinstance(observation.signals, dict) else None
        members = screen.get("members", []) if isinstance(screen, dict) else []
        return {m.get("position") for m in members if m.get("selected")}

    @staticmethod
    def _palette_state(observation: Observation) -> str | None:
        palette = observation.signals.get("master_palette") if isinstance(observation.signals, dict) else None
        return palette.get("order_change") if isinstance(palette, dict) else None

    def _close_palette(self, deadline) -> bool:
        """Toggle the palette shut and confirm it is gone."""
        self._send(Action("master_menu"))
        closed, _, failure = self._await(
            lambda o: o.phase == TACTICAL and self._palette_state(o) is None, deadline)
        return closed is not None and failure is None

    def _cancel_order_change(self, deadline) -> bool:
        """Leave the member screen WITHOUT committing, then close the palette.

        Observed live: the close X leaves Order Change unspent and returns to the
        tactical screen with the palette still open.
        """
        try:
            self._send(Action("order_change_cancel"))
        except Exception:
            return False
        back, _, failure = self._await(lambda o: o.phase == TACTICAL, deadline)
        if back is None or failure is not None:
            return False
        return self._palette_state(back) is None or self._close_palette(deadline)

    def reconnoitre_order_change(self, *, time_budget: float | None = None) -> dict:
        """Open the member screen once to read the back line, then back out unspent.

        Nothing is committed: the member screen is closed with its X, which leaves
        the master skill available. The driver keeps what the frames showed.
        """
        deadline = None if time_budget is None else self._monotonic() + time_budget
        try:
            start = self._observe()
        except Exception as exc:
            return {"status": "error", "reason": f"initial observation failed: {exc}"}
        if start.phase != TACTICAL:
            return {"status": "skipped", "reason": f"reconnaissance needs the tactical screen, saw {start.phase}"}
        try:
            self._send(Action("master_menu"))
            # The palette opens within a second or two. Seen live: a story stage
            # grays out the Master Skill button, the tap does nothing, and waiting
            # out the whole budget cost 90 s before the first turn.
            palette_deadline = self._monotonic() + self._PALETTE_WAIT
            if deadline is not None:
                palette_deadline = min(palette_deadline, deadline)
            palette, _, failure = self._await(
                lambda o: o.phase == TACTICAL and self._palette_state(o) is not None, palette_deadline)
            if palette is None or failure is not None:
                mark = getattr(self.driver, "mark_order_change_unavailable", None)
                if callable(mark):
                    mark()
                return {"status": "unavailable", "reason": "master skill palette was not recognized"}
            state = self._palette_state(palette)
            if state != "ready":
                closed = self._close_palette(deadline)
                return {"status": "unavailable", "reason": f"order change is {state}", "palette_closed": closed}
            self._send(Action("master_skill", skill=3))
            screen, last, failure = self._await(lambda o: o.phase in ("order_change", "skill_error"), deadline)
            if screen is not None and screen.phase == "skill_error":
                # Seen live: "You don't have any switchable Sub Members". Nothing is
                # spent; close the dialog and the palette, and stop offering it.
                mark = getattr(self.driver, "mark_order_change_unavailable", None)
                if callable(mark):
                    mark()
                restored = self.dismiss_skill_error(deadline)
                if restored is not None and self._palette_state(restored) is not None:
                    self._close_palette(deadline)
                return {"status": "unavailable", "reason": "the game refused Order Change (skill-error dialog)",
                        "dialog_closed": restored is not None}
            if screen is None or failure is not None:
                return {"status": "uncertain", "reason": "order change screen did not appear",
                        "last_phase": getattr(last, "phase", None)}
            members = screen.signals["order_change"]["members"]
            restored = self._cancel_order_change(deadline)
            return {"status": "ok" if restored else "uncertain",
                    "reason": ("back line read; order change left unspent" if restored
                               else "back line read but the screen was not verifiably restored"),
                    "backline": [m for m in members if m.get("group") == "sub"]}
        except Exception as exc:
            return {"status": "error", "reason": f"reconnaissance input failed: {exc}"}

    def scout_enemies(self, observation: Observation, *, time_budget: float | None = None) -> dict | None:
        """Read enemy Status Changes that are missing or stale, then return to the tactical screen.

        Returns None when nothing needs reading. The dialog is read-only: opening
        and closing it spends nothing and, on the captured frames, did not change
        the selected target. Each step is verified from fresh frames.
        """
        deadline = None if time_budget is None else self._monotonic() + time_budget
        try:
            if observation.phase in ("enemy_profile", "ally_status"):
                closed = (self._close_enemy_profile(deadline) if observation.phase == "enemy_profile"
                          else self._close_ally_status(deadline))
                return {"status": "ok" if closed else "uncertain",
                        "reason": ("closed a status dialog left open" if closed
                                   else "a status dialog could not be verifiably closed")}
            wanted = getattr(self.driver, "enemies_to_inspect", lambda o: [])(observation)
            allies = getattr(self.driver, "allies_to_inspect", lambda o: [])(observation)
            if not wanted and not allies:
                return None
            read, missing = [], []
            for slot in wanted:
                self._send(Action("enemy_profile_open", target=slot))
                dialog, last, failure = self._await(lambda o: o.phase == "enemy_profile", deadline)
                if failure is not None:
                    return {"status": "error", "reason": f"observation failed: {failure}", "read": read}
                if dialog is None:
                    if last is not None and last.phase == TACTICAL:
                        missing.append(slot)
                        continue
                    return {"status": "uncertain", "reason": f"no dialog after pressing enemy {slot}",
                            "last_phase": getattr(last, "phase", None), "read": read}
                read.append(slot)
                self._scroll_status(dialog, "enemy_profile", deadline)
                if not self._close_enemy_profile(deadline):
                    return {"status": "uncertain", "reason": "Status Changes dialog did not close", "read": read}
            allies_read = []
            for slot in allies:
                self._send(Action("ally_status_open", target=slot))
                dialog, last, failure = self._await(lambda o: o.phase == "ally_status", deadline)
                if failure is not None or dialog is None:
                    if failure is None and last is not None and last.phase == TACTICAL:
                        missing.append(f"ally {slot}")
                        continue
                    return {"status": "uncertain", "reason": f"ally {slot} dialog did not open",
                            "read": read, "allies_read": allies_read}
                if dialog.signals["ally_status"].get("tab") != "status":
                    self._send(Action("ally_status_tab"))
                    dialog, _, failure = self._await(
                        lambda o: o.phase == "ally_status" and o.signals["ally_status"].get("tab") == "status",
                        deadline)
                if dialog is not None and failure is None:
                    allies_read.append(slot)
                    self._scroll_status(dialog, "ally_status", deadline)
                if not self._close_ally_status(deadline):
                    return {"status": "uncertain", "reason": "ally dialog did not close",
                            "read": read, "allies_read": allies_read}
            return {"status": "ok", "read": read, "allies_read": allies_read, "no_dialog": missing}
        except Exception as exc:
            return {"status": "error", "reason": f"enemy inspection input failed: {exc}"}

    def dismiss_skill_error(self, deadline=None) -> Observation | None:
        """Tap Close on the skill-error dialog; return the tactical frame, or None."""
        try:
            self._send(Action("dismiss_skill_error"))
        except Exception:
            return None
        back, _, failure = self._await(lambda o: o.phase == TACTICAL, deadline)
        return back if failure is None else None

    _MAX_STATUS_SCROLLS = 3

    def _scroll_status(self, dialog: Observation, phase: str, deadline) -> int:
        """Scroll an overflowing status list until its end, merging each view.

        Stops at the list's end, when the scrollbar no longer moves, or after
        ``_MAX_STATUS_SCROLLS`` swipes. Returns how many swipes were made.
        """
        key = "enemy_profile" if phase == "enemy_profile" else "ally_status"
        swipes, current = 0, dialog
        while swipes < self._MAX_STATUS_SCROLLS:
            info = current.signals.get(key) if isinstance(current.signals, dict) else None
            scroll = (info or {}).get("scroll") or {}
            if not scroll.get("scrollable") or scroll.get("at_end"):
                break
            self._send(Action("status_scroll"))
            swipes += 1
            moved, _, failure = self._await(lambda o: o.phase == phase and o.id != current.id, deadline)
            if moved is None or failure is not None:
                break
            before = scroll.get("thumb")
            current = moved
            after = ((current.signals.get(key) or {}).get("scroll") or {}).get("thumb")
            if before is not None and after == before:
                break
        return swipes

    def _close_ally_status(self, deadline) -> bool:
        self._send(Action("ally_status_close"))
        back, _, failure = self._await(lambda o: o.phase == TACTICAL, deadline)
        return back is not None and failure is None

    def _close_enemy_profile(self, deadline) -> bool:
        self._send(Action("enemy_profile_close"))
        back, _, failure = self._await(lambda o: o.phase == TACTICAL, deadline)
        return back is not None and failure is None

    def _run_order_change(self, action: Action, before: Observation, deadline: float | None) -> ActionResult:
        """Swap field slot ``servant`` with back-line slot ``target``, verified at each step.

        Before Replace every failure backs out through the close X and nothing is
        spent. After Replace the swap is confirmed only by the field slot's
        servant id changing in two consecutive tactical frames.
        """
        field, sub = action.servant, action.target
        old_id = next(a.get("id") for a in before.allies if a.get("slot") == field)
        member = next(m for m in before.signals["backline"] if m.get("slot") == sub)
        expected_name, expected_id = member.get("name"), member.get("servant_id")

        def backed_out(reason, last):
            restored = self._cancel_order_change(deadline)
            return self._result(action, "rejected" if restored else "uncertain", before, last,
                                reason=reason + ("; cancelled unspent" if restored
                                                 else "; cancel could not be verified"))

        try:
            self._send(Action("master_menu"))
            palette, last, failure = self._await(
                lambda o: o.phase == TACTICAL and self._palette_state(o) is not None, deadline)
            if failure is not None:
                return self._observation_error(action, before, last, failure)
            if palette is None:
                return self._result(action, "uncertain", before, last,
                                    reason="master skill palette did not open; nothing committed")
            state = self._palette_state(palette)
            if state != "ready":
                self._close_palette(deadline)
                return self._result(action, "rejected", before, palette,
                                    reason=f"order change is {state}; nothing committed")
            self._send(Action("master_skill", skill=3))
            screen, last, failure = self._await(lambda o: o.phase == "order_change", deadline)
            if screen is None or failure is not None:
                return self._result(action, "uncertain", before, last,
                                    reason="order change screen did not appear; nothing committed")
            members = screen.signals["order_change"]["members"]
            incoming = next((m for m in members if m.get("position") == 3 + sub), {})
            from .order_change import names_match
            if not names_match(incoming.get("name"), expected_name):
                return backed_out(f"back-line slot {sub} shows {incoming.get('name')!r}, "
                                  f"expected {expected_name!r}", screen)
            if self._selected_positions(screen):
                return backed_out("member screen opened with a selection already made", screen)
            outgoing_name = next((m.get("name") for m in members if m.get("position") == field), None)
            for position, wanted in ((field, {field}), (3 + sub, {field, 3 + sub})):
                self._send(Action("order_change_member", target=position))
                chosen, last, failure = self._await(
                    lambda o, wanted=wanted: (o.phase == "order_change" and self._selected_positions(o) == wanted
                                              and (len(wanted) < 2
                                                   or o.signals["order_change"].get("replace_enabled") is True)),
                    deadline)
                if chosen is None or failure is not None:
                    return backed_out(f"selection {sorted(wanted)} was not observed", last)
            self._send(Action("order_change_confirm"))
        except Exception as exc:
            return self._result(action, "error", before, None, reason=f"order change input failed: {exc}")

        # Seen live: two copies of the same servant, so the id cannot change. Then
        # the slot's own state (HP, NP, skill readiness) must change instead: the
        # incoming copy arrives with its own HP and gauge and every skill fresh.
        same_servant = type(old_id) is int and expected_id == old_id

        def slot_state(observation):
            ally = next((a for a in observation.allies if a.get("slot") == field), {})
            return (ally.get("hp"), ally.get("np"), tuple(s.get("ready") for s in ally.get("skills", [])))
        before_state = slot_state(before)
        prior = None
        last = None
        for observation in self._poll(deadline):
            if isinstance(observation, _ObservationFailure):
                return self._observation_error(action, before, last, observation.error)
            last = observation
            current = (next((a.get("id") for a in observation.allies if a.get("slot") == field), None)
                       if observation.phase == TACTICAL else None)
            if same_servant:
                state = slot_state(observation) if current == old_id else None
                swapped = (state is not None and None not in state[:2]
                           and all(value is not None for value in state[2]) and state != before_state)
                current = (current, state) if swapped else current
            else:
                swapped = (type(current) is int and current != old_id
                           and (expected_id is None or current == expected_id))
            if swapped and prior == current:
                mark = getattr(self.driver, "mark_order_change_used", None)
                if callable(mark):
                    mark(field, sub, outgoing_name)
                evidence = ([f"field slot {field} holds another copy of servant {old_id}: HP, NP and skill "
                             f"readiness changed from {before_state} to {current[1]} in two consecutive "
                             "tactical frames"] if same_servant else
                            [f"field slot {field} servant changed {old_id} -> {current} "
                             "in two consecutive tactical frames"])
                if expected_id is None:
                    evidence.append(f"incoming {expected_name!r} has no unambiguous id; identity not cross-checked")
                return self._result(action, "confirmed", before, observation, evidence=evidence)
            prior = current if swapped else None
        return self._result(action, "uncertain", before, last,
                            reason="Replace was sent but the swapped servant was not confirmed")

    def _finish_skill(self, action: Action, before: Observation, deadline: float | None = None) -> ActionResult:
        target_sent = False
        target_dialog_seen = False
        last: Observation | None = None
        pending_cooldown: int | None = None
        prior_was_tactical_evidence = False
        prior_was_tactical_unready = False
        unavailable_observed = False
        effect_observed = False
        replaced_frames = 0
        dark_streak = 0
        for observation in self._poll(deadline):
            if isinstance(observation, _ObservationFailure):
                return self._observation_error(action, before, last, observation.error)
            last = observation
            if observation.phase in ("skill_error", "skill_unavailable"):
                return self._result(action, "rejected", before, observation,
                                    reason=f"skill rejected by {observation.phase}")
            if observation.phase == "target_selection":
                target_dialog_seen = True
                prior_was_tactical_evidence = False
                prior_was_tactical_unready = False
                target_error = self._validate_skill_target(action, observation)
                if target_error:
                    return self._result(action, "uncertain", before, observation, reason=target_error)
                if not target_sent:
                    can_target = getattr(self.driver, "can_target", None)
                    if callable(can_target) and not can_target(action.target):
                        if self.trace:
                            self.trace.record("target_wait", {"observation_id": observation.id,
                                                              "target": action.target,
                                                              "target_count": observation.target_count,
                                                              "reason": "target dialog does not match observed living allies"})
                        continue
                    if self._deadline_expired(deadline):
                        return self._result(action, "uncertain", before, observation,
                                            reason="execution time budget exhausted before target input")
                    try:
                        self._send(Action("skill_target", target=action.target, reason=action.reason))
                    except Exception as exc:
                        return self._result(action, "error", before, observation,
                                            reason=f"target send failed: {exc}")
                    target_sent = True
                    continue
            if observation.phase in TERMINAL_OR_ERROR:
                return self._result(action, "uncertain", before, observation,
                                    effect_observed=effect_observed or None,
                                    reason=f"skill outcome interrupted by {observation.phase}")
            if observation.phase != TACTICAL:
                prior_was_tactical_evidence = False
                prior_was_tactical_unready = False
                continue
            if target_dialog_seen and not target_sent:
                # A dialog was observed for this skill, but its target mapping was
                # never safe to use.  Do not mistake an intervening tactical frame
                # for evidence that an untargeted cast completed.
                prior_was_tactical_evidence = False
                prior_was_tactical_unready = False
                continue
            effect_observed = effect_observed or self._effect_increased(before, observation)
            if self._ally_identity_replaced(before, observation, action.servant):
                # Seen live (Lady Avalon, Kama): a skill animation made the portrait
                # read as another servant for single frames, and the cast was
                # reported uncertain. A real replacement persists; a flicker does not.
                replaced_frames += 1
                if replaced_frames >= 2:
                    return self._result(action, "uncertain", before, observation,
                                        effect_observed=effect_observed or None,
                                        reason="servant identity changed after skill attempt")
                prior_was_tactical_evidence = False
                prior_was_tactical_unready = False
                continue
            replaced_frames = 0
            cooldown = self._increased_cooldown(before, observation, action.servant, action.skill)
            if cooldown is not None:
                if prior_was_tactical_evidence and pending_cooldown == cooldown:
                    evidence = ["same increased cooldown observed in two consecutive tactical frames"]
                    if effect_observed:
                        evidence.append("HP or NP increased for the same known ally in the same turn and wave; cast attribution is not guaranteed")
                    return self._result(action, "confirmed", before, observation,
                                        evidence=evidence,
                                        skill_consumed=True, effect_observed=effect_observed)
                pending_cooldown = cooldown
                prior_was_tactical_evidence = True
            else:
                pending_cooldown = None
                prior_was_tactical_evidence = False
            if self._skill_became_unready(before, observation, action.servant, action.skill):
                if prior_was_tactical_unready:
                    unavailable_observed = True
                prior_was_tactical_unready = True
                # Darkness only counts toward confirmation inside a known,
                # unchanged wave and turn; otherwise it stays mere evidence.
                in_context = self._same_known_actor_context(before, observation, action.servant)
                dark_streak = dark_streak + 1 if in_context else 0
                # Only our own input changes skill state during our tactical
                # phase, so a used skill that stays dark is strong evidence it
                # was cast even when its cooldown numeral cannot be read. Missing
                # this halts the whole battle; accepting it can at worst skip a
                # re-tap, never cause one. Corroboration keeps a dimmed or
                # transitional screen from counting.
                corroborated = target_sent or effect_observed
                specific = self._darkness_is_specific(before, observation, action.servant, action.skill)
                if in_context and ((corroborated and dark_streak >= _DARK_FRAMES_CORROBORATED)
                        or (specific and dark_streak >= _DARK_FRAMES_SPECIFIC)):
                    evidence = [f"skill stayed unavailable in {dark_streak} consecutive same-turn tactical frames; "
                                "cooldown numeral unreadable"]
                    if target_sent:
                        evidence.append("target dialog for this skill was completed")
                    if effect_observed:
                        evidence.append("HP or NP increased for the same known ally in the same turn and wave; cast attribution is not guaranteed")
                    if specific:
                        evidence.append("another previously ready skill stayed ready, so the screen was not globally dimmed")
                    return self._result(action, "confirmed", before, observation, evidence=evidence,
                                        skill_consumed=True, effect_observed=effect_observed)
            else:
                prior_was_tactical_unready = False
                dark_streak = 0
        if self._deadline_expired(deadline):
            reason = "execution time budget exhausted awaiting skill outcome"
        else:
            reason = ("timed out after target dialog without compatible target mapping"
                      if target_dialog_seen and not target_sent
                      else "timed out awaiting two consecutive tactical cooldown observations")
        return self._result(action, "uncertain", before, last,
                            evidence=(["skill was unavailable in two consecutive tactical frames"]
                                      if unavailable_observed else []),
                            effect_observed=effect_observed or None,
                            reason=reason)

    def _finish_attack(self, action: Action, before: Observation, deadline: float | None) -> ActionResult:
        last = None
        for observation in self._poll(deadline):
            if isinstance(observation, _ObservationFailure):
                return self._observation_error(action, before, last, observation.error)
            last = observation
            if observation.phase == CARDS:
                return self._result(action, "confirmed", before, observation,
                                    evidence=["cards phase observed"])
            if observation.phase in TERMINAL_OR_ERROR:
                return self._result(action, "uncertain", before, observation,
                                    reason=f"attack outcome interrupted by {observation.phase}")
        return self._result(action, "uncertain", before, last,
                            reason=("execution time budget exhausted awaiting cards phase" if self._deadline_expired(deadline)
                                    else "timed out awaiting cards phase"))

    def _finish_enemy_target(self, action: Action, before: Observation, deadline: float | None) -> ActionResult:
        last = None
        consecutive_matches = 0
        for observation in self._poll(deadline):
            if isinstance(observation, _ObservationFailure):
                return self._observation_error(action, before, last, observation.error)
            last = observation
            if observation.phase in TERMINAL_OR_ERROR:
                return self._result(action, "uncertain", before, observation,
                                    reason=f"target outcome interrupted by {observation.phase}")
            if observation.phase != TACTICAL:
                return self._result(action, "uncertain", before, observation,
                                    reason="target outcome left the tactical screen")
            if not self._same_known_context(before, observation):
                return self._result(action, "uncertain", before, observation,
                                    reason="target outcome has unknown or changed wave/turn")
            target_state, target_error = self._enemy_target_outcome_state(action.target, observation)
            if target_state == "transient_unknown":
                consecutive_matches = 0
                continue
            if target_state == "invalid":
                return self._result(action, "uncertain", before, observation,
                                    reason=f"target outcome is not verifiable: {target_error}")
            if observation.signals.get("selected_enemy") != action.target:
                return self._result(action, "uncertain", before, observation,
                                    reason="selected enemy marker does not match requested target")
            consecutive_matches += 1
            if consecutive_matches == 2:
                return self._result(action, "confirmed", before, observation,
                                    evidence=["requested selected_enemy and positive HP observed in two consecutive tactical frames"])
        return self._result(action, "uncertain", before, last,
                            reason=("execution time budget exhausted awaiting two selected-enemy observations" if self._deadline_expired(deadline)
                                    else "timed out awaiting two selected-enemy observations"))

    # A card turn can outlast the normal wait. Seen live (Morgan): three NPs, the
    # boss's NP, a death and a reinforcement took over 120 s, and the run stopped.
    _CARD_WAIT_SCALE = 3.0

    def _finish_cards(self, action: Action, before: Observation, deadline: float | None) -> ActionResult:
        last = None
        for observation in self._poll(deadline, scale=self._CARD_WAIT_SCALE):
            if isinstance(observation, _ObservationFailure):
                return self._observation_error(action, before, last, observation.error)
            last = observation
            if observation.signals.get("turn_advanced") is True:
                return self._result(action, "confirmed", before, observation,
                                    evidence=["turn_advanced signal observed"])
            if observation.phase in {"result", "defeat"}:
                return self._result(action, "confirmed", before, observation,
                                    evidence=[f"{observation.phase} observed"])
            if observation.phase == "network_error":
                return self._result(action, "uncertain", before, observation,
                                    reason="network error after card selection")
        return self._result(action, "uncertain", before, last,
                            reason=("execution time budget exhausted awaiting turn advancement" if self._deadline_expired(deadline)
                                    else "timed out awaiting turn advancement"))

    def _validate(self, action: Action, observation: Observation) -> str | None:
        if observation.phase not in {TACTICAL, CARDS}:
            return f"action refused in {observation.phase} phase"
        if action.kind == "skill":
            if observation.phase != TACTICAL:
                return "skill requires tactical phase"
            if action.target is not None and action.target not in {1, 2, 3}:
                return "invalid skill target"
            skill = self._skill(observation, action.servant, action.skill)
            if skill is None:
                return "unknown ally or skill index"
            ready = skill.get("ready")
            if ready is False:
                return "skill is not observably ready"
            if ready is not True:
                # An unreadable icon is a perception failure, not evidence of a
                # cooldown.  ``execute`` re-reads this before deciding.
                return UNOBSERVED_READINESS
            return None
        if action.kind == "attack":
            return None if observation.phase == TACTICAL else "attack requires tactical phase"
        if action.kind == "enemy_target":
            if observation.phase != TACTICAL:
                return "enemy target requires tactical phase"
            return self._enemy_target_error(action.target, observation, require_change=True)
        if action.kind == "cards":
            return self._validate_cards(action, observation)
        if action.kind == "order_change":
            return self._validate_order_change(action, observation)
        return f"unsupported action kind: {action.kind}"

    @staticmethod
    def _validate_order_change(action: Action, observation: Observation) -> str | None:
        if observation.phase != TACTICAL:
            return "order change requires tactical phase"
        if action.servant not in (1, 2, 3) or action.target not in (1, 2, 3):
            return "order change needs a field slot and a back-line slot, each 1..3"
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if signals.get("order_change_available") is not True:
            return "order change is not observably available"
        member = next((m for m in signals.get("backline") or [] if m.get("slot") == action.target), None)
        if not member or not member.get("name"):
            return "requested back-line member is unknown"
        if not any(a.get("slot") == action.servant and type(a.get("id")) is int for a in observation.allies):
            return "outgoing field servant is unknown"
        return None

    @classmethod
    def _enemy_target_error(cls, target: object, observation: Observation, *, require_change: bool) -> str | None:
        if type(target) is not int or target not in (1, 2, 3):
            return "invalid enemy target"
        if cls._turn_key(observation) is None:
            return "enemy target requires known wave and turn"
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if signals.get("enemy_layout") != "standard3":
            return "enemy target requires standard three-enemy layout"
        if signals.get("enemy_target_verifiable") is not True:
            return "enemy target is not observably verifiable"
        selected = signals.get("selected_enemy")
        if type(selected) is not int or selected not in (1, 2, 3):
            return "selected enemy is unknown"
        enemies = cls._enemy_slots(observation)
        if enemies is None:
            return "enemy slots are ambiguous"
        enemy = enemies.get(target)
        selected_enemy = enemies.get(selected)
        if enemy is None or type(enemy.get("hp")) is not int:
            return "enemy target HP is unknown"
        if enemy["hp"] <= 0:
            return "enemy target has non-positive HP"
        if selected_enemy is None or type(selected_enemy.get("hp")) is not int:
            return "selected enemy HP is unknown"
        if selected_enemy["hp"] <= 0:
            return "selected enemy has non-positive HP"
        if require_change and selected == target:
            return "enemy target is already selected"
        return None

    @classmethod
    def _enemy_target_outcome_state(cls, target: object, observation: Observation) -> tuple[str, str | None]:
        error = cls._enemy_target_error(target, observation, require_change=False)
        if error is None:
            return "valid", None
        signals = observation.signals if isinstance(observation.signals, dict) else {}
        if (signals.get("enemy_layout") is None or signals.get("enemy_target_verifiable") is not True
                or type(signals.get("selected_enemy")) is not int
                or error in {"enemy target HP is unknown", "selected enemy HP is unknown"}):
            return "transient_unknown", error
        return "invalid", error

    @staticmethod
    def _enemy_slots(observation: Observation) -> dict[int, dict] | None:
        slots: dict[int, dict] = {}
        for enemy in observation.enemies:
            if not isinstance(enemy, dict) or type(enemy.get("slot")) is not int or enemy["slot"] not in (1, 2, 3):
                return None
            if enemy["slot"] in slots:
                return None
            slots[enemy["slot"]] = enemy
        return slots

    @staticmethod
    def _turn_key(observation: Observation) -> tuple[int, int] | None:
        if type(observation.wave) is int and observation.wave > 0 and type(observation.turn) is int and observation.turn > 0:
            return observation.wave, observation.turn
        return None

    @classmethod
    def _same_known_context(cls, before: Observation, after: Observation) -> bool:
        return cls._turn_key(before) is not None and cls._turn_key(before) == cls._turn_key(after)

    def _validate_skill_target(self, action: Action, observation: Observation) -> str | None:
        if action.target is None:
            return "skill requires a target but no target was supplied"
        if observation.target_count not in {1, 2, 3}:
            return "ambiguous skill target count"
        if action.target not in {1, 2, 3}:
            return "invalid skill target"
        return None

    def _validate_cards(self, action: Action, observation: Observation) -> str | None:
        if observation.phase != CARDS:
            return "cards require cards phase"
        if len(action.cards) != 3 or len(set(action.cards)) != 3 or any(slot not in range(1, 9) for slot in action.cards):
            return "cards must contain exactly three unique slots from 1 through 8"
        indexed = {card.get("slot"): card for card in observation.cards}
        from .card_rules import selectable_slots
        selectable = set(selectable_slots(observation.cards, observation.signals, observation.turn))
        for slot in action.cards:
            card = indexed.get(slot)
            if card is None or card.get("type") is None or slot not in selectable:
                return f"card {slot} is sealed or unknown"
            if str(card.get("type")).lower() == "np" and card.get("ready") is not True:
                return f"NP card {slot} is not observably ready"
        return None

    def _poll(self, deadline: float | None = None, *, scale: float = 1.0):
        """Observe until the deadline, the timeout or max_polls; ``scale`` stretches both limits."""
        started = self._monotonic()
        timeout = None if self.timeout is None else self.timeout * scale
        for index in range(int(self.max_polls * scale)):
            if self._deadline_expired(deadline) or (timeout is not None and self._monotonic() - started >= timeout):
                return
            if index:
                sleep_for = self.poll_interval
                if timeout is not None:
                    sleep_for = min(sleep_for, max(0.0, timeout - (self._monotonic() - started)))
                if deadline is not None:
                    sleep_for = min(sleep_for, max(0.0, deadline - self._monotonic()))
                if sleep_for > 0:
                    self._sleep(sleep_for)
                if self._deadline_expired(deadline) or (timeout is not None and self._monotonic() - started >= timeout):
                    return
            try:
                yield self._observe()
            except Exception as exc:
                yield _ObservationFailure(exc)
                return

    def _deadline_expired(self, deadline: float | None) -> bool:
        return deadline is not None and self._monotonic() >= deadline

    def _observe(self) -> Observation:
        try:
            observation = self.driver.observe()
        except Exception as exc:
            if self.trace:
                self.trace.record("observation_error", {"type": type(exc).__name__, "message": str(exc)})
            raise
        if self.trace:
            self.trace.record("observation", observation)
        return observation

    def _send(self, action: Action) -> None:
        if self.trace:
            self.trace.record("action_request", action)
        self.driver.send(action)

    def _result(self, action: Action, status: str, before: Observation | None, after: Observation | None,
                *, evidence: list[str] | None = None, skill_consumed: bool | None = None,
                effect_observed: bool | None = None, reason: str = "") -> ActionResult:
        result = ActionResult(action, status, before.id if before else None, after.id if after else None,
                              evidence or [], skill_consumed, effect_observed, reason)
        if self.trace:
            self.trace.record("action_result", result)
        return result

    def _observation_error(self, action: Action, before: Observation, last: Observation | None,
                           error: Exception) -> ActionResult:
        return self._result(action, "error", before, last,
                            reason=f"observation failed: {error}")

    @staticmethod
    def _skill(observation: Observation, servant: int | None, skill_slot: int | None) -> dict | None:
        if servant not in {1, 2, 3} or skill_slot not in {1, 2, 3}:
            return None
        ally = next((item for item in observation.allies if item.get("slot") == servant), None)
        if ally is None:
            return None
        return next((item for item in ally.get("skills", []) if item.get("slot") == skill_slot), None)

    @staticmethod
    def _ally_identity_replaced(before: Observation, after: Observation, servant: int | None) -> bool:
        old = next((item for item in before.allies if item.get("slot") == servant), None)
        new = next((item for item in after.allies if item.get("slot") == servant), None)
        return (old is not None and new is not None and old.get("id") is not None
                and new.get("id") is not None and old.get("id") != new.get("id"))

    def _increased_cooldown(self, before: Observation, after: Observation, servant: int | None,
                            skill_slot: int | None) -> int | None:
        if not self._same_known_actor_context(before, after, servant):
            return None
        old, new = self._skill(before, servant, skill_slot), self._skill(after, servant, skill_slot)
        if not old or not new or old.get("ready") is not True or new.get("ready") is not False:
            return None
        old_cooldown, new_cooldown = old.get("cooldown"), new.get("cooldown")
        if ((old_cooldown is None or (self._is_integer(old_cooldown) and old_cooldown == 0))
                and self._is_integer(new_cooldown) and new_cooldown > 0):
            return new_cooldown
        return None

    def _skill_became_unready(self, before: Observation, after: Observation, servant: int | None,
                               skill_slot: int | None) -> bool:
        if self._ally_identity_replaced(before, after, servant):
            return False
        old, new = self._skill(before, servant, skill_slot), self._skill(after, servant, skill_slot)
        return bool(old and new and old.get("ready") is True and new.get("ready") is False)

    def _darkness_is_specific(self, before: Observation, after: Observation, servant: int | None,
                              skill_slot: int | None) -> bool:
        """Whether some OTHER skill that was ready before is still ready now.

        A skill icon can also look dark on a frame where the whole skill row is
        dimmed.  If another previously ready skill is still lit on the same frame,
        the darkness belongs to the used skill alone.
        """
        for ally in before.allies:
            for skill in ally.get("skills", []) if isinstance(ally, dict) else []:
                if not isinstance(skill, dict) or skill.get("ready") is not True:
                    continue
                if (ally.get("slot"), skill.get("slot")) == (servant, skill_slot):
                    continue
                current = self._skill(after, ally.get("slot"), skill.get("slot"))
                if current and current.get("ready") is True:
                    return True
        return False

    @staticmethod
    def _effect_increased(before: Observation, after: Observation) -> bool:
        if (before.wave is None or after.wave is None or before.turn is None or after.turn is None
                or before.wave != after.wave or before.turn != after.turn):
            return False
        old = {item.get("slot"): item for item in before.allies}
        for new in after.allies:
            prior = old.get(new.get("slot"))
            if (prior is None or prior.get("id") is None or new.get("id") is None
                    or prior.get("id") != new.get("id")):
                continue
            for key in ("hp", "np"):
                if (BattleController._is_integer(prior.get(key)) and BattleController._is_integer(new.get(key))
                        and new[key] > prior[key]):
                    return True
        return False

    @staticmethod
    def _is_integer(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _same_known_actor_context(self, before: Observation, after: Observation, servant: int | None) -> bool:
        if (before.phase != TACTICAL or after.phase != TACTICAL
                or before.wave is None or after.wave is None or before.turn is None or after.turn is None
                or before.wave != after.wave or before.turn != after.turn):
            return False
        old = next((item for item in before.allies if item.get("slot") == servant), None)
        new = next((item for item in after.allies if item.get("slot") == servant), None)
        return (old is not None and new is not None and old.get("id") is not None
                and old.get("id") == new.get("id"))
