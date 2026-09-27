# Numeric recognition and uninterrupted battle validation

**Outcome: one uninterrupted victory from turn 1 through all three waves, on
turn 3, with no manual combat input or source changes during the run.**

## Scope

Improve shared NA battle observation and validate the existing heuristic policy
on one fresh MuMu battle. This does not introduce an AI strategy planner.

The controller does not recognize servant-specific NP animations. It verifies
shared UI signals: cooldowns, card-selection screens, turn/wave changes, and
result screens. NP availability combines shared seal checks with the NP gauge
and explicitly carried same-turn tactical observations. Manually recognizing
an animation during development is supporting evidence only, not runtime logic.
Skill/card strategy still uses upstream servant metadata; perception and action
verification remain separate from that strategy data. Generalization across
different parties has not yet been established by this single-lineup test.

## Repairs

- HP/NP OCR uses strict integer parsing and bounded threshold variants. Repairs
  require agreement; the padded fallback also checks the original digit sequence.
  Conflicting or insufficient readings remain unknown.
- NP portraits can cover the turn counter. Card screens carry the preceding
  same-wave tactical turn, with provenance, instead of trusting obscured digits.
- Ready-NP brightness can blink. Explicit seal checks remain authoritative;
  same-turn NP values can corroborate availability when brightness is transient.
- A transient target-count mismatch now causes bounded waiting without input.
  A target is sent at most once after the mapping becomes compatible. Once a
  target dialog has appeared, cooldown frames cannot confirm a skill until its
  target has actually been sent. No skill retry is introduced.
- Two unavailable-icon observations no longer end the confirmation window early.
  A briefly obscured identity may recover during bounded polling. Confirmation
  still requires two consecutive matching cooldowns with the same known actor,
  wave and turn; permanent identity uncertainty still stops without retrying.

## Offline validation

The complete `test_agent_*.py` suite passed **57 tests** in 21.133 seconds using
the portable Python 3.11 runtime. Evidence:
`agent-runs/final-offline-validation-57.txt`. Tests do not connect to MuMu.
Independent review of the numeric, context, NP-seal, and target-dialog changes
found no remaining blockers for this bounded validation.

## Development runs retained

Three earlier fresh attempts stopped and needed explicit developer continuations.
They are not uninterrupted successes:

| Session under `agent-runs/` | Stop and continuation |
| --- | --- |
| `20260921T224206Z-9d248301` | Refused card input after an obscured counter looked like another valid turn; `validation-final-wave-continuation` subsequently finished with normal cards. |
| `20260921T225904Z-6c9a438f` | Refused a transient two-target reading for three living allies; `validation-target-continuation` selected the pending target once and later finished with NP/card order `8,3,2`. |
| `20260921T231715Z-d6c6bd5f` | A briefly unknown actor interrupted cooldown evidence; two unavailable-icon frames prematurely stopped polling. `validation-cooldown-continuation` confirmed the already-consumed skill without pressing it again, then finished in victory on turn 3; 81 recorded screenshot hashes verified. |

Original traces, screenshots, and audit JSON files are preserved locally.
Four 10-AP quests were selected during this validation work; no AP refill or
premium item was used. Quest setup was performed separately through logged
developer ADB inputs. The production battle runner still does not select quests
or navigate rewards.

## Final unchanged-build run

The third attempt above stopped and is not a clean success. Its audit verifies
all 59 screenshot hashes. Of 99 tactical ally observations, one had unknown HP;
the other HP/NP pairs parsed, which does not establish their accuracy against
independent labels. Evidence: `agent-runs/final-attempt-stop-audit.json`.

The subsequent fresh run **completed in victory**:
`agent-runs/20260921T233001Z-fc6550bd`. It started on turn 1 with all skills
available and finished on turn 3 after all three waves, without intervention.
Two consecutive matching terminal observations established the outcome; the
final Servant Bond screenshot was also inspected visually. MuMu was left there.

| Measurement | Result |
| --- | --- |
| Confirmed actions | 15: nine skills, three Attack actions, three card submissions |
| Card orders | `4,5,3`; `4,2,3`; `6,8,5` (slots 6 and 8 are NP selections) |
| Input taps | 27, all through the controller |
| Observations and screenshot hashes | 138 recorded, 138 verified, zero hash failures |
| Tactical ally HP/NP observations | 144; two unknown HP values and one unknown NP value |
| Trace duration | 6 minutes 23.7 seconds |
| Agent module source hashes | Nine files unchanged from the pre-launch manifest |

Selecting two NP cards does not prove both fired: the battle can end before later
cards execute. Action confirmation here establishes submitted sequence and
subsequent progress/result, not each individual card's damage or effect.

Evidence: `agent-runs/corrected-clean-validation-audit.json`,
`agent-runs/corrected-clean-code-manifest.json`, and the session's
`trace.jsonl`, `battle-result.json`, and `screenshots/frame-000138.png`.
The 71 unknown-phase frames include animations/transitions; the controller
waited through them within its bounds. Unknown numeric values remained explicit
rather than being filled with guesses. The successful run does not erase the
three development stops listed above.

## Scope of the evidence and next milestone

The live test uses two Koyanskaya of Light servants and Arjuna (Alter), against
low-HP enemies in a three-wave quest. It cannot establish story win rate,
boss-mechanic understanding, or recognition accuracy across different parties.
Numeric fields that parse are not automatically independently verified labels.

The next strategy milestone should choose only legal actions from structured
observations, retain uncertainty, and explain choices in the trace. Evaluate its
decisions against the heuristic baseline on replay scenarios covering different
servant roles and skill effects before broadening live tests. This avoids
building a library of individual animations as a prerequisite for strategy.
