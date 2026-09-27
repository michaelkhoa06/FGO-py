"""A bounded, single-battle loop over the observable controller and policy."""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Callable

from .models import ActionResult, Observation


class BattleRunner:
    """Run only the battle already on screen; this class never starts a quest."""

    def __init__(self, controller, policy, trace=None, *, monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep, unknown_polls: int = 5,
                 poll_interval: float = 0.2, policy_retries: int = 1, reconnoitre=None,
                 unconfirmed_continuations: int = 3, scout=None, unknown_seconds: float = 30.0):
        if unknown_polls < 1:
            raise ValueError("unknown_polls must be at least one")
        if policy_retries < 0:
            raise ValueError("policy_retries must not be negative")
        # A failed decision sends no input, so the battle simply waits. A model
        # call that stalls once should not end a battle that is otherwise fine.
        self.policy_retries = policy_retries
        # Skills, target changes and Attack can end unconfirmed because perception
        # misread the result, not because anything unsafe happened. Deciding again
        # from a fresh frame is safe for those kinds; card selections are not,
        # because a partial selection is invisible to the observer.
        self.unconfirmed_continuations = unconfirmed_continuations
        # Optional one-time read of the back line (Order Change). It must leave
        # the battle on the tactical screen with nothing committed.
        self.reconnoitre = reconnoitre
        # Optional read-only inspection (enemy Status Changes) before deciding.
        self.scout = scout
        self.reconnaissance: dict | None = None
        self.controller, self.policy, self.trace = controller, policy, trace
        self._monotonic, self._sleep = monotonic, sleeper
        self.unknown_polls, self.poll_interval = unknown_polls, poll_interval
        # Seen live (Kama's skill, 2026-09-25): after the speed pass five frames
        # took only ~3 s, shorter than one long skill animation, and the run
        # stopped on a healthy battle. The limit needs both the frame count and
        # this much time spent without a recognizable screen.
        self.unknown_seconds = unknown_seconds

    def run(self, max_actions=80, max_turns=30, battle_timeout=900):
        """Return JSON-safe evidence for one started battle without any menu input."""
        self._validate_bounds(max_actions, max_turns, battle_timeout)
        started, actions, turns = self._monotonic(), [], set()
        terminal_count = unknown_count = policy_failures = continuations = 0
        unknown_since = None
        terminal_phase = None
        try:
            observation = self.controller._observe()
        except Exception as exc:
            return self._finish("error", f"initial observation failed: {exc}", actions)
        while True:
            if self._monotonic() - started >= battle_timeout:
                return self._finish("stopped", "battle timeout reached", actions)
            if observation.phase in {"result", "defeat"}:
                terminal_count = terminal_count + 1 if observation.phase == terminal_phase else 1
                terminal_phase = observation.phase
                if terminal_count >= 2:
                    outcome = "victory" if terminal_phase == "result" else "defeat"
                    return self._finish("completed", "two consecutive matching terminal observations", actions,
                                        outcome=outcome, terminal_observation_id=observation.id)
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            terminal_count = 0
            terminal_phase = None
            dismiss = getattr(self.controller, "dismiss_skill_error", None)
            if observation.phase == "skill_error" and callable(dismiss) and unknown_count < self.unknown_polls:
                # A rejected skill or master skill leaves this dialog open; nothing
                # is decided until it is closed.
                unknown_count += 1
                closed = dismiss(None)
                if self.trace:
                    self.trace.record("skill_error_dismissed", {"observation_id": observation.id,
                                                                "closed": closed is not None})
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            report = None
            if self.scout is not None and (observation.phase in ("enemy_profile", "ally_status") or (
                    observation.phase == "tactical" and self._known_progress(observation))):
                try:
                    report = self.scout(observation)
                except Exception as exc:
                    report = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            if report is not None:
                if self.trace:
                    self.trace.record("enemy_inspection", report)
                # Inspection moved the screen; decide only from a fresh frame.
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            if observation.phase not in {"tactical", "cards"} or not self._known_progress(observation):
                unknown_count += 1
                unknown_since = self._monotonic() if unknown_since is None else unknown_since
                if (unknown_count >= self.unknown_polls
                        and self._monotonic() - unknown_since >= self.unknown_seconds):
                    return self._finish("uncertain", "unknown observation limit reached without input", actions)
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            unknown_count = 0
            unknown_since = None
            if (self.reconnoitre is not None and self.reconnaissance is None
                    and observation.phase == "tactical"):
                try:
                    self.reconnaissance = self.reconnoitre()
                except Exception as exc:
                    self.reconnaissance = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
                if self.trace:
                    self.trace.record("order_change_reconnaissance", self.reconnaissance)
                # The recon moved the screen; decide only from a fresh frame.
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            turns.add((observation.wave, observation.turn))
            if len(turns) > max_turns:
                return self._finish("stopped", "maximum turns reached", actions)
            if len(actions) >= max_actions:
                return self._finish("stopped", "maximum actions reached", actions)
            try:
                action = self.policy.next_action(observation)
            except Exception as exc:
                policy_failures += 1
                if policy_failures > self.policy_retries:
                    return self._finish('uncertain', f'policy decision failed without input: {type(exc).__name__}: {exc}', actions)
                if self.trace:
                    self.trace.record("policy_retry", {"observation_id": observation.id, "attempt": policy_failures,
                                                       "error": f"{type(exc).__name__}: {exc}"})
                # Decide again from a FRESH frame, never the stale one.
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            policy_failures = 0
            # Model latency consumes the battle budget too. Never send a late decision.
            if self._monotonic() - started >= battle_timeout:
                return self._finish('stopped', 'battle timeout reached during policy decision; no input sent', actions)
            self._trace_decision(observation, action)
            remaining = battle_timeout - (self._monotonic() - started)
            # ``execute`` cannot cancel a driver operation already underway, but
            # this budget blocks any subsequent controller input after the battle deadline.
            result = self.controller.execute(action, expected=observation, time_budget=remaining)
            actions.append(asdict(result) if isinstance(result, ActionResult) else self._result_dict(result))
            status = getattr(result, "status", None)
            if status != "confirmed":
                resumable = (status in ("uncertain", "rejected")
                             and action.kind in ("skill", "enemy_target", "attack")
                             and continuations < self.unconfirmed_continuations)
                if not resumable:
                    return self._finish("uncertain", f"action was {status or 'invalid'}: "
                                                     f"{getattr(result, 'reason', '')}", actions)
                continuations += 1
                self.policy.observe_result(action, result)
                if self.trace:
                    self.trace.record("unconfirmed_continuation", {
                        "action": asdict(action), "status": status, "reason": getattr(result, "reason", ""),
                        "used": continuations, "limit": self.unconfirmed_continuations})
                observation, error = self._next_observation()
                if error:
                    return self._finish("error", error, actions)
                continue
            self.policy.observe_result(action, result)
            observation, error = self._next_observation()
            if error:
                return self._finish("error", error, actions)

    def _next_observation(self):
        if self.poll_interval:
            self._sleep(self.poll_interval)
        try:
            return self.controller._observe(), None
        except Exception as exc:
            return None, f"observation failed: {exc}"

    @staticmethod
    def _known_progress(observation: Observation):
        return isinstance(observation.wave, int) and isinstance(observation.turn, int)

    @staticmethod
    def _validate_bounds(max_actions, max_turns, battle_timeout):
        for name, value in (("max_actions", max_actions), ("max_turns", max_turns),
                            ("battle_timeout", battle_timeout)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be positive")

    @staticmethod
    def _result_dict(result):
        return {"action": asdict(result.action), "status": result.status,
                "before_observation_id": result.before_observation_id,
                "after_observation_id": result.after_observation_id,
                "evidence": list(result.evidence), "skill_consumed": result.skill_consumed,
                "effect_observed": result.effect_observed, "reason": result.reason}

    def _trace_decision(self, observation, action):
        if self.trace:
            decision = dict(getattr(self.policy, "last_decision", {}))
            decision.update({"observation_id": observation.id, "action": asdict(action)})
            self.trace.record("decision", decision)

    def _finish(self, status, reason, actions, **details):
        result = {"status": status, "reason": reason, "actions": actions, "action_count": len(actions)}
        if self.reconnaissance is not None:
            result["order_change_reconnaissance"] = self.reconnaissance
        result.update(details)
        if self.trace:
            self.trace.record("battle_result", result)
        return result
