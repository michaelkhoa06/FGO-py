# FGO agent: start here in a fresh development chat

## Goal and working rules

Build an adaptive FGO NA story-battle agent on MuMu. User prioritizes general
servant coverage over animation-specific recognition, English output, and an
explicit **Recommended next action** at the end of substantive final replies.
User authorized live tests and supplied AP. Use existing AP; do not infer
authorization to purchase currency or refill with premium items. No commits or
pushes have been requested. Preserve earlier failed-run evidence.

Gameplay/model corrections belong in persisted project files, never only in
chat or manual instructions to a continuing CLI conversation.
Before adding any feature, inspect both local FGA and original FGO-py for reusable
behavior, assets, coordinates and tests. Record both findings and reuse decisions.

## Locations

- Development repo: `C:\Users\micha\Downloads\FGO-codex\FGO-py`.
- Original installed copy: `C:\Users\micha\Downloads\FGO-py.portable\FGO-py` — leave it alone.
- Python: `C:\Users\micha\Downloads\FGO-py.portable\Python311\python.exe`.
- MuMu serial: `127.0.0.1:16384`; package `com.aniplex.fategrandorder.en`.
- Strategy guide: `FGO-py/FGO-py/fgo_agent/assets/strategy/PLAYBOOK.md`.
- English reference cache: adjacent `na.json`; explicit `Agent.ps1 catalog --ids ...` refresh.
- Setup and implementation: `FGO-py/STRATEGY-PLANNER.md` and `README-AGENT.md`.
- Results: `FGO-py/STRATEGY-VALIDATION-2026-09-21.md` and, for the policy
  comparison that supersedes it, `FGO-py/CODEX-VS-LEGACY-2026-09-22.md`.

## Current architecture

Observer -> structured observation -> allowed actions -> Sol/low -> strict
request-bound validation -> fresh observation -> bounded controller -> evidence.
`Agent.ps1 battle --policy codex --objective farm_fast --account dedicated --serial 127.0.0.1:16384`
runs ONE already-started battle. Default `--policy legacy` remains the heuristic baseline. The model
backend uses authenticated native Codex CLI with `gpt-5.6-sol`, low reasoning.
Every decision is an ephemeral fresh CLI session with disabled tools and an
isolated read-only cwd. A policy loads the saved guide once per battle and
includes its content/hash in each request with at most 24 confirmed skill events.
It does not accumulate a conversation. API keys are not required for this backend.

Verified enemy targeting is now available for the NA standard three-position HUD.
Master skills, quest selection, result navigation and AP refills
are not part of the production battle runner. Developer setup uses a separately
logged helper at `FGO-py/agent-runs/validation_setup.py`; inspect current screenshots
before setup input. Never assume MuMu remains on the previous screen.

## Verified versus pending

- Previous heuristic baseline: uninterrupted three-turn victory; 57 prior tests.
- AI integration: 88 offline tests passed after guide/affinity changes; focused
  four evaluation tests passed after adding the eleventh replay scenario.
- Initial real Sol replay: 10/10 synthetic objectives versus baseline 8/10;
  six recorded snapshots produced legal decisions. These are not win rates.
- First actual AI run `20260921T235855Z-fd719dbb`: uninterrupted six-turn victory,
  no skills/NPs used, 12 model calls, 113 screenshot hashes verified. Slower in
  turns than the earlier baseline. Exposed `weak` affinity misinterpretation.
- Corrected shared meaning is persisted: `weak` means enemy vulnerability/bonus
  damage; `resist` means reduced damage. The guide is loaded automatically.
- Follow-up replay: Sol met 11/11 synthetic objectives, baseline 9/11; all
  choices legal. The added affinity case is a regression, not a held-out test.
- Second AI run `20260922T001237Z-9715a93e`: uninterrupted five-turn victory
  on the 30-AP Archer/Caster Advanced daily, with Altria Caster support.
  All 13 actions confirmed, including three charge skills and Arjuna's final-wave
  NP; all 13 model calls completed and 138 screenshot hashes verified.
  Runtime sources and strategy guide stayed unchanged. Duration 470.5 seconds.
- MuMu was left at the victory bond-results screen. These two AI quests spent
  40 AP total; no refills were used. Inspect the current screen before input.
- Story win-rate improvement remains unproven. Numeric OCR can still leave HP
  unknown and conservatively remove skill targets, as seen in the second run.
- Enemy-target integration: 109 offline tests passed after the presence repair; Sol met 4/4 target-choice
  replay objectives versus baseline 3/4, all legal. Supervised live target changes
  to all three positions were confirmed from two frames each. See
  `FGO-py/ENEMY-TARGETING.md` for the FGA/FGO-py comparison and evidence.
- Target-enabled run `20260922T003623Z-79305fbb` was developer-interrupted at wave
  2/turn 4: a disappeared enemy's background OCR yielded 3 HP. A class-HUD presence
  guard now preserves raw `hp_ocr` but withholds actionable HP without presence.
  Its first version rejected bronze ranks; this was repaired and full tests passed.
- Separate continuation `20260922T004617Z-259dd3ad` won on overall turn 7 from
  wave 2/turn 4. All 13 actions confirmed (four skills, four attacks, four card
  selections, one model-chosen enemy target); all 13 model calls completed.
  All 133 screenshot hashes verified and runtime hashes stayed unchanged.
  The target changed from slot 3 to higher-HP slot 1 on wave 3/turn 6. Empty-slot
  OCR values 3 and 8 were retained diagnostically but withheld from actionable HP.
  This is an assisted diagnostic quest with a successful continuation, NOT an
  uninterrupted full-battle victory. Total additional quest cost: 30 AP, no refill.
- Current last verified MuMu state (end of 2026-09-22 session): **Chaldea Gate
  menu**, AP 30/138, no battle in progress and no agent process active. Ten quest
  starts were spent at 10 AP each across the day; no refills. Inspect the screen
  before any input.
- Operator caution recorded from this session: navigating menus with BATCHED
  blind taps caused two unintended detours (a Recommended Support Setup screen and
  the Present Box) and appears to have claimed some login-bonus presents (saint
  quartz went 298 -> 300). Both were backed out without saving. Navigate one tap at
  a time, verifying a fresh screenshot between taps. Inspect
  the screen before future input.

## Readiness deadlock fixed in the controller

`_validate` refused a skill whenever readiness was `is not True`, lumping
UNREADABLE together with OBSERVED COOLDOWN. That is the same category error
already fixed for HP, and it is why legacy failed one battle twice on 2026-09-22.
An observed `False` still refuses immediately and sends nothing; an unreadable
value is now re-read up to three times within the same deadline and refuses with
`skill readiness is unobserved`. This is in the controller, so it repairs the
BASELINE too. The icon-brightness heuristic itself is unchanged.

## Extreme session, 2026-09-22 (evening)

Evidence: `FGO-py/CODEX-VS-LEGACY-EXTREME-2026-09-22.md`. Daily Ember Gathering
Archer/Caster Extreme (40 AP), user's double-Koyanskaya + Arjuna Alter party with a
2/6/4 MXP04 Koyanskaya support. Clean runs so far: legacy 8 turns / 32 actions /
820s; Codex 3 turns / 16 actions / 476s. Legacy spends every battery on turn 1 and
then grinds wave 2; Codex budgets batteries across waves per the written procedure.

AP authorization: user allowed AT MOST THREE golden apples (Golden Fruit) for these
tests, never saint quartz. The Restore AP dialog lists Saint Quartz FIRST; the
Golden Fruit is the second row and shows a confirmation dialog. Golden apples used
so far: 2 of 3 (the last authorized apple is still unused).

Fixes this session: controller confirms a cast from stable same-turn darkness when
the cooldown numeral is unreadable; observer treats an impossible cooldown 0 on a
dark icon as unreadable (a two-digit 10 lost its leading digit in the crop);
PLAYBOOK.md has a written three-turn procedure with an end-plan-early rule; guide
size guard raised to 20,000 with a test that loads the shipped guide.

The support list reorders between visits: select the support by verifying its row.

## Order Change: built, validated live, first clean three-NP clear (2026-09-22)

See `FGO-py/ORDER-CHANGE-AND-MASTER-SKILLS.md`. Enable with `--order-change`
(Chaldea Combat Uniform only; anything else is refused). The battle reads the back
line once by opening the member screen and closing it with the X, which leaves
Order Change unspent. `20260922T233701Z-ac378eda` cleared Rider/Saber Extreme in 3
turns with 3 NPs, uninterrupted, using a 10/10/10 support Koyanskaya and swapping
Oberon in for wave 3. The 2/6/4 support cannot reach a third NP even with Oberon.
Legacy has not yet been run with the 10/10/10 support, so that comparison is open.

## Story mode (2026-09-23)

Use `--objective survive_and_win` (the GUI's "Story / unknown" option). The guide
has a "Story battles" section: damage is the default, focus fire, defence held for
full enemy charge or low allies, keep one defensive answer per enemy near full
charge, avoid skills that raise ENEMY charge, and expect story enemies to revive
or refill HP. These were generalized from the Orkney loss (LB6 Sect 19, progress
6: `20260923T000514Z-b34d685b`, defeat) without servant or stage names.

Mid-run stops the user saw (`20260923T015042Z-36926b1c` and an earlier turn-9 run)
had ONE root cause: an ally's HP digits misread (real 880 HP read as ".880" /
"1.880"), which marked the servant not alive, which stopped the observer reading
its skills. Her skills were never offered for four turns, and a cast she did make
was never confirmed, so the runner stopped. Fixes: `hp_fraction` read from the HP
bar's colour (fixture `tests/fixtures/story/castoria-880hp-unreadable.png`),
liveness from a filled bar when digits fail, and the value itself left unknown.

Resilience: an unconfirmed skill, target change or Attack no longer ends the
battle. The runner decides again from a fresh frame, at most 3 times per battle
(`unconfirmed_continuations`), and the planner withholds that skill for the rest
of the turn. Unconfirmed card selections still stop, because a partial selection
is invisible to the observer.

Failure notes: `--notes FILE` sends operator notes as `stage_notes` on every
decision. Keep them for fight-specific facts on a RETRY of the same fight (e.g. a
boss that revives); put anything general in PLAYBOOK.md instead, so lessons do not
over-fit one battle.

Turn counter: the tactical crop ended at x=872 and read "10 Turn(s)" as 1, so
the controller never saw turn 10 advance and stopped (run
`20260923T022006Z-0b370108`). Now `_TURN_COUNTER = (846, 81, 880, 111)`; fixtures
`story/turn10-two-digit.png`, `story/turn9-one-digit.png`. The card-screen crop is
evidence only and was left unchanged. The resumed run `20260923T022726Z-415f5bfa`
won turns 10-15 with 16 policy actions and no operator battle input.

Open, not fixed: on that fight Mélusine's 14,500 HP read as 4,500 (dropped
leading digit; bar full), and her third skill reads readiness None. HP is only
informative to the policy, but a dropped digit is a plausible wrong value, not
an unknown. Consider cross-checking HP digits against `hp_fraction`/`catalog_hp_max`.

Model and speed (2026-09-23): the default is `gpt-6-sol`, with the Codex Fast tier
(`service_tier="priority"`) on by default (`--no-fast` turns it off). The server
refused gpt-6-sol to codex-cli 0.145.0 (PATH) but served it to 0.155 (the Codex
desktop app's binary under %LOCALAPPDATA%/OpenAI/Codex/bin), so `find_codex`
now picks the highest-version native codex.exe. Boss run
`20260923T024457Z-e1ea26cd` (victory, 5 turns, 22 actions, 779 s): median
decision 6.1 s (was 9.8 s on gpt-5.6-sol), one 60 s timeout recovered by the
retry. The model is only ~19% of wall time. Executing and confirming actions is
~62%: each skill takes 10-18 s to confirm, cards 30-73 s (the cards time
includes NP animations). Speeding up skill confirmation is the next speed lever.

Enemy Status Changes (2026-09-23): `fgo_agent/enemy_profile.py`. Long-pressing an
enemy class icon at ENEMY_ICONS opens a read-only dialog; its X at (1058, 117)
closes it. The observer reports it as phase `enemy_profile`, the driver keeps a
profile per slot, `controller.scout_enemies` does open, read and verified close,
and the runner calls it on tactical frames (`--inspect-enemies`, default on for
codex). The planner sends `enemy_profiles`. Neither FGA nor upstream FGO-py has
this. First live read, run `20260923T031918Z-e56a7bf7`: 3/3 enemies read and
closed cleanly. Calibration used operator taps (Back from cards with nothing
selected, two long-presses and the X), logged in
agent-runs/validation-setup/inputs.jsonl.

Story coverage (fixed 2026-09-23): the catalog covered only ~16 farming servants,
so story allies had no skill text. `fgoAgent.py catalog --all`
(`refresh_full_catalog`) now loads all 417 playable NA servants from Atlas's
single `export/NA/nice_servant.json` (45 MB, about 3 s; records for already
cached servants came out identical). Identity: a story guest Senji Muramasa
showed an Alter Ego icon while FGO-py lists #302 as a Saber, so the class-gated
portrait match failed. An unidentified ally is dropped from `_living_allies`,
which removes all its skills from the AI's options. `fgo_agent/identity.py` now
falls back to OCR of the battle name label, resolved against the servant index
at word boundaries, with the class icon breaking ties. A class mismatch is kept
as `identity_source: name_label_class_differs`, and the planner warns that it may
be a story variant.

Plan-ahead mode (2026-09-23, `--plan-ahead`, off by default): on the first tactical
frame, one Codex call (`task: battle_plan`) schedules every remaining turn (wave,
ordered skill/OC/target ids, NP slots). Catalog skills now carry
`cooldown_by_level` from Atlas. The planner serves the schedule without model
calls, and code picks cards from all legal triples that contain exactly the
planned NPs. It re-plans, with the previous plan and the deviation, when a turn's
wave differs, a planned skill is unavailable, a planned NP is not ready, or an
enemy's HP rises within a wave: at most once per turn and 5 per battle. Otherwise
it falls back to turn-by-turn decisions. First live run, Ember Extreme
`20260923T214552Z-3584e943`: a true 3T victory with 1 model call (14 s, 20k input
tokens), 17 actions, 384 s wall, no deviation. The earlier turn-by-turn 3T run
`20260922T233701Z-ac378eda` took 9 calls, 156k input tokens and 616 s, but used
gpt-5.6-sol, so the wall-time gap mixes model and mode. Not yet tried on story
fights, where re-planning matters most.

First story run with plan-ahead, `20260923T215952Z-5189fcad` (3-wave normal stage,
double Koyanskaya + Arjuna Alter, launched WITHOUT --order-change): victory in 8
turns, 4 model calls (1 plan + 3 re-plans), all 31 actions from plans, 817 s.
- The opening plan scheduled no NP for wave 2 (Buster buffs only, batteries held
  for Turn 3), so wave 2 survived and took an extra turn. That is a plan-quality
  miss against the PLAYBOOK's 3T procedure.
- The wave-3 boss (272k HP) took 5 turns. The front line died and the back line
  (Oberon, Draco, Mash) came in.
- Bug, fixed: steps name slots, so after the replacement "s1-3" cast Oberon's S3
  instead of Koyanskaya's. Plans now store `roster` (slot -> servant id); a
  mismatch is a deviation, and a planned Order Change updates the expected
  servant.

Ally status and seals (2026-09-23): tapping an ally portrait opens its dialog.
`enemy_profile.read_status_dialog` reads both dialogs; ally phase is
`ally_status`, which can open on Details (then tap Status at (567, 70)) and closes
at (1087, 64). It gives name, real ATK, and effects with durations. The driver
re-reads an ally when its icon-row signature changes (once per turn, max 30 per
battle) and the planner sends `ally_statuses`. Upstream isSkillNone fires on the
ally dialog, so a recognized status dialog overrides skill_error and
skill_unavailable. `card_rules.py`: sealed cards stay selectable, and a fully
sealed hand passes the turn with them (verified in-game). An NP Seal from
ally_statuses blocks that NP card even when the card image reads ready (it did
once). Planning calls use medium reasoning, and a plan with an NP-less wave is
sent back once.
Sealed story fight `20260923T225700Z-b90e8d80`: a scripted "survive 2 turns"
battle with every skill, NP and card type sealed. The plan (1 call, 25 s)
correctly passed turns 1-2 with sealed cards and held Arjuna's NP for turn 3; the
fight ended by script after turn 2. An earlier attempt had swapped in Nitocris
(Alter) via Order Change, but the seal is field-wide. Calibration taps
(portrait, Details tab, sealed card, Back) are in validation-setup/inputs.jsonl.

Black Wolf story fight, 2026-09-24 (defeat; runs `20260924T002459Z-6a1b5ebe`,
`...015642Z-6b3e6b43`, `...020835Z-fd2265eb`). Party: Castoria, Mash (support)
and Draco, no back line; lone Caster boss, 249k HP, regen each turn, Damage Cut,
resistance to all attacks.
- Fixed along the way: the game's "no switchable Sub Members" refusal is now
  dismissed (`dismiss_skill_error`, Close at (640, 563) as upstream), with Order
  Change marked unavailable; any leftover skill-error dialog is closed before
  deciding; an empty OCR crop crashed the ONNX model and lost a whole dialog; a
  scrolling list's scrollbar merged all rows (text region now stops at x=1045).
- Plan quality, the cause of the loss: the opening plan spent Castoria's
  targeted invincibility and every buff on Turn 1 for Draco's first NP. When the
  Wolf reached full charge (Turn 5-6), nothing defensive was left but Mash.
  Castoria died Turn 4, Mash Turn 7, then Draco. A general rule, "support and
  defensive NPs count; schedule protection for the turn an enemy's charge fills",
  was added to the plan instructions mid-fight.
- Follow-up (2026-09-24): `_defense_issue` sends a survive_and_win plan back once
  when it spends every ready defensive skill (catalog text: invincible, evade,
  damage cut, target focus/taunt, guts, DEF up) and every defensive NP before the
  first expected enemy NP (turn = now + max - current charge, including turns
  past the plan's end) and schedules none on it. Replayed on the real Black Wolf
  plan it does NOT fire: Mash's DEF up, Target Focus and NP were kept (and used
  turns 5-6). So the loss was attrition plus an over-optimistic plan (two Draco
  NPs expected to kill 249k through Damage Cut and regen, with the plan ending at
  turn 4). The rule guards the all-in pattern; it is not the fix for that loss.
- Durable bosses (2026-09-24): the plan instructions now say an enemy whose
  profile shows HP regen, damage cut or resistance, Guts, revive, invincibility or
  evasion will outlast its HP. `_durability_issue` sends a plan back once when it
  ends before such an enemy's first NP turn, asking for sustain and defence
  through that turn. Replayed on the real Black Wolf opening plan it fires
  ("ends on turn 4 ... before its Noble Phantasm on turn 6").
- Rematch `20260924T063109Z-1da0e468` (same party): defeat on turn 5. The opening
  plan already ran to turn 10 (the instruction worked; no revision was needed).
  Draco's S2 was not ready on turn 2, so the fight was re-planned. The re-plan
  (and the executor following it) then held everything: turns 2-4 had no
  skills and no NPs, Mash's whole kit was reserved for the charged attack on
  turn 6, Castoria's NP sat at 110-115% and Draco's at 100% from turn 4. The
  Wolf's crit-boosted normal attacks killed Mash on turn 3/4, then the rest.
  Lessons (not yet built): (a) an ally death or a large HP loss since the plan
  must trigger a re-plan, since a dead slot reads id None and the roster check
  skips it; (b) a plan must not idle a full damage NP for turns against a
  regenerating boss, and "protect for the enemy NP" must not mean "ignore normal
  attacks". Planning calls at medium effort took 45-51 s each.
- Built 2026-09-24: `_vitals_change` re-plans when an ally dies or loses >= 50%
  of its HP bar since the plan. `_held_np_issue` sends a story plan back when a
  full OFFENSIVE NP is not fired this turn or next. NP role (`np_role` in
  servant_metadata) is "offensive" when any NP version deals damage, else
  "support" (370/47 across the catalog). Support NPs (invincibility, stun,
  damage cut, buffs) are exempt and saved for their moment, per the user's rule.
- After the user's Command Spell revive (all NP full; operator intervention), run
  `20260924T064725Z-161a870d` won in 3 turns with 2 calls. Castoria's support NP
  went on the Wolf's full-charge turn together with Draco's NP. On turn 8 the
  HP-loss trigger fired (Draco 100% -> 50%), and the re-plan taunted with Mash to
  protect Draco while Draco's NP finished the Wolf.
- Defeat was detected (two defeat frames) and no Continue was tapped. The game
  crashed once and ADB timed out once (emulator issues, not agent input).

Run retention (2026-09-24, user request): `fgo_agent/retention.py` keeps the
newest 5 battle/observe/execute session folders and, separately, the newest 5
`replay-*` folders in agent-runs (`--keep-runs N`). It runs at the start of each
new session and never touches the current session, a folder containing a
`KEEP` file, or anything not named like a session (scripts, logs,
validation-setup). agent-runs was 9.8 GB before the first prune. Run IDs cited
in this file and the older docs will no longer exist on disk after pruning; the
fixtures that matter were copied into tests/fixtures.

322k Black Wolf, 2026-09-24. Run `20260924T070124Z-e6762742` took the Wolf to
705 HP on turn 4; it refilled to 390k (refill trigger and defence revision both
worked live). The run then stopped on turn 6 because a Stunned Castoria's cards
and NP were picked. Upstream CHARASEALED is CN text; the NA "Stunned" template
(assets/cards/stunned.png) scores >= 0.8 on stunned cards and <= 0.4 on 7,400+
other frames. Stunned cards are sealed and never used as sealed filler. Status
list scrolling was calibrated live: swipe (680,540)->(680,330), 700 ms. It
revealed three effects the first view hid; clipped top rows are dropped and
titles merge fuzzily. Resumed run `20260924T072940Z-3a3067d2` won turns 6-8 with
no operator battle input: Draco's NP on turn 6, re-plan on Mash's death, and
Castoria's support NP held. Retention's first prune removed 93 folders
(agent-runs 9.8 GB -> 2.0 GB).

Cost trims (2026-09-24, user-approved), both untried live so far:
- Planning effort: `planning.reasoning` is "medium" for the opening plan and for
  crisis re-plans (ally death or >= 50% HP loss, slot holds a new servant, enemy
  HP refill), and "low" for revisions and minor re-plans (skill or NP not
  ready, plan ran out, wave mismatch). The backend honours it for battle_plan
  calls. Medium plan calls took 25-49 s.
- Ally status re-reads: a changed icon row is re-read at most every 3 turns
  (`ALLY_REREAD_TURNS`); a new servant in a slot is read at once. Inspections had
  cost 92-122 s per run (about a quarter of the battle). Revisions were already
  capped at one per plan (all checks share one feedback message).

Boss-mechanic preparation (2026-09-24). Olympus' Demeter (GamePress Ch7-2) and all
eight Avalon le Fae boss guides (GamePress LB6 directory) were surveyed and
grouped into general mechanic types. Added:
- Adaptive status cadence: `driver.statuses_volatile` switches ally and enemy
  re-reads to every turn once an enemy profile shows a triggered infliction
  ("Activate when Attacking (Inflict Curse)") or an ally gains a new debuff
  (seal, stun, curse, poison, burn, Nullify Buff, block, max-HP cut). Otherwise
  a changed icon row is re-read every 3 turns. An enemy NP (charge falling from
  full) or a break (enemy HP rise) re-reads allies at once. Enemies now carry a
  `status_icons` row signature, so a boss cycling self-buffs is re-read.
- Planner: an enemy charge jump of >= 2 in one turn is a crisis re-plan.
- card_rules: Stun, Charm, Sleep, Petrify, Pigify or "unable to act" from the
  Status tab blocks that servant's cards and NP until expiry; buffs that only
  name them ("Stun Resist Up", "Charm Immunity") do not.
- Guide lines (cap raised 24000 -> 26000): Nullify Buff (cheap buffs first),
  cleanse delayed debuffs before they fire, don't buff right before scheduled
  buff removal, an enemy that NPs every turn is beaten by burst plus sustain.
Known unsupported: field HP gauges that lose the battle (Albion's Storm Border),
which are not read at all; those fights need the user.

Guide consolidation (2026-09-24, user request). PLAYBOOK.md sections are scoped
by objective (`PlannerPolicy._SECTION_SCOPES`): story calls get about 9.9k chars
(was 23.8k), farming calls 18.4k. The story section is a four-step priority order:
(1) no ally dies this enemy phase, (2) fire offensive NPs when ready, (3) save
support NPs and defence for the enemy's NP or a crisis, (4) otherwise damage. It
is followed by a short state glossary. Prose that restated code rules (sealed
NPs, sealed-hand passing, seals overriding NP readiness) was removed. Revisions
remain one per plan whatever the number of objecting checks (test
`OneRevisionTests`). Pre-consolidation copy: scratchpad `PLAYBOOK.before.md`.
Follow-up: `_PLAN_INSTRUCTIONS` now carries planning mechanics only (entry and ID
format, cooldown counting, NP-at-100%, re-plan behaviour; 1.5k chars, was ~3.5k).
The seal sentence, "do not hedge" and all strategy were removed; strategy comes
from the guide priorities. A new unscoped "Shared tactics" section holds the
demerit check, "don't hinge on unknowns" and Order Change, so story calls get them
too (story guide 11.5k chars). The guide cap is back to 24,000 (file: 20.8k).

Provisional rules (per AGENTS.md "Rule changes"; confirm on a replay or a second,
different fight before they stay):
- CONFIRMED defence-reserve plan check (`_defense_issue`): Black Wolf, then the solo
  Gareth fight (2026-09-24), where the revision kept DEF/Target Focus for the
  knight's turn-5 NP instead of spending them on turn 3
- CONFIRMED vitals re-plan on >= 50% HP loss: Black Wolf, then Gareth (64% -> 4%;
  the re-plan used Guts and fired the NP). Its "died" branch fired falsely once,
  caused by the 1-HP observer bug below, not by the rule
- CONFIRMED low/medium effort split: live medium opening 29 s, low re-plans 6-12 s
- durable-boss plan check (`_durability_issue`), which fired correctly on the Wolf replay only
- idle offensive NP check plus `np_role`; one live win so far, same boss
- charge-jump crisis re-plan; fired correctly live once (knights, 2026-09-25)
- adaptive status cadence, enemy icon cycling and break/enemy-NP re-reads; offline only
- incapacitation card guard (charm/sleep/petrify); offline only
- the 3-turn ally re-read cap; not yet exercised live (Gareth read once, then misread dead)
- guide lines on Nullify Buff, delayed debuffs, buff-removal timing, every-turn NPs
- Shared-tactics "Fire NPs inside the buff window, and give that servant the turn" plus plan `focus` (focus confirmed in plans, not yet in card choice) (user advice, 2026-09-24: stack
  targeted buffs on the dealer, spend charges so NPs land within the buffs'
  duration, time Order Change into the same window)
Order Change is now ON by default (`--no-order-change` to disable); an empty back
line or another Mystic Code leaves it unavailable, and Codex may decline it.

### Solo Gareth story fight (2026-09-24): victory, 10 turns, 12 min

Gareth (story guest, identified by name label) alone against two knights (~300k).
Won, but three generic faults showed, all fixed with tests (302 pass):
- 1 HP read as dead. From turn 4 Gareth sat at 1 HP: empty bar, digits unreadable,
  so `alive` was False and her 256-300% NP and ready skills were hidden for six
  turns of plain cards. A readable NP gauge now proves a living servant (empty
  slots show no NP text) and also triggers the name-label identity fallback.
  Fixtures `tests/fixtures/story/one-hp-guest*.png`, test `OneHpAllyTests`.
- Plan budget counted calls. Revisions were charged to `_MAX_BATTLE_PLANS`, so two
  crises spent the budget by turn 5 and every later action cost its own model
  call. The budget now counts plans (`PlanBudgetTests`).
- A lone legal action (only "attack") still called the model; it is now served
  without one (source `only_allowed_action`).
Watch item, no rule added: an offensive NP that fills a turn earlier than the plan
predicted (Gareth 120% on turn 2, planned for turn 3) is not a deviation, so it
waits for the planned turn. Revisit if it costs a fight.
Launch note: run Agent.ps1 through the Bash tool (`powershell.exe -NoProfile
-Command ...`); the PowerShell tool turns the game's stderr log into a terminating
NativeCommandError under Agent.ps1's `$ErrorActionPreference='Stop'`.

### Arts 3T story stage (2026-09-24): victory in 7 turns, not 3

Double Castoria + Ruler Melusine against three trash waves (run with `farm_fast`
so the three-turn procedure applied; the user wanted to see whether Codex reads an
Arts team). The opening plan did read it: "Melusine is the area-NP dealer, both
Altrias supply party and targeted NP charges", and fired her NP in waves 1 and 2
(one model call). Wave 3 failed: Melusine opened it at 84%.
Cause (perception, generic): her ready S3 "Blue Horizon EX" (NP charge 50%) has
dark art (brightness 165), so the brightness test left it `ready: None` all battle
and the plan never offered it. 84% + 50% would have fired wave 3. Fix: a cooling
skill always prints NA "Turn(s)" under its icon. Template
`assets/skills/cooldown_turns.png` (min-channel, averaged from 60 live icons):
ready icons score <= 0.41, cooling >= 0.76 over both 2026-09-24 runs; >= 0.6 marks
cooling, < 0.5 resolves an unknown icon to ready. Replaying both runs: no ready/
cooling flips, 148 unknowns became ready, 26 became cooling, and no living ally's
skill stays unknown. Tests `CooldownLabelTests`.
Also: "7 Turn(s)" read as 71 (the wide crop caught the "T"), and the re-plan
believed wave 3 was on its 69th turn. The counter only stays or advances by one, so
a jump equal to the plausible value plus a stray trailing digit is repaired
(signal `turn_repair`).
After the wave-3 deviation, the re-plan fired all three NPs (support + damage)
at once, which is reasonable; the last enemy then took three turns of cards.
Next check: re-run an Arts 3T now that Melusine's S3 is visible.
Re-run (Daily Ember Rising, same team, `farm_fast`, 2026-09-24): VICTORY IN 3 TURNS,
2 model calls (medium opening + one low re-plan), 7.7 min. Blue Horizon read ready
on turn 1 and was cast first; the cooldown-label fix is confirmed live. The opening
plan still over-counted wave 2: Melusine reached 88%, not 100%, so the planned NP
did not fire and face cards cleared the 1.3k-HP wave instead (fine here, a
risk on sturdier waves). Watch: NP gauge arithmetic after an NP refund.

### Tam Lin Lancelot boss (2026-09-25): victory on turn 9, no operator battle input

Double Castoria + Draco vs Tam Lin Lancelot (196k, full charge every turn, two
scripted HP refills to 239k and 264k). Master Skill was grayed out for the stage.
Opening plan (medium): both Castoria S3s on Draco, party charges to fire Draco's NP
and Castoria's defensive NP on turn 1 (matches the user's stated ideal setup).
Both refills and three ally deaths (auto-replaced by Oberon, Muramasa and Grimr)
triggered crisis re-plans, all handled by the roster/vitals checks; the plan budget
(5) ran out on turn 8, so the last two turns used per-action calls. The final
Grimr NP killed the boss exactly as the 1,800 s battle timeout expired, so the
runner logged the last action "uncertain"; the screen showed RESULT.
Wall time 30 min: enemy phases/card animations 694 s, status inspections 447 s
(refills force full re-reads), skills 361 s (~17 s each), model calls 177 s
(9 calls), grayed Master Skill wait 90 s.
Fixes (307 tests pass):
- Grayed Master Skill: the palette wait is now ~8 s (`BattleController._PALETTE_WAIT`)
  and an unopened palette marks Order Change unavailable (`GrayedMasterSkillTests`).
- Ally HP 16,922 read as 116,922: a six-digit value led by 1 drops that digit
  (signal `hp_repairs`, `HpLeadingOneTests`).
- Launch boss fights with `--battle-timeout 3600`.
Open speed items: status inspections (25% of wall time) and ~17 s per skill.

### Speed pass (2026-09-25), after the 30-minute Tam Lin fight

Measured on the Tam Lin recording: a frame was captured every 2.2-3.9 s, and one
skill took ~17 s (6 captures: animation frames, two confirming frames, then two
more before the next tap). Per frame: PNG `screencap -p` 1.02 s, a 1 s controller
pause, and 1.9 s of analysis on tactical frames (OCR 1.06 s for 16 fields, portrait
matching 0.6 s). Changes (313 tests pass):
- Raw `screencap` (RGBA, 12/16-byte header) with PNG fallback: 0.35 s instead of
  1.02 s (`decode_raw_screencap`, live-verified colours). Upstream FGO-py uses
  airtest JAVACAP and FGA uses MediaProjection; raw screencap needs nothing on
  the device, so it was preferred. JAVACAP stays an option if more is needed.
- `_MemoOcr`: OCR answers from memory for byte-identical crops; portrait identity
  cached on the class-icon + portrait pixels. Replaying 260 Tam Lin frames gave
  zero differences on non-card frames (card frames differ only because a fresh
  uncached observer lacks the previous tactical frame), and a
  tactical observation dropped from ~1.9 s to 0.46 s (median).
- `BattleController._fresh`: a decision frame under 1.5 s old is the "before"
  frame, so a scheduled plan step no longer re-captures the screen.
- Battle controller poll interval 1 s -> 0.4 s (max_polls 200; the 120 s wait
  timeout remains the bound). Frames saved per run will rise (a run was ~860 MB);
  retention keeps 5 runs.
Expected: ~1 s per frame instead of 2.2-3.9 s, so skills and inspections roughly
halve; game animations (enemy phase, NPs) are unchanged. To be measured live.
Measured live (three Caster knights, ~515k HP, 2026-09-25): VICTORY in 4 turns,
7 min 53 s, 4 model calls. Median frame gap 0.95 s (was ~2.5 s); skill 8.9 s avg
(was 17.2 s); attack 2.3 s (was 7.3 s); 2 inspections 62 s. The charge-jump crisis
re-plan fired live for the first time (enemy 2 charge 1 -> 3) and was correct; the
defence-reserve revision fired again. Order Change reconnaissance read a back line
(Melusine, Space Ereshkigal, Elisabeth); Codex did not swap in a 4-turn win.
Battle bounds raised to 300 actions / 3600 s (the 3600 s launch was refused).

### High Queen Morgan boss (2026-09-25): DEFEAT on turn 8 (random team)

Castoria + Draco + Space Ereshkigal, back line Arjuna (Alter), Melusine, a second
Arjuna (Alter). The user expected a loss with this team and will build a proper
one. Three generic faults blinded or stopped Codex, all fixed (318 tests pass):
- Enemy layout: upstream `setupEnemyGird` chose the 6-enemy grid when Morgan's
  large icon let a small class template pass (~0.19 < 0.2) on most frames. HP was
  read as 3,610,802, a false "HP refill" crisis re-plan followed, and afterwards
  her HP was mostly unknown (plans said "enemy HP is unknown"). A clear large
  standard-row icon (CLASS[93] < 0.1 and better than the small score) now
  overrules it (signal `enemy_grid_override`, `EnemyGridTests`). There is no genuine 6-enemy frame
  to test against yet, so the veto is conservative. Majority voting was tried
  and rejected: the wrong grid was the majority.
- Card shortlist: on turn 1 Draco's and Ereshkigal's NPs were ready but no NP
  triple made the top-10 scored shortlist, so the per-action model could not
  fire either. The best triple for every distinct NP set is now always kept.
- Order Change onto a second copy of the same servant (Arjuna Alter for Arjuna
  Alter) happened in-game, but the swap check wanted the slot's id to change and
  stopped the run as uncertain. A same-servant swap is now confirmed by the slot's
  HP/NP/skill readiness changing and holding steady (`SameServantSwapTests`). The run was relaunched
  mid-fight and continued to the defeat.
Codex did use Order Change on its own (turn 6, swapping out a 1,200-HP ally with
no ready skills for a fresh copy): the first live, policy-chosen swap.

### Focus servant (user idea, 2026-09-25, Morgan rematch)

The user saw Codex fire Space Ereshkigal's NP while Star Ciel carried important
time-limited buffs; Ciel's NP plus her face cards would have used those buffs.
Plan-ahead only named NP slots, and face cards were picked by the upstream
chain scorer, which knows nothing about buffs. Now each plan entry carries
`focus` (field slot or null): the executor prefers triples whose face cards
belong to that servant among chains firing exactly the planned NPs, and the
scorer breaks ties. The guide's buff-window bullet (Shared tactics) was revised,
not extended: the buffed servant is the turn's focus, and another damaging NP
costs them a card slot unless it clears the wave or defends. Provisional until
seen live. Cards of duplicate servants have no owner, so focus cannot favour one
copy. Other fixes that session: card-turn wait x3 (a 3-NP + boss-NP + death turn
outlasted 120 s), and HP "14,266 -> 4,266" dropped-digit repair within a turn.

### Morgan rematch (2026-09-25): DEFEAT on turn 8 (Castoria, Lady Avalon, Draco)

Turn 1 burst took Morgan from 226k to 24k over three turns; her scripted refill
to 273,680 then coincided with Castoria and Lady Avalon dying (Oberon and Star
Ciel reinforced). Turn 4: Oberon, Ciel and Draco NPs fired as [Oberon, Ciel,
Draco] and dealt only ~22k: Oberon's NP text applies Invincible (1 turn)
[Demerit] to all enemies, and the executor's scorer chose the order, not Codex
(which knew about the demerit in the Tam Lin fight). Fixes (324 tests pass):
the plan's `np` list is now the firing order, and any NP whose catalog text
applies a [Demerit] to enemies is placed last in the chain (`NpOrderTests`).
The run also timed out after that turn (card wait now x3) and was resumed; the
resumed part lost Draco and Ciel and ended on turn 8 with Morgan at ~97k.

### NP demerits made explicit (user feedback, 2026-09-25)

The user asked whether Codex could know what an NP does (Arash kills himself,
Chen Gong sacrifices an ally, Oberon buffs enemies). The full Atlas NP text was
already in every request (`catalog_noble_phantasms`, field and back line; 28 of
417 NPs carry a [Demerit]); Oberon's failure was the executor's card order. To
make the danger impossible to miss, `np_demerits` now lists each NP's [Demerit]
clauses (`PlannerPolicy._np_demerits`), and the guide's existing demerit bullet
(Shared tactics) now covers NPs. `NpDemeritTests`.
Morgan retry (third attempt): the plan's `focus` field worked live (Draco), and
Codex planned Order Change (Oberon for a spent Lady Avalon) on turn 2. The swap
backed out unspent because the member name read "OberonW" in reconnaissance
and "Oberon XX" on the member screen. `names_match` now also accepts one
shared run of 5+ letters with at most 3 stray characters per reading ("Arjuna"
vs "Arjuna (Alter)" still differ; `NoisyNameTests`). Relaunched from turn 2.

### Morgan third attempt, continued (2026-09-25): DEFEAT on turn 10 (phase 3, ~284k)

Turn 1-4 went to plan (focus Draco; Castoria's NP for protection; Draco NP);
phase 2 (273k) fell to Castoria + Lady Avalon NPs and Oberon/Draco/Ciel NPs.
Codex avoided Space Ereshkigal's poison-demerit skill while Ciel was low (the
demerit guidance working). Two generic faults, fixed (331 tests pass):
- Reinforcements: Oberon, Ciel and Space Ereshkigal all came in from the back
  line after deaths, but the back line kept its reconnaissance contents, so
  Order Change was still offered and the game refused (user confirmed the
  warning dialog by hand; nothing spent). The driver now pops the entering
  member off the back line (`_track_reinforcements`, `ReinforcementTests`).
- Enemy charge rate: Morgan gained 2-3 charge a turn; `_danger_turns` assumed +1,
  so the saved defence was timed a turn late again and again. The largest per-turn
  gain seen this wave now sets the danger turn and is sent to Codex as
  `planning.enemy_charge_gain_per_turn`.
User plan: use a Leyline Stone (revive, NP gauges full) rather than repeat.

### Enemy-charge skills: rule was a ban, now a cost (user question, 2026-09-25)

The user asked why Codex never used Draco's S2/S3. S2 (Aurea Poculum) has
"increase Charge [Demerit] for all enemies", and story priority 4 said "Never use
a skill that raises ENEMY charge while an affected enemy can reach full charge
before it dies"; against Morgan (fills 2-3 a turn) that banned it for good, and
S3 (Seven Beast Crowns) needs S2's [Holy Grail Possession] state for most of its
value. Priority 4 now treats enemy charge as a cost weighed against the skill's
gains (cheap vs an enemy that fills every turn or two, or with defence ready),
and Shared tactics gains "Some skills only work after another" (cast the
enabling [State] skill first; value the pair together). Guide 22.6k chars.
Provisional: the absolute rule came from an earlier fight; check that Codex now
uses such skills sensibly and does not hand a slow-charging enemy an early NP.

### Final-attack skills (user feedback on Oberon, 2026-09-25)

Oberon's S2 (charge, then "decrease NP Gauge at the end of the turn") and S3
(one-turn Buster/NP boost, then buffs removed and [Eternal Slumber]) only pay
off when the target fires its NP that turn with everything stacked; Codex cast
half the kit. Now: the guide's demerit bullet says such a skill is a final
attack (cast only on the target's NP turn, with every other buff and charge),
and a plan check `_now_or_never_issue` asks for the single revision when a plan
casts one on an ally whose NP is not in that turn's `np`. The text pattern
matches exactly Oberon S2/S3, Tamamo (Lancer) S3, Ishtar (Rider) S3 and
Passionlip S3 in the NA catalog (delayed 3-turn stuns and self-cleanse heals
excluded). Tests `NowOrNeverTests`; 334 pass. Provisional.

### Morgan cleared after the user's Leyline Stone (2026-09-25): VICTORY on turn 15

From turn 11 (party revived, gauges full; guide as of launch, i.e. before the
enemy-charge-cost and final-attack changes): Draco NP with Castoria/Lady Avalon
buffs took phase 3 from 220k to 121k; Oberon + Lady Avalon NPs and Draco's next
NP brought it to 28k; Lady Avalon's invincibility was timed for Morgan's full
charge; Space Ereshkigal's NP finished it. 12 min, 6 model calls, no operator
battle input. Live confirmations: reinforcement tracking popped all three
back-line members (`backline_reinforcement` x3); `enemy_charge_gain_per_turn`
reached the plan ("charge gain of 3 per turn"). Codex also declined Oberon's S3 on
Draco because it would put Draco to sleep. Still open: Draco S2/S3 remained unused
(this run loaded the old "never raise enemy charge" rule).

### Triple Morgan (2026-09-25): interrupted by a game crash on turn 7

Castoria, Lady Avalon, Melusine vs three Morgans (226k each); back line Oberon,
Star Ciel, Draco. Melusine's area NP carried turns 1-4 with Lady Avalon's
invincibility and Castoria's NP for defence; reinforcements tracked correctly.
Draco (last survivor, turn 7) used S1, S2, then S3: the enemy-charge cost rule
is CONFIRMED live. Then FGO crashed (ADB "closed", launcher in front).
Oberon (per-action mode, plan budget spent): Morning Lark on himself, then only
Ciel's NP fired, because the one listed Oberon + Ciel triple put Oberon's NP
(Invincible to enemies) first. Fixed (336 tests pass):
- The card shortlist keeps an enemy-buffing NP last in every triple whenever its
  NP set allows (`ShortlistDemeritTests`).
- A charge jump no larger than the enemy's measured rate is no longer a crisis
  re-plan (it spent all 5 plans by turn 6) (`ExpectedChargeRateTests`).

### Triple Morgan rerun (2026-09-25, after a game update): scripted end on turn 9

The stage is a scripted survival fight: RESULT after turn 9 with "Enemy
Remaining: 3" and +0 bond; the runner records "victory" (result screen). Codex
kept a servant alive to the end. Melusine's area NP and Castoria's defensive NP
carried turns 1-6; each Morgan refilled once (to 273,680). Oberon (reinforcement,
per-action mode) used S1 and Morning Lark on himself and fired his own NP last in
the chain [card, card, Oberon NP]: the demerit-last shortlist fix worked live.
Draco used S1-S3 together on turn 7 and fired next turn. The plan budget was
again spent (two HP refills plus jumps from three enemies with different rates).
Note: the runner cannot tell a scripted end from a kill; the result screen's
"Enemy Remaining" would distinguish them if needed.

### Cathedral Guards (2026-09-25): VICTORY in 3 turns, 6 min, 2 model calls

Castoria, Lady Avalon, Melusine vs three guards (~400k). Party charges to ready
Castoria's defensive NP and Melusine's area NP on turn 1; Melusine NPs on turns
2-3 cleared it. Fault found and fixed: during a turn-1 skill animation Lady Avalon
flickered to Oberon on single frames, the controller reported "servant identity
changed" (continued as uncertain), and reinforcement tracking popped the whole
back line though nobody died. A replacement now counts only when the new servant
appears in a later turn than the old one and holds two frames. Replay on
recorded runs: 3 correct reinforcements each in both Morgan runs, none in this
one (was 3). 337 tests pass.

### Mors horde (2026-09-25): scripted end after turn 5 (87 of 99 remaining)

Double Castoria + Melusine vs a 99-enemy Mors horde in the 6-enemy grid; HP read
correctly from the small layout, and the large-icon veto correctly did not
override it (first genuine 6-grid frame seen). Melusine's area NP each turn;
Codex's PLAN scheduled Order Change on turn 2 (support Castoria, skills spent, out;
Lady Avalon in), executed and confirmed live: the first planned Order Change
outside the Ember runs. Reinforcement tracking caught Zhuge Liang correctly.
Watch item: each newly spawned Mors in a slot read as "enemy HP rose within wave"
and forced a crisis re-plan (4 in 5 turns); a horde spawn is not a refill. Not
fixed yet: HP value alone cannot tell a respawn from a boss refill.
Fixed afterwards: the observer reads "ENEMY Remaining : N" (`enemies_remaining`;
neither FGA nor upstream reads it; OCR junk like "96)", "3.", "942" handled by
taking at most two digits after the colon; 80-frame samples read correctly), and
an HP rise in the same frame as a falling count is a respawn, not a refill
(`HordeRespawnTests`, `EnemiesRemainingTests`; 340 tests pass).

### Solo Barghest vs 8 Mors (2026-09-25): VICTORY on turn 6 (Remaining 0), 9.5 min

Guest Barghest alone, Master Skill grayed. The defence-reserve revision kept Foul
Weather (damage cut) for the predicted enemy NP turn, three times. Her area NP
fired turns 3 and 6. Launched before the respawn fix, so each Mors spawn forced a
crisis re-plan (3). Watch item: on turn 5 her area NP sat at 133% with two
enemies alive and waited for turn 6 (buffs first), when only one was left; the
held-NP check allows two turns, which suits single-target NPs better than area
NPs whose value falls as enemies die.

### Large Mors (2026-09-25): stopped on turn 10 by the unknown-frame limit; resumed

Muramasa, Draco, Arjuna (Alter); back line Tezcatlipoca, Kama, Grimr. The plan
used Order Change on turn 3 (a 14%-HP Muramasa out, Tezcatlipoca in): the second
planned swap in a story fight. The run then stopped during Kama's skill animation
with "unknown observation limit reached": after the speed pass, 5 unknown frames
took ~3 s. The runner now also requires 30 s of unrecognised screens
(`unknown_seconds`) before giving up (runner test added; 341 pass). A
respawned 186k Large Mors in a slot that read HP "7" still produced one false
"HP rose" crisis (the count did not fall at that frame).
Resumed from turn 10: VICTORY on turn 13 (wave 2 of 2). Kama's skills twice
reported "servant identity changed after skill attempt" from one-frame portrait
misreads (as Lady Avalon's did). The skill check now needs the changed identity
in two consecutive tactical frames; a single flicker frame is skipped (controller
tests added; 343 pass).

### Red Dragon boss (2026-09-25): DEFEAT on turn 8 (dragon at 261k of 800k)

Castoria, Draco, Melusine; Master Skill refused (no back line). Turn 1 fired Draco
and Melusine NPs (Draco S1-S3 all used: enemy-charge cost rule working again).
Loss cause: from turn 4 both damaging NPs were at 100% and the schedule fired
neither on turns 4, 5 and 6 (Castoria died turn 4, Draco turn 7). Two faults,
fixed (345 tests pass):
- A gauge filling earlier than the schedule assumed was not a deviation (the
  Gareth watch item, now fatal). A ready offensive NP the current turn does not
  fire now flags a (minor) deviation once per turn, so the re-plan and the
  held-NP check apply (`EarlyNpTests`).
- The turn-5 re-plan frame read every NP as unknown, hiding the full gauges from
  the held-NP check. The observer carries the same servant's last readable NP
  within the turn (`np_carried`); replaying that frame now gives 100/100.
Also seen: ally HP OCR with junk prefixes ("/6,950", "M1,388") stays unknown.

### Red Dragon rematch (2026-09-25): stopped on turn 3-4 by a Codex auth failure

Four model calls succeeded, then every call returned "401 Unauthorized: Incorrect
API key" from chatgpt.com/backend-api/codex/responses, and the run stopped with
"policy decision failed without input" (the battle is left mid-fight, no input
sent). `auth_status('dedicated')` still says "Logged in using ChatGPT"; a minimal
`codex exec` on the dedicated home reproduces the 401. The Codex app had updated
to codex-cli 0.155.0-alpha.16.4 (bin/13995fba801849b0; the older binary is gone).
Diagnosis afterwards (user checked usage: 5-hour 52% left, weekly 39% left):
- Logged the dedicated home out (`auth_command('logout')`) and the user signed in
  again in the GUI: still 401 on every call.
- npm @openai/codex updated 0.145.0 -> 0.157.0 (user approved): 0.157 also 401,
  for gpt-6-sol and for the default model. 0.145 had reached model validation
  (400 "gpt-6-sol not supported with a ChatGPT account"), i.e. it authenticated.
- The same app build served this account's first four calls at 22:33; every call
  since 22:41 fails. Conclusion: an OpenAI-side authentication change for this
  account with current clients ("Incorrect API key" for a minted sk-svcac key),
  not a local credential or usage problem. `find_codex` still prefers the app
  binary over the npm shim (it only considers `codex.exe` on PATH).
- Resolved as an OpenAI incident: status.openai.com showed "Issues with Codex:
  elevated errors" (identified, ongoing) at the same time. Nothing local was
  wrong. Lesson: on a sudden 401/5xx wave, check status.openai.com before
  touching logins. The Red Dragon rematch is paused mid-fight, no input sent.
Guide change from the user's question (Castoria S3 kept "for the enemy's charged
turn"): story priority 3 no longer says bundled invincibility "is still that
answer"; a single-ally invincibility bundled with a multi-turn damage buff goes to
the damage dealer on their NP turn, with party-wide protection answering the enemy
NP when available. Provisional. (No battle-specific notes were added.)

### Red Dragon rematch 2 (2026-09-26): DEFEAT on turn 6 (dragon ~317k of 800k)

Codex was back after the OpenAI incident (one-word test OK). The FGA overlay
button hid Castoria's class icon (she read as unidentified) until the user hid
it; check for overlays before launching. Turn 4 fired Draco + Melusine NPs
together with Draco S2 then S3 (pairing rule working). Loss: Draco's S2
"increase Charge [Demerit] for all enemies" took the dragon from 3/4 to full that
same turn, and Castoria (268 HP) died holding a 246% support NP saved for turn 5.
Castoria's S3 was still held until the crisis. Cause (code, not guide): the plan
check counted it as the defensive answer to keep. Fixes (347 tests pass):
- `_defensive_options`: a skill whose only defence is invincibility/evade and
  that carries a multi-turn damage buff is not the reserve answer (Castoria S3,
  Melusine S3); "def state" wording counts as defence (Castoria's NP); NP clauses
  that buff the enemies no longer count (Oberon's NP).
- `_danger_turns(obs, raises)`: the plan's own enemy-charge-raising skills move
  the predicted enemy NP earlier (`_planned_charge_raises`), and the revision
  message says so. `DefenceReadingTests`.

### Red Dragon rematch 3 (2026-09-26): VICTORY on turn 7, 12 min, 3 model calls

With the defence-reading fixes, turn 1 put Castoria's Sword of Selection on Draco
(s1-3-t2) together with Draco's S1, S2, S3 and fired Draco's NP; Melusine's area
NP on turn 2; Castoria's NP (now counted as protection) on turn 3 against the
charged dragon. Castoria died on turn 4; Melusine and Draco finished it. This
confirms the bundled-invincibility change live (the user's request), in the fight
that motivated it; still provisional until a different fight.
Next: user asked for damage-based choices (NP vs a buffed servant's face cards).
Atlas nice data has exact skill/NP values (e.g. Castoria S3 Arts +50% at L10,
Draco NP 900-1500% by NP level). Proposed: keep numeric effects in the catalog,
track self-cast buffs, a formula-based estimator for plan and card choice, and
an account file of NP levels (default NP1). Awaiting the user's NP levels.

### Damage estimator (built 2026-09-26, user request; NP1 for every servant)

- Catalog: every skill variant, NP and class passive keeps numeric `effects`
  (Atlas nice export, 418 servants); `cards` deck; `class_relation.json`.
  `catalog --all` picks up new servants.
- `damage.py`: published formula, `chain_damage`, `BuffTracker` (buffs this
  agent applied, skill L10 / NP1, passives, trait bonuses kept separate).
- Observer reads enemy class from its icon (standard and 6-grid).
- Planner: `damage_estimates` in story requests; plan executor ranks legal
  chains by estimated damage after NP order, demerit-last and focus; card
  candidates carry `estimated_damage`. 210 chains in 3.8 ms; request +~870 chars.
- Calibration: Red Dragon turn 1 estimated 98.7k vs 249.8k actual, explained by
  class term missing from old recordings, a possible trait bonus, level-100 ATK
  and crits. Estimates rank options; they are not exact.
- Guide at 23,840 / 24,000 chars: consolidate before adding anything.

### Six Berserkers (2026-09-26): VICTORY in 3 turns, 8 min, 2 model calls

Double Koyanskaya of Light + Arjuna (Alter), back line Oberon, Mash, Merlin; six
Berserkers in the 6-grid (classes read, `damage_estimates` sent). Turn 1: both
Koyanskayas' kits on Arjuna plus all three NPs. Turn 2: Arjuna at 17%, face cards.
Turn 3: Order Change for Oberon, then Oberon S1, S2 (on Arjuna) and S3 (on Arjuna)
with Arjuna's NP the same turn: the user's "Oberon all-in" CONFIRMED live.
The user's ideal (Arjuna NP on all three turns) missed turn 2 because both
Koyanskayas' charges went into turn 1; battery budgeting across turns is in the
farm-only three-turn procedure, not in story guidance. Watch item.

### Four Berserkers (2026-09-26): VICTORY in 2 turns, 6.5 min

Same team. Turn 1: Koyanskaya kits on Arjuna, Order Change Koyanskaya #2 out
for Oberon, Oberon S1 and S3 on Arjuna, then Koyanskaya's and Arjuna's NPs.
Estimator check on the previous fight: turns 1 and 3 played the top-estimated
chain; turn 2 could not estimate because two Koyanskaya copies left face cards
ownerless. Fixed: cards keep `servant_id`, and ambiguous cards use the lower
estimate over the copies (`DuplicateServantTests`). Enemy HP read "1" after the
opening NPs in both Berserker fights (Guts-like), so no damage calibration yet.
Bug fixed: the first plan used "s1-3-t3" for Koyanskaya before an Order Change
and again for Oberon after it; the validator rejected the repeat and the turn
fell back to per-action play. A slot's skill may now repeat after an Order Change,
and issued steps are keyed by the servant in the slot (`_issue_key`,
`SkillAfterOrderChangeTests`). 355 tests pass.

### Avenger (2026-09-26): VICTORY on turn 4, 8.5 min

Double Koyanskaya + Arjuna vs one Avenger (455k); Merlin came in by Order
Change on turn 3. First clean single-target calibration of the estimator:
turn 1 estimate 91,535 vs 177,468 actual (x1.94), turn 2 29,413 vs 56,767
(x1.93): consistent, so rankings hold, but absolute values ran ~2x low (unseen
ATK, CEs, crits). Added per-battle calibration: after a turn with one living
enemy, the HP drop / estimate ratio (0.5-5, no overkill) is kept; the median of
the last five scales `damage_estimates` and is reported as `calibration`
(`CalibrationTests`; replay reproduces 1.94). Also: the early-NP deviation fired
on the very turn the plan chose to hold an NP, forcing per-action play; it now
only applies to plans made on an earlier turn. 356 tests pass.

### Confirm Party read (user idea, 2026-09-26)

New start flow: the user leaves the game on Confirm Party. `fgoAgent.py party
--serial ...` (read-only) reads each column (1-3 starting = field slots, 4-6 sub
= back line): level, HP, ATK (Fou + CE ATK included), skill levels, NP level
(`fgo_agent/party.py`), and saves `agent-runs/party-latest.json`. The screen
alternates the skill/NP line with append-skill levels every few seconds, so six
captures are taken and each field takes its most common valid reading;
`parse_levels` splits "/" misread as 4/7 only where both sides are valid levels.
The operator then taps To Battle (logged). `battle` loads the party file if under
4 hours old (`--no-party` to skip): real ATK and NP level replace the catalog
guesses, and member skill levels set buff values; a replacement servant takes
the unused sub member of its class (else the first). 360 tests pass
(`test_agent_party.py`; fixture `story/confirm-party.png`).
Game tutorials can state battle rules (Storm Border Endurance: a ship gauge the
enemy's unique attacks drain; Castoria's NP protects it). The agent cannot read
that gauge; its rule text was passed as `--notes` (use an absolute path; the
agent changes directory at start).

### GitHub repository (2026-09-26)

Public fork: https://github.com/michaelkhoa06/FGO-py (fork of hgjazhgj/FGO-py,
AGPL-3.0), default branch `codex-agent` = upstream v21.1.1 + one commit with the
agent. Since 2026-09-26 (user request) this working copy, FGO-codex/FGO-py, is
itself the git checkout (origin = fork, upstream = hgjazhgj); the separate clone
was deleted. AGENTS.md stays at the FGO-codex root, untracked (local paths).
Commits use the GitHub no-reply address; the dedicated account's email was
removed from the docs before publishing. agent-runs/ is git-ignored (it holds
screenshots and full model requests).

### GUI revamp (2026-09-26, user request)

Start from Confirm Party is now the agent's default (`--start-from-party`,
`--no-start-from-party` to disable; it taps only when Confirm Party is recognized):
the user's first GUI run from Confirm Party never attempted the party read and the
cause could not be reproduced, so it no longer depends on the GUI's flag. The session
record now logs `plan_ahead` and `start_from_party`. Empty or locked party columns
(no ATK read) are dropped.

`fgoAgentGui.py` rewritten: dark card-style theme; settings remembered with
QSettings ("FGO-Codex"/"FGO Agent": serial, account mode, player, start-from-party);
story objective only (farming removed; `gui_support.DEFAULT_OBJECTIVE` =
survive_and_win and every GUI battle adds `BATTLE_OPTIONS`: --plan-ahead, 300
actions, 100 turns, 3600 s - GUI runs previously lacked plan-ahead); the raw log,
"Open run folder" and "Observe" removed; "Start one battle" renamed "Start"; a
status card shows wave/turn, enemy HP, elapsed time, the latest decision and the
current plan (read from the run's trace.jsonl by `gui_support.battle_progress`);
plain-language results (Victory!/Defeat/stopped reason). "Open latest screenshot"
kept. Login output (redacted) still appears only during sign-in.
`battle --start-from-party` (GUI default on): if Confirm Party is showing, read
the party (6 frames), tap To Battle (logged), wait up to 2 min for the battle
screen; otherwise play the battle on screen. A saved party whose starting
classes do not match the field is discarded (`party_mismatch`). 365 tests pass.

### Barghest (2026-09-27): DEFEAT on turn 7, 18.5 min
Run 20260927T023258Z-6f093079, first live start from Confirm Party (6-frame read,
To Battle tapped and logged). User party "thrown at a wall": Draco (skills 3/3/3),
Arjuna Alter (Berserker vs a Saber boss), support Mash, Lancelot and Gawain in the back.
Demonic Dog Barghest: one Saber with 3 bars (252,972 / 313,781 / 372,354), charge
absorb. Arjuna NP'd T1, Draco broke bar 1 on T3, Arjuna and Mash died by T5, the
T6 NP wiped Draco and Lancelot, and Gawain alone broke bar 2 on T7 (brave Buster
chain, the right call), then bar 3 appeared and killed him. The damage calibration
(1.68) worked. Loss driver: team strength. Perception gap: bar diamonds under the
enemy name were on screen from T1 and never read, so Codex treated bar 2 as the
last.
Fix: `_bars_remaining` counts lit diamonds on the standard row (enemy
`bars_remaining`, sent in the request); tests `BarsRemainingTests`. Guide:
the boss sentence names it (reworded, no new rule). Provisional until a second
multi-bar boss shows the count helps.

### Black Wolf (2026-09-27): VICTORY on turn 9 after a stun fix mid-battle
Run 20260927T064048Z-209dfc9e: one Caster, 2 bars, Guts + "recover HP and survive".
`bars_remaining` read 1 then 0 correctly, and Codex planned with it ("Draco's NP
should then hit the remaining bar"). On turn 3 Draco and Arjuna were Stunned: only
Mash's Arts card and NP were selectable, card enumeration demanded three, and the
run stopped with "no legal observable actions" on the card screen.
Verified live, in order: (1) picking only Mash's two cards does NOT start the
attack; the game waits for a third. (2) Tapping a stunned card as the third DOES
(run 20260927T071259Z-09459f20: chain [1, 6, 2], turn advanced). This corrects the
09-24 note ("stunned taps did nothing": that run had picked a stunned NP).
Fix: `card_rules.selectable_slots` adds stunned (or incapacitated-ally) face cards
as a last resort when fewer than 3 others remain, as FGA does
(FaceCardPriority: "Stunned cards at the end"); `filler_slots` + the planner keep
fillers after every real card. `chain_length` (short chains) remains only for a
hand with nothing left to fill. Tests `test_two_stunned_servants_leave_a_two_card_chain`
(fixture two-servants-stunned.png), `test_stunned_cards_fill_the_chain_last`.
Result: the resumed run won on turn 9 (turns 3-9 in 11.5 min, no operator battle
input). Draco's NP took the last bar to 9,124 on turn 6, Mash died on turn 7, and
the Wolf's Guts left it at 1 HP; Codex finished it on turn 9 with brave chains,
keeping Draco's and Arjuna's NPs in hand.
Resume from the card screen: a fresh run has no trusted turn there (card-screen
turn OCR is untrusted), so the runner taps Back (1195, 683; commits nothing,
clears picks) via `controller.back_from_cards` and plays from the tactical
screen (`CardsBackTests`; worked live twice).

### Cernunnos (2026-09-27): DEFEAT on turn 6; NP Sealed on the card, fixed mid-battle
Run 20260927T072826Z-3085e072 (started from Confirm Party; Zhuge Liang, Lady
Avalon, Draco; order change on turn 1). One Berserker with 4 bars (`bars_remaining`
3 on bar 1, 2 on bar 2). The enemy's turn-2 attack sealed Lady Avalon's and Draco's
NPs; Avalon's Status tab had been read before that, so `np_sealed_allies` did not
know. The cards read ready, [7, 8, 1] was picked, the two sealed NP taps did
nothing, and the game waited for two more cards until the 2-minute timeout.
Fix: `assets/cards/np_sealed.png` (NA "NP Sealed" label, grayscale, cut live):
sealed cards score >= 0.76, 268 other card screens <= 0.39; threshold 0.6, OR-ed
into the NP seal check with upstream's CN templates and the stunned template.
Test `test_np_sealed_label_makes_the_np_unusable` (fixture np-sealed-two.png).
Resumed run 20260927T074901Z-1c3df513 tapped Back, then played [5, 1, 3] (no
sealed NP picked). Zhuge Liang and Lady Avalon fell on turn 3; the back line
(Castoria, Space Ereshkigal) came in and fell by turn 6. Draco alone fired on
turn 6 and died with the boss at 54,996 on bar 2 of 4 (2 bars still to come).
Loss driver: a 4-bar boss with NP seals and curses against this party.
Also added (generic): if the card screen stays 25 s after the picks, the game did
not take them; the controller taps Back and returns "rejected", and the runner
lets the policy decide the cards again (within the 3 unconfirmed continuations).
Tests `test_a_card_screen_that_stays_after_the_picks_goes_back`, `RejectedCardsTests`.
Not yet exercised live.

### Cernunnos rematch (2026-09-27): Tezcatlipoca team
Run 20260927T081001Z-37ad5bd7 (Castoria x2 + Tezcatlipoca; subs Zhuge Liang, Lady
Avalon, Space Ereshkigal). User asked Codex to use Tezcatlipoca's buff window
(NPs, else his face cards); no notes were passed (user declined them). His S3
Special Attack vs [Divine]/[Super Giant] is in the catalog as `target_traits`; the
estimator reports it as `needs_enemy_trait` and does not assume enemy traits.
Turn 1: Tezcatlipoca NP'd (176k -> 117k), then the Berserker boss's normal attacks
(x2 vs every class) took 9-14k from each ally and killed him while Black Sun
(party Invincible x2) was held for the projected turn-6 enemy NP.
Bugs fixed: (1) after a reinforcement the back line kept old slot numbers; the
game moves the rest up (Space Ereshkigal showed as sub 2), so an Order Change to
"b2 = Lady Avalon" was cancelled unspent. `_track_reinforcements` renumbers
slot/position. (2) A rejected (cancelled, unspent) Order Change stopped the run;
the runner now lets the policy decide again (like rejected cards).
New request data (generic): `enemy_pressure` = HP each ally lost over the last
enemy turn (or died) and each enemy's class damage multiplier per ally
(`_track_incoming`, `_enemy_pressure_view`, `EnemyPressureTests`). Guide: story
priority 1 now counts "an ally that last enemy turn's losses would kill"; glossary
line added; priorities 3-4 condensed to stay under the 24,000 cap. Provisional.
Resumed run 20260927T082002Z-686682d2 (started before `enemy_pressure` existed):
DEFEAT on turn 7. Without Tezcatlipoca there was no damage: Castoria NPs and
Avalon's invincibility kept the party alive to turn 5, bar 2 broke on turn 6, and
Space Ereshkigal (last reinforcement) fell on turn 7 with 2 bars still to come.
Bar reader fix: a pink sky right of Cernunnos's three diamonds (purple, blue,
green) read as 5; a lit diamond must now differ from the pixels 8 px either side
(its rim), and green hues count. Today's runs now read one stable count per turn
(single animation frames can read 0). Test `test_a_same_coloured_background_is_not_a_diamond`.
User decision (2026-09-27): enemy traits are ignored entirely. No trait notes and no
trait assumptions in the estimator (it already counts trait-conditional buffs only
as `needs_enemy_trait`, outside the numbers). The user builds teams; this rematch
compares Tezcatlipoca with Draco as the main damage dealer, so far worse. To be
continued on 2026-09-28 with a Cernunnos rematch.

### Cernunnos rematch 2 (2026-09-27 evening): first run with `enemy_pressure`
Run 20260927T190349Z-983c0fc9. Tezcatlipoca NP'd on turn 1 and survived (1,882
HP). On turn 2 Codex cited the new data ("took 13,653 damage last enemy turn") and
used Black Sun; Tezcatlipoca NP'd again on turn 3 and bar 1 broke. The whole front
line fell in the turn-3 enemy phase; all three subs came in.
Bugs fixed: (1) the support Castoria in slot 1 was never identified (id None), so
Zhuge Liang replacing her was not counted and the back line kept him; turn 5's
Order Change met "You don't have any switchable Sub Members" and the run stopped
("order change screen did not appear"). The driver now holds a living
unidentified servant's slot as "unknown"; a new id there counts as a reinforcement
only if it matches a back-line member (id, or one name contains the other, letters
only: "Zhuge Liang" / "Zhuge Liang (Lord El-Melloi II)"). (2) The swap itself now
handles the refusal dialog like reconnaissance does: close it, mark Order Change
unavailable, return "rejected" (the runner continues).
Tests: `test_an_unidentified_servant_replaced_by_a_back_line_member_counts`,
`test_an_unidentified_servant_later_identified_is_not_a_reinforcement`,
`test_a_refused_swap_closes_the_dialog_and_is_rejected_unspent`. Resumed as
20260927T192414Z-5d02d0d5 (dialog closed, reconnaissance: unavailable).
Result: DEFEAT on turn 8 (31 min over both runs). Space Ereshkigal NP'd on turns 6
and 8 as the last one standing; the boss ended on bar 2 of 4 at 29,740. Compared
with the Draco team (same fight): both stalled on bar 2 of 4; this team lasted two
turns longer. Bars 1-2 alone were about 176k + 244k; bars 3-4 were never seen.
Leyline continuation 20260927T193655Z-42d1dbc2 (turns 9-13, 19.5 min): DEFEAT on
turn 13 with the boss on bar 3 of 4 at 91,047 and full charge. Codex kept
Tezcatlipoca's NP off a 14,867 bar for the fresh one (turn 10: 342k bar), and
fired it again on turn 12 as his buffs ran out, but held Lady Avalon's party
Invincible for turn 13 while all three allies were at 1-3k HP after losing 5-10k
each; that enemy phase killed Avalon and Tezcatlipoca.
Fixes: (1) `_lethal_issue` joins the plan revision: when last enemy turn's loss
(`enemy_pressure`) is at least an ally's current HP and a ready defensive skill or
full-gauge defensive NP goes unused this turn, the plan is sent back once
(`LethalDamageTests`). Provisional. (2) Back-line name match allows up to 3 stray
OCR letters ("Zhuge LiangA" vs "Zhuge Liang (Lord El-Melloi II)"), which had
popped the wrong member when two subs came in together.
Second Leyline continuation 20260927T200559Z-351470c4 (turns 14-17, 15.5 min):
DEFEAT on turn 17. Turn 14 at full enemy charge: Castoria's NP (protection) then
Tezcatlipoca's NP broke bar 3 (77,927). The last bar was 537,164. Turn 16's enemy
phase (charge jumped 0 -> 2) killed Zhuge Liang (11.8k), Lady Avalon and
Tezcatlipoca (8.3k) at once; losses the turn before were 2.5-4.7k, so
`_lethal_issue` correctly did not fire. Space Ereshkigal NP'd alone on turn 17
(484k left). Over 4 attempts this team reached the last bar; the fight is a
team-strength wall.
Third Leyline continuation 20260927T202751Z-934b967b (turns 18-23, 18.5 min):
DEFEAT on turn 23 with the last bar at 51,235 (from 511,137). Codex cited
`enemy_pressure` ("slot 2 lost 10,395 HP last enemy turn"), fired Castoria's
protective NP before Tezcatlipoca's each time the boss was near full charge, and
used Zhuge Liang's charge reduction. On turn 23 Zhuge's NP and Space Ereshkigal's
NP hit for only ~19k total (the boss resists and heals), and its attacks then
killed all three.
Fourth Leyline continuation 20260927T205231Z-dcfc6afa: VICTORY on turn 24 (4.3
min). Tezcatlipoca, buffed, NP'd the last 50,235 HP. Cernunnos took the original
attempt plus 5 continuations over two days; team strength was the wall, and each
fix (NP-seal label, back line, `enemy_pressure`, lethal revision) moved it further.

### Animation speed-up taps (user tip, 2026-09-27)
A tap during a skill or NP animation doubles its speed. Measured before: a skill
spent 5-6 s on "unknown" animation frames. FGA taps after each skill/Order Change
(Caster.kt `extraInfoWindowCloseClick`, "also triggers skill speedup") and in the
middle of the screen for death/wave/NP (`battleSafeMiddleOfScreenClick`); both
points can hit an enemy or the Attack/card area if the animation has just ended,
so the agent taps the turn panel (880, 95) instead, which is inert on the tactical
and card screens. Controller: one tap after the first "unknown" frame of a skill;
during a card turn, one tap every 2 s on "unknown" frames. Driver refuses the tap
unless the latest frame is "unknown". `--speed-taps` (default on). Test
`test_animation_frames_get_speed_taps_only_when_enabled`.
Live 2026-09-27 (Cernunnos turn 24): 14 taps; skills confirmed in 5-7 s instead of
8-11 s. One bug: the first frame after tapping Castoria's targeted skill was the
transition into target selection, and the tap cancelled it (skill unused, 2-min
wait). Skill taps now wait until the target is sent or two animation frames in a
row (`test_a_single_transition_frame_after_a_skill_tap_gets_no_speed_tap`).

### FGA overlay button over slot 1 (found 2026-09-27)
The FGA floating button sat bottom-left (~(40, 687)) in today's runs and covered
slot 1's class icon. `getFieldServantClassRank(0)` returned None, so the servant in
slot 1 was unidentified: the support Castoria (Lv120, "Altria Avalon") in every
Cernunnos run and the Oberon fight. Her skills were never offered and her kit never
reached Codex (cards and NP still played). Verified: the same frame with the button
painted out identifies her as 284. Zhuge Liang still matched in slot 1. This was also
the root of the "unknown slot" back-line bug. The user moved the button to the top
right (~(1240, 35)), clear of everything the agent reads or taps. Open idea: when a
slot's class icon is unreadable, match its portrait against all classes.
Oberon fight 20260927T210403Z-33589f58: the run stopped on turn 13 with "FGO NA must
be in the foreground" while the user moved the button (the frame still showed the
battle); no input was sent, and it was resumed. Speed taps: 6-36 per card turn
(every 2 s through the whole enemy phase); harmless so far.
Correction: the game had crashed (ADB "error: closed" on the next launch); the
user reopened it and FGO restored turn 13 from its start (boss 63,471). Resumed
run 20260927T213529Z-cde56012: DEFEAT on turn 14. Tezcatlipoca alone, skills on
cooldown (next on turn 16), 76% NP; the boss fired its NP at full charge with ~42k
left on its last bar. Party read: Altria Avalon (support, Lv120), Mash, Draco; subs
Oberon, Arjuna Alter, Tezcatlipoca. Order Change on turn 2 swapped Mash for
Tezcatlipoca, citing `enemy_pressure` ("Mash lost 8,309 HP last enemy turn").
Leyline continuation 20260927T214035Z-9054de6c: FGA button removed, slot 1 now
identified (Castoria 284) and her skills used from turn 15. Bar 3 broke on turn 15
(Castoria's NP then Draco's); last bar 404,312. On turn 18 Tezcatlipoca was
Immobilized (sleep; label on his NP card), his NP read ready, [6, 7, 8] was
picked, the game ignored his tap, and the new stuck-card recovery tapped Back, but
the plan re-ran the same chain until the 3 continuations were spent.
Fixes: (1) `assets/cards/np_immobilized.png` joins the NP label check (sealed
>= 0.82 incl. a Castoria sleep earlier; others <= 0.33; threshold 0.6); test
`test_an_immobilized_np_is_unusable` (fixture np-immobilized.png). (2) After a
refused card chain, its NP cards are withheld for the rest of that turn and the
history says so (`RefusedChainTests`).
Resumed 20260927T220150Z-c8ca0d00: VICTORY on turn 20 (Oberon fight total: 20
turns, one crash, two Leyline Stones). Immobilized read correctly (slot 2 NP
sealed, 6 and 8 ready), but the plan executor, with NP [1, 2, 3] planned and 7
unusable, took the top-ranked subset: face cards only; Castoria's and Oberon's
NPs fired a turn late. Fix: when not every planned NP is legal, the executor
fires every planned NP that still is (`PartialPlannedNpTests`). Turn 19 fired all
three NPs (205k -> 61k) and Arjuna Alter's buffed cards finished it on turn 20.

### U-Olga Marie (2026-09-27): DEFEAT on turn 6, played blind
Run 20260927T222718Z-91413522 (Koyanskaya of Light, Mash, Arjuna Alter; subs incl.
Nitocris, Space Ereshkigal). The boss's "Unknown" class shows a red custom diamond;
`guard_enemy_presence` needs the standard gold/silver outline, so its HP (OCR
290,716 correct) was withheld on every frame and Codex planned with no enemy data
("enemy HP is unreadable"). Turn 6: Nitocris's NP was really "NP Sealed" (correctly
skipped). Fixes: (1) a readable charge meter with a real maximum (0/4) is presence
evidence for a non-standard class icon; a removed enemy's phantom HP reads 0/0
(`UnknownClassBossTests`, fixture unknown-class-boss.png). (2) Bar diamonds may be
any colour (U-Olga: purple, blue, green, yellow, orange-red; read 3 before), with
red hue wrap handled; the rim check still rejects backgrounds. All fixtures and
today's runs re-read correctly.

## Next session intent (user, end of 2026-09-24)

Continue the Avalon le Fae story. The next few fights are heavily scripted and
probably not boss stages, so they test breadth ("how comprehensive the system
is") rather than a clean comparison with the Black Wolf plans. Run each with
plan-ahead, inspection on, and Order Change only when there is a back line.
After each, check which provisional rules fired and whether they helped, and
confirm or drop them. Also check whether plans still keep a defensive answer and
fire offensive NPs promptly with strategy coming only from the four priorities.

## Earlier session intent: a harder stage (user, end of 2026-09-22)

The user plans to challenge a harder stage where NPs matter for efficient clears,
and EXPECTS legacy may win there. That expectation is mechanically well founded
and worth testing rather than assuming: legacy's area-NP gate is
`final_wave or (several enemies and sum(enemy_hp) > 12000)`. On Ember Novice that
sum is about 4,100, so legacy never fired an NP on waves 1-2 and looked wasteful.
Above roughly 12,000 its tuning starts being RIGHT, so legacy should improve
sharply while Codex loses the cheap advantage of skipping pointless work.

So on a harder stage Codex cannot win by spending less. It has to win on decision
quality: buff targeting by `catalog_effective_atk_max`, holding the one-turn
amplifier for the heaviest wave, and reacting when `stage_turn` shows a wave
survived. Those are exactly the parts still verified OFFLINE ONLY, so this is the
held-out test for the wave-per-turn NP guidance.

Watch the 12,000 crossover explicitly when recording results. Use
`--objective survive_and_win` for a story or unknown-difficulty battle;
`farm_fast` trades resources for turns and is wrong when losing is possible.
Harder stages cost 30-40 AP per run, so plan the run count against available AP.

## Order Change work: where it stopped (2026-09-22)

Implementation was STARTED and is at the first blocking step. Nothing was coded
yet, so there is no half-finished state in the runtime.

The blocker is deliberate: the plan requires the observer to recognize the two new
screens FIRST and refuse without them, and template/region detection cannot be
written for screens that have never been captured. Guessing them is the specific
risk the plan warns about, because a half-completed Order Change leaves the battle
on an unrecognized modal screen.

**Resume here.** Capture two frames from a live Ember Novice battle, one tap at a
time with verification between taps:

1. Tactical screen, as the baseline.
2. Master skill palette open. The `Master SKILL` button is near (1210, 310) in the
   normalized 1280x720 frame; this tap is reversible and makes no tactical choice.
3. Whether this account's Mystic Code even has Order Change as its third master
   skill is UNVERIFIED. Confirm it from the palette frame before planning further.
4. The Order Change screen itself, if present, then cancel out.

Then build detection from those real frames, and only then the action kinds,
driver taps, controller confirmation and planner enumeration. Let Codex finish
whatever battle was started so the AP is not wasted.

## Master skills and Order Change: surveyed, not built

`FGO-py/ORDER-CHANGE-AND-MASTER-SKILLS.md` holds the reuse survey and plan.
Upstream `fgoKernel.castMasterSkill` already implements Order Change but drives
keystrokes, not coordinates. FGA's script space is 2560x1440, so its coordinates
halve into our frame; the halved values independently corroborate our existing
skill coordinates. Order Change needs two new observed phases, two new action
kinds, and swap-aware metadata. Until then Oberon cannot be reached from the back
line, and the guide correctly forbids plans that depend on it.

Attack power IS observable in game, contrary to an earlier note: the in-battle
profile popup shows `ATK` and the equipped craft essence (Winter Crystal explains
the 75% starting NP). It needs a new tap/OCR/close interaction per servant, so it
is unused; `effective_atk_max` already ranks buff targets correctly.

## Recommended next action

Implement Order Change and master skills, following
`FGO-py/ORDER-CHANGE-AND-MASTER-SKILLS.md`. The user asked for this directly: many
stages clear with the basic three-turn comp, and they intend to use the agent for
story battles too, but the back-line amplifier (Oberon) is unreachable today, so
the comp is only partly executable. The survey is done and the coordinates are
derived, so the work is mechanical: two observed phases, two action kinds,
swap-aware metadata, offline cases, then one live battle on an authorized stage.

Do the observer phases first and refuse without them, because a half-completed
Order Change leaves the battle on an unrecognized modal screen.

Second priority, needing authorization for a node beyond Ember Novice: one live
`farm_fast` battle where plain face cards CANNOT clear a wave, so the
wave-per-turn NP plan is exercised in game rather than only offline.

## Codex versus legacy, measured 2026-09-22

Full evidence in `FGO-py/CODEX-VS-LEGACY-2026-09-22.md`. On Daily Ember Gathering
Novice, four battles per policy, same party and support:

- Turns 3 versus 3. Three waves means three turns is the floor; both reach it.
- Actions 7.0 versus 13.3 mean. Wall clock 240s versus 337s mean (29% faster).
- Battles completed 4/4 versus 3/4. Model calls 6 per battle, down from 13.
- Legacy deadlocked on one battle, twice, rejecting its own skill because HP and
  readiness OCR flickered on two slots. Codex then won that same battle. That is
  strategy sidestepping the trap, NOT better perception; the instability is unfixed.

Five policy changes produced this, all in the planner and the persisted guide:

1. `quest_context.objective` (`farm_fast` / `survive_and_win`). Guide version 1
   told the model to conserve skills against low-HP enemies, which is every Ember
   wave, so it never used one. That was the real cause of "doesn't select skills".
2. Card permutations (up to 336 opaque ids) are scored with the upstream heuristic
   scorer and trimmed to a ranked shortlist with chain/Brave/affinity/crit and a
   `baseline_choice` marker. Cause of "picks cards at random". Kept ids stay a
   subset of enumerated legal actions.
3. Turn plans: one response may order several allowed action ids. Later entries
   are re-derived from a fresh observation before being sent, and dropped on a
   phase or turn change or any unconfirmed action. `_MAX_PLAN` is 8, so a seven
   skill setup turn plus `attack` fits in ONE model call. `action_id` still valid.
4. Asymmetric cost: an extra turn costs far more than an unneeded skill, and own
   damage is the value the model cannot observe. Without this it under-spent and
   took 4 turns.
5. `signals.stage_turn` is now sent, so a value above 1 is observed proof that a
   plain card turn did not clear this wave.

Also: upstream effect class 0 meant a skill was never offered at all, including
Oberon S3, the one-turn NP amplifier a heavy wave wants. Class 0 means upstream
never classified it, so it is now offered when the target mode is known and
non-special, flagged `unknown_upstream_rely_on_description`. Rare (2 of 1,395
skills) but it blocked exactly the farming skill. `legacy_policy` is untouched.

Offline NP farming: `examples/np-farming-scenarios.json`, planner 7/7 objectives
versus baseline 4/7, including two cases for the real double-Koyanskaya field comp.
On a durable wave 1 it covered 75% to full with the dealer's OWN battery and took
only the two Buster buffs, conserving both Koyanskaya charges for later waves. The policy derived a Buster setup rotation from the cached
English descriptions unprompted, spent nothing on a trivial wave, and in one pass
declined Oberon S3 by reading its Eternal Slumber demerit. Hand-authored
objectives, not proof of good play, and NOT yet seen in a live battle.

Conditional fallbacks now in the guide: check the party's "Increase NP Gauge"
skills can cover one NP per remaining wave BEFORE committing (a support with no
charge skill normally ends the plan); abandon the schedule once `stage_turn` > 1
rather than chasing it; aim selectable buffs at the strongest attacker; prefer
plans that do not hinge on unknown HP or readiness.

Attack power was not observable at all. The catalog now stores `atk_max`,
`hp_max`, `rarity`, `attribute`, `level_max`, plus `class_attack_rate` from the
official NiceClassAttackRate export and a derived `effective_atk_max`; the planner
exposes them. Ranking by raw attack is wrong: Karna has higher `atk_max` than
Arjuna Alter, but Arjuna's Berserker 1.1 rate makes him the real damage dealer.
Re-run `Agent.ps1 catalog --ids ...` for any servant cached before 2026-09-22 to
pick up the new stat fields.

Accounts: the development Codex login is out of credits until 2026-09-28. Use
`--account dedicated` (the dedicated account, Plus). The GUI now has an
Objective selector defaulting to farming, and passes `--objective`.

## English GUI and account milestone

- Account UI is simplified to Development / Other Account and Login. Extra
  device/status/logout buttons and isolation explanation are removed. A persistent
  email/plan line reads the selected account via official app-server account/read
  at startup, selection change and after login. Underlying isolation is unchanged.
- User's Observe run `20260922T013732Z-d04808d1` completed: wave 1/turn 1,
  tactical phase, three allies and enemies, errors empty. Both local auth modes
  subsequently reported signed in. No model inference or game inputs used to
  verify account status. Observe completion now remains in the GUI activity log.
- Real GUI follow-up queried both saved accounts: email and plan displayed for
  both, different emails confirmed, identity not appended to logs. Evidence:
  `FGO-py/agent-runs/gui-identity-qa.json`. No game input/model calls. The user's
  Other Account sign-in is saved; a new sign-in is not needed just to test it.

- Browser login silently puts the complete Codex authorization URL on the
  clipboard for pasting into a private browser. No copy notification is shown.
  Reopen the GUI after this change; existing batch launchers use the updated code.

- Root `FGO-Agent.bat` and `GUI-English.bat` now launch our agent GUI. The repo's
  `GUI-Legacy-English.ps1` preserves deliberate access to the original UI.
- GUI defaults to Codex AI, Sol/low; it supports device refresh, read-only
  Observe, one battle, Stop, and opening run evidence. It does not start quests
  or refill AP. Every GUI child runs in an owned Windows kill-on-close Job Object.
- Select current development login or dedicated app login. Dedicated browser/
  device login, status and logout use `%LOCALAPPDATA%/FGO-Codex/codex-home` with
  file credential storage. Ambient auth overrides are removed from child envs.
  No credentials are copied and development login changes are disabled in GUI.
- Both FGA and original FGO-py were checked: `GUI-SOURCE-NOTES.md` and
  `PRIORITY-LABELS.md` record reuse. Small FGA Danger/Servant label hints are
  implemented; four synthetic tests passed, real boss validation remains pending.
- 127 full-suite tests passed, followed by 18 focused tests after the final
  auth-output repair. Real development-account replay passed; logged-out
  dedicated-account replay failed as expected without falling back.
- GUI MuMu/device/account/Observe checks passed. Terminal-screen Start smoke
  produced zero inputs and model calls: not a new battle victory. No AP spent.
  Native Windows English layout inspected. The later account identity follow-up
  confirmed a different saved account; a GUI-driven active battle is still
  unverified. Account/GUI/Windows worker follow-up passed 23 focused tests.
- See `FGO-py/GUI-AGENT.md` for use and `FGO-py/GUI-VALIDATION.md` for evidence.

## Commands (from development repo)

```powershell
.\Agent.ps1 observe --serial 127.0.0.1:16384 --cooldown-ocr
.\Agent.ps1 replay --suite examples\strategy-scenarios.json
.\Agent.ps1 battle --serial 127.0.0.1:16384 --policy codex --objective farm_fast --account dedicated
.\Agent.ps1 replay --suite examples
p-farming-scenarios.json --objective farm_fast --account dedicated
& C:\Users\micha\Downloads\FGO-py.portable\Python311\python.exe -m unittest discover -s tests -p 'test_agent_*.py'
```

Next development should follow the final validation report, distinguish decision
quality from perception/control failures, and retain unknowns. Do not restart
architecture discovery or repeat successful tests without a concrete reason.
