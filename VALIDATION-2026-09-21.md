# First milestone validation — 2026-09-21

## Delivered

An isolated local clone of FGO-py v21.1.1 (`7b0cb32`), explicit English GUI
selection, a MuMu/NA-only observation and bounded-action CLI, typed state and
action records, saved screenshots, JSONL traces and English summaries. The
portable source checkout was left unchanged. No updater, commits or pushes ran.

The new controller is a foundation for adaptive planning. It does not yet call a
language model or autonomously decide skills/cards. The upstream combat policy
has not been routed through it yet.

## Offline evidence

28 focused tests pass across the final affected-test manifest:

| File | Tests | Coverage |
|---|---:|---|
| `tests/test_agent_controller.py` | 12 | bounded polling, unknown observations, no repeated skill press, cooldown/identity/context evidence, target validation, observation failures |
| `tests/test_agent_trace.py` | 1 | serializable observation/request/result JSONL |
| `tests/test_agent_transport.py` | 5 | foreground checks, no read-only taps, exact serial, coordinate scaling |
| `tests/test_agent_driver.py` | 4 | survivor targeting, ambiguous mapping, turn/wave tracking, deferred enemy targeting |
| `tests/test_agent_cli.py` | 2 | full-plan validation, unreadable numbers remain unknown |
| `tests/test_agent_vision.py` | 4 | ordinary tactical screen, blank screen, real recorded cooldown transition, target-modal precedence |

The initial suite found a real readiness mismatch on available golden icons;
the detector was corrected and its fixture passed. A separate synthetic test
exhausted its fake observation stream; its polling bound was corrected while
preserving production observation-error behavior. The affected tests were rerun
after each repair; the count above aggregates those final passing runs.

CLI help and Qt English-catalog loading passed. `git diff --check` passed. A
focused independent review found unknown enemy targeting and wave-transition
confirmation defects. Enemy-target execution is now disabled pending a real
selection detector; progress tracks known wave/turn pairs. The focused recheck
found no remaining actionable findings in those reviewed contracts.

## Live evidence on MuMu

The selected instance was `127.0.0.1:16384`, running FGO NA in the foreground at
1920x1080. Frames were normalized to 1280x720. The user manually prepared battle
wave 1, turn 1 with all skills available.

1. **Observation:** all three Servant identities and HP matched the inspected
   screen. NP values were 13%, 0%, and 75%; all enemy HP values were 1428.
2. **Self-skill attempt:** Arjuna (Alter), field slot 3, skill 2 was pressed
   exactly once. NP visibly increased from 75% to 100%, and cooldown 5 appeared.
   The original controller result was **uncertain**, because the cooldown crop
   was wrong. That original result and its frames remain preserved in
   `agent-runs/20260921T214517Z-dafb6ecf`. Corrected offline reprocessing of two
   distinct post-action frames now confirms consumption; that is an offline
   replay result, not a retroactive claim of a live pass.
3. **Targeted attempt:** field slot 2, skill 1 was pressed exactly once. The
   target dialog stayed open. The original controller stopped **uncertain**
   after confusing the visible background Attack button with tactical state.
   Original evidence: `agent-runs/20260921T214750Z-8be0e776`.
4. **Correction and continuation:** modal detection was moved ahead of tactical
   detection and verified against the saved dialog. A one-off developer harness
   continued only that pending selection, choosing field slot 3. It did not
   press the skill again. Two tactical observations of cooldown **8** confirmed
   consumption. Evidence: `agent-runs/target-continuation/trace.jsonl` and
   `results.json`. This was an explicit developer continuation, not an automatic
   retry or a new public resume feature.

Exactly three game taps were recorded across these checks: two skill buttons and
one target. No Attack/card selection was sent. The game was left at wave 1,
turn 1 after the two skill uses.

## Limits

- Readiness/cooldown vision has only narrow validation on this lineup; cooldown
  OCR remains opt-in. Ambiguous readings remain unknown.
- The final target result required developer continuation after a detector fix;
  a fresh uninterrupted targeted run with the final code has not been executed.
- Attack, face-card and NP submission are implemented with bounded checks but
  have not been exercised live in this milestone.
- One/two-survivor target mapping is unit-tested, not live-tested.
- Buff/debuff semantics, break bars, enemy selection confirmation, persistent
  party memory and an AI planner remain later work. Face-card ownership is
  currently unknown; grouping is recorded.
- Polling limits and ADB subprocess timeouts are bounded, but the polling timeout
  is not a hard wall-clock deadline for all detection work.

See [README-AGENT.md](README-AGENT.md) for use and the next development steps.
