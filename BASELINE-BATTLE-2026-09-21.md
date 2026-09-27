# Observable baseline battle: completed with developer intervention

## Outcome

The already-started NA battle in MuMu (`127.0.0.1:16384`) ended in victory on
turn 3. All three waves were cleared. Two consecutive Servant Bond result frames
were detected; the final frame was also visually inspected. The agent stopped on
that screen. No new quest, repeat, refill, or result-screen navigation occurred.

This was an **assisted integration run**, not an uninterrupted autonomous win.
The battle already contained two skill casts from the preceding controller
milestone. During this run, two stops required code fixes and explicit developer
continuations of the same pending battle. Original failed evidence is preserved.

## Implemented

- Observation-only adapter for FGO-py's skill metadata and NP/card heuristics.
- One-battle runner with logged decisions, action verification, fresh-context
  checks before input, action/turn/time bounds, and stopping on uncertainty.
- Distinct victory/defeat outcomes after two matching terminal observations.
- Four attributed English FGA result templates, with the MIT notice included.
- Same-wave tactical turn carry-over when a ready NP portrait hides the counter.

This is a baseline heuristic policy, not an AI strategy planner. Enemy targeting
still retains the game's current target. Several scoring details are adapted,
so this is not claimed to be an exact behavioral reproduction of upstream.

## Live evidence

All paths below are relative to this checkout.

| Session under `agent-runs/` | Outcome | Input taps |
| --- | --- | ---: |
| `20260921T222323Z-a779e061` | Stopped after opening a skill target dialog | 1 |
| `baseline-target-continuation` | Selected the pending target once; cooldown confirmed twice | 1 |
| `20260921T222444Z-4d9ca924` | Seven confirmed actions, then refused stale/unknown turn context on wave-2 cards | 13 |
| `baseline-cards-continuation` | Three confirmed actions; verified victory | 7 |

There were 11 confirmed primary actions across the continuation records: five
skills, three Attack actions, and three three-card submissions. The card orders
were `5,4,3`, `2,4,5`, and `5,3,1`; no NP was selected. Each input, observation,
policy reason and action result is stored in its session's `trace.jsonl`.

Final result: `agent-runs/baseline-cards-continuation/battle-result.json`.
Final screenshot: `agent-runs/baseline-cards-continuation/screenshots/frame-000041.png`.
FGA English `result` template similarity was 0.9940 on this frame; that is a match
measurement, not a calibrated probability.

## Stops and repairs

1. Arjuna's HP OCR returned unknown, leaving only two known living slots. The
   driver refused a three-target dialog. Three visible targets necessarily map
   to slots 1/2/3, so that layout now uses its unambiguous positions; one/two-target
   layouts retain the stricter living-slot check. Only the pending target was
   sent on continuation, never the skill again.
2. A ready NP portrait covered the card-screen turn counter. Fresh-context
   validation refused the card action before any card input. The observer now
   explicitly carries the preceding tactical turn within the same known wave.
   Continuation seeded that recorded tactical context and first checked the
   pending wave-2 card screen. No quest was restarted.

## Validation and limits

The pre-run suite passed 45 tests; focused regression checks passed after each
live repair. Recorded wave-2 tactical/cards and final-result screenshots were
added to offline regressions. The final aggregate suite passed **48 tests** with
the portable Python 3.11 runtime (`python -m unittest discover -s tests -p
'test_agent_*.py'`). Tests did not connect to the emulator.

Arjuna's HP and NP OCR remained unknown in many tactical frames, causing the
policy to skip useful actions, including his ready NP. Card portrait matching
has limited calibration; duplicate servant IDs retain unknown ownership. The
test battle had low-HP Hand/Arm enemies and does not establish story-boss
competence, a higher win rate, or unattended reliability.

Recommended next action: repair and regression-test HP/NP recognition, then run
one uninterrupted baseline battle before adding an AI planner. Reliable party
state is necessary for meaningful strategy comparisons.
