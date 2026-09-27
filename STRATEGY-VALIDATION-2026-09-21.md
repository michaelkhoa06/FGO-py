# Sol/low strategy implementation and validation

## Implemented

- Real `gpt-5.6-sol`, low-reasoning decisions through the authenticated native
  Codex CLI. No separate API key or simulated model is used in the model runs.
- Enumerated legal choices bound to observation ID and request hash; invalid
  responses stop before input, with no automatic fallback or retry.
- Confirmed skill history and cached English skill/NP reference descriptions for
  nine servants. Generic UI verification remains independent of animation names.
- Independent-snapshot comparisons, fixed synthetic objectives, model-call
  artifacts, and per-action explanations/uncertainty.
- Remaining battle time now bounds action execution/polling as well as planning.
  An already-running bounded device operation cannot be cancelled mid-call.

## Offline checks

The integrated suite passed **88 tests** with the portable Python 3.11 runtime:
`agent-runs/planner-guide-offline-tests.txt`. The four evaluation tests also
passed after the affinity scenario was added. An earlier integration run found
a zero-poll-delay regression in the deadline change; it was repaired before live
execution. The original failure log is retained. Independent review approved
the model/controller boundary and the deadline correction.

## Model comparisons before live play

| Evaluation | Baseline | Sol/low |
| --- | --- | --- |
| Legal choices on ten synthetic snapshots | 10/10 | 10/10 |
| Predefined synthetic objectives met | 8/10 | 10/10 |
| Legal choices on six recorded battle snapshots | 6/6 | 6/6 |

The two distinguishing objectives were conserving skills on a weak opening wave
and targeting NP charge at the near-ready damage dealer instead of the
lowest-NP support. Other cases cover Merlin/Mash defense, Skadi charge
conservation, area NPs, sealed NPs, and unknown field ownership.

These are small hand-authored behavior checks, not a blinded strategy benchmark,
damage simulator or win-rate estimate. Recorded snapshots are evaluated
independently; future recorded outcomes are never attributed to changed actions.

Evidence under `agent-runs/`:

- `replay-20260921T235531Z-9a47586c`: ten synthetic cases.
- `replay-20260921T235557Z-6fb84492`: six recorded snapshots.
- `replay-20260921T235453Z-1e54061e`: initial one-case CLI smoke.

Each directory contains `evaluation.json`, `comparisons.jsonl`, `trace.jsonl`
and individual model request/schema/output/usage artifacts.

## Live validation

First AI run: `agent-runs/20260921T235855Z-fd719dbb`, same party and 10-AP novice
quest as the prior heuristic baseline. It **won uninterrupted in six turns**,
using no skills or NPs. All 12 actions and all 12 Sol/low calls completed; all
113 screenshot hashes verified. Duration: 378.7 seconds, including 134.9 seconds
in model calls. Agent module hashes were unchanged during execution.

This saved resources but did not improve turn count over the prior three-turn
heuristic run. Its explanations exposed a semantic interface issue: the
model interpreted detector affinity `weak` as unfavorable, although the
upstream scorer uses it for enemy vulnerability/advantage (weight 1.7; resist
0.6). The shared card schema now adds explicit meanings, and the persisted
`assets/strategy/PLAYBOOK.md` explains them. No animation-specific code was added.

## Persisted guide and follow-up regression

Every new policy reads `PLAYBOOK.md`, freezes it for the battle, and includes its
content and SHA-256 in each request. Every CLI call is still a fresh ephemeral
session. Guidance is not stored only in this conversation or sent as manual
corrections to a continuing CLI session. `../PROJECT-STATE.md` supports a fresh
development chat, with paths, commands, limitations and validation pointers.

The follow-up replay `replay-20260922T000735Z-4c39f642` passed 11/11 objectives
with legal choices throughout; the baseline met 9/11. The added affinity case
is explicitly a regression derived from the live failure, not a held-out test.
Sol selected all three favorable-affinity cards and explained why resisted cards
were worse. The original ten-scenario suite is retained at
`agent-runs/strategy-scenarios-initial-10.json`.

Second AI run: `agent-runs/20260922T001237Z-9715a93e`, a 30-AP advanced
Archer/Caster daily quest with Altria Caster support replacing Koyanskaya support.
The new frontline identity and initial HP/NP were correctly read. It **won
uninterrupted in five turns**: two turns per opening wave, then one final-wave
turn. All 13 actions were confirmed (five attacks, five card selections, three
skills), with 24 device inputs. Koyanskaya charged Arjuna, Altria Caster used her
party charge/ATK skill, and Arjuna used his own charge before his area NP.
All 13 model calls completed. All 138 screenshot hashes verified, and every
runtime source, catalog and guide hash matched the pre-run manifest. Each
request used the same persisted guide hash.

Duration was 470.5 seconds, including 201.9 seconds in model calls. The terminal
bond-results screenshot independently shows five turns. Evidence is in the
session's `audit.json`, `battle-result.json`, `trace.jsonl`, `model-calls/` and
`screenshots/frame-000138.png`; the pre-run manifest is
`agent-runs/planner-live-2-source-manifest.json`.

This demonstrates varied-support perception, successful verified skill use and
NP selection under the actual planner. It is not a story-difficulty benchmark.
An unreadable Arjuna HP value conservatively removed him from subsequent skill
target choices; unknown numeric state remains a meaningful limitation. MuMu was
left at the victory bond screen. The two AI quests consumed 40 AP total without
refills.

## Limits

The baseline's prior three-turn victory remains useful operational evidence;
changing card draws and enemy layouts prevent treating separate live battles
as an exact paired comparison. The planner has not demonstrated improved story
win rate. Buff/debuff recognition, enemy targeting, Master skills, break bars,
skill upgrade identification and exact damage prediction remain incomplete.

## Recommended next action

Implement observed and verified enemy targeting, with replay checks before live
validation. The current planner can choose ally skill targets, but cannot change
which enemy is selected. Keep this separate from claims about improved win rate.
