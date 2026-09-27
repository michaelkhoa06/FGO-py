# Verified enemy targeting

## Check both existing implementations first

This comparison is required before future feature work by `../AGENTS.md`.

| Source | Existing capability | Reuse decision |
| --- | --- | --- |
| Local FGA `scripts/.../modules/AutoChooseTarget.kt` | Searches Danger/Servant labels; chooses the last matching A/B/C slot; caches the choice; taps, waits 0.5s, then closes extra information | Reuse the explicit screen-slot approach and repeat prevention. Do not adopt unconditional popup dismissal or assume a tap succeeded. Label-based priority detection is not added in this change. |
| FGA `scripts/.../locations/BattleScreenLocations.kt` | Coordinates and per-slot search regions, including wide-screen adjustment | Inspect its coordinate contract: script positions are 1440p, matching templates 720p. Do not copy raw coordinates into normalized 1280x720. |
| FGO-py `FGO-py/fgoKernel.py`, `selectCard` and battle target logic | Reads enemy HP/charge, heuristically targets high HP, and changes targets before cards | Reuse current observer HP/charge and current normalized device transport; expose choices to the constrained planner instead of importing the legacy loop. |
| FGO-py `FGO-py/fgoConst.py`, `KEYMAP` | Standard target taps `(142,40)`, `(342,40)`, `(542,40)` through g/h/i | Reused unchanged. All three were verified on MuMu; although spacing differs from HP crop origins, these points fall within the corresponding enemy panels. |
| Both paths inspected | No post-tap selected-enemy confirmation in those targeting paths | Add generic selected-class HUD highlight observation and two-frame confirmation. |

Local sources:
- FGA: `C:/Users/micha/Downloads/FGA-3050/FGA-3050`.
- Original FGO-py: `C:/Users/micha/Downloads/FGO-py.portable/FGO-py`.

FGA is MIT-licensed; the existing copied result-template notice remains in
`FGO-py/fgo_agent/assets/fga-en/LICENSE`. No additional FGA code or image assets
were copied for this feature. The new detector reads the game's shared HUD.

## Implemented behavior

`enemy_target.py` observes cyan outline geometry around the selected enemy class
diamond. It requires two upper edges, a lower edge, and a mostly non-blue center.
The class label obscures part of the lower outline. A blue background alone does
not qualify. A unique matching slot with known positive HP is required. Scores
are written to the observation; they are heuristics, not probability estimates.

Only the NA standard three-position HUD is supported. Slots 1/2/3 mean screen
left/middle/right, not the letter appended to the enemy's displayed name.
Six-enemy layouts, modal/card screens, missing or duplicate highlights, and
unknown selected-enemy HP do not authorize targeting. No enemy-animation or
servant-animation recognition is used.

After a live empty-slot OCR failure, a separate presence guard now requires the
shared class-diamond outline before standard-layout HP is actionable. It handles
bronze/silver border colors and larger class symbols by geometry and contrast.
The original numeric result is retained as `hp_ocr`; without HUD evidence, `hp`
and `present` stay unknown. This does not infer death from an obscured HUD.
Both upstream sources were inspected again: FGO-py's HP/charge reads are fixed
crops without an independent presence guard; FGA's broad Danger/Servant labels
do not establish occupancy for every ordinary enemy. Neither fixes this issue
directly.

The planner enumerates `e1`, `e2`, `e3` only for eligible living nonselected slots.
Wave/turn, selected HP, requested HP, and slot uniqueness are independently
checked by planner, controller and driver. A fresh observation precedes input;
a changed decision context is rejected. Only one tap is sent. Confirmation needs
two consecutive tactical observations in the same known wave/turn with the
requested marker and positive HP. Transient unknown observations reset the
confirmation count; a known wrong selection or changed context stops uncertain.
No retry tap or automatic extra-info dismissal is sent.

At most one confirmed target change is available to the planner per wave/turn.
The decision history is bounded and the guidance is persisted in `PLAYBOOK.md`.
Fresh CLI calls continue to load current structured state and this guide.

## Validation evidence

- Initial observe `20260922T002956Z-b50a02c2`: selected slot 3 correctly recognized.
- Live controller `20260922T003053Z-46d98364`: slot 3 to slot 1, confirmed.
- Live controller `20260922T003228Z-97af4536`: slot 1 to 2, then 2 to 3, confirmed.
- Actual post-action images are copied unchanged to
  `tests/fixtures/na-enemy-selected-{1,2,3}.png`; the selected Archer/Caster HUDs
  were visually checked. Each confirmation needed two separate saved frames.
- `tests/test_agent_enemy_target.py` tests the recorded HUDs and adversarial
  missing/duplicate/filled-blue highlights, modal/card screens, six-enemy layouts,
  unreadable HP, wrong resolution and unrelated scene changes.
- Controller/driver/planner tests cover stale contexts, deadlines, invalid slots,
  known-wrong markers, transient unknowns and target-switching prevention.
- CLI plans now accept explicit integer `enemy_target` slots. The first attempt
  was rejected by the old plan whitelist before any input; that log is retained.

The live tap checks are supervised controller tests, not autonomous strategic
choices. The full suite passed **105 tests** (`agent-runs/target-all-tests.txt`).
Independent review found no blocking issue; it called out limited calibration
coverage. Sol replay `replay-20260922T003434Z-259e1dd8` met **4/4** fixed target
objectives versus baseline **3/4**, with all decisions legal. Only Sol changed
to the low-HP full-charge enemy; both retained the correct target and refused to
invent missing selection/HP. These are hand-authored checks, not win rates.

The validation quest cost 30 AP; no refill was used. Target-enabled Sol run
`20260922T003623Z-79305fbb` was **interrupted by the developer**, not completed.
Frame 39 shows only one enemy, but the empty slot 3 background OCR produced 3 HP.
The process was stopped at wave 2/turn 4 before any model-chosen target change.
The session retains `interruption.json`, all frames and requests; the runtime
manifest is `agent-runs/target-battle-source-manifest.json`. A continuation must
be reported separately, never as an uninterrupted victory.

The first presence-guard version also rejected real bronze icons; full regression
testing caught it before more live input. That failed log is retained at
`agent-runs/target-presence-all-tests.txt`. The repaired version adds bronze
Lancer/Assassin fixtures, silver Archer/Caster fixtures, the live phantom-slot
fixture, and an observer-to-planner test that excludes the empty target. Final
full-suite evidence is `agent-runs/target-presence-all-tests-v2.txt`.

The repaired build passed **109 tests** and the follow-up independent review.
Separate continuation `20260922T004617Z-259dd3ad` started from wave 2/turn 4 and
**won on overall turn 7**. All 13 actions confirmed: four attacks, four card
selections, four skills and one enemy target change. All 13 Sol calls completed;
all 133 screenshot hashes verified, and runtime files/catalog/guide remained
identical to `agent-runs/target-continuation-source-manifest.json` throughout.
Duration: 502.5 seconds, including 181.7 seconds in model calls; 23 device inputs.

Sol chose enemy slot 1 over selected slot 3 on wave 3/turn 6 because slot 1 had
24,078 HP versus 9,843. Frames 55-57 show the preflight and two confirming
observations. It did not repeat the target change after confirmation. The
continuation also used Arjuna's NP in wave 2 and Da Vinci's NP on the final turn.
The new guard withheld empty-slot raw OCR values of both 3 and 8 during this run.
Session `audit.json`, `battle-result.json`, model calls and screenshots retain
the evidence. The terminal bond screenshot was visually checked; MuMu was left
there. This is a successful continuation after repair, not an uninterrupted
full-quest win or evidence of improved story win rate.

## Limits and next action

The marker/presence checks cover recorded NA bronze Lancer/Assassin and silver
Archer/Caster HUDs, with selected-slot live checks on Archer/Caster. These
checks do not establish broad class, background, boss or story coverage. Priority
labels, enemy buff/debuff state, target lock/taunt, six-enemy layouts and exact
damage remain outside this implementation. An uncertain check stops the runner.

Recommended next action: reuse FGA's Danger/Servant label detectors and compare
their output with charged-enemy targeting on recorded boss encounters. This gives
the planner useful threat context beyond HP and charge without inventing it.
