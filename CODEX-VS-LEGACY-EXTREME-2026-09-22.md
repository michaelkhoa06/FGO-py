# Codex versus legacy on Daily Ember Gathering Extreme — 2026-09-22

The harder-stage test planned at the end of the Novice comparison. User-directed
stage; user authorized up to three golden apples for AP (never saint quartz). All
in-battle decisions came from a policy; the operator only navigated menus.

## Conditions

- Quest: Daily Ember Gathering `<Archer/Caster>` Extreme, Lv.60, 40 AP, 3 waves.
  Waves observed at roughly 83k-88k, 121k and 138k total enemy HP — far above
  legacy's 12,000 area-NP threshold, so every wave genuinely needs an NP or heavy
  buffing.
- Party (user-built): support Koyanskaya of Light from MXP04 (skills **2/6/4**,
  so a weak level-2 battery), own Koyanskaya of Light (10/5/10), Arjuna Alter
  (10/10/2, Winter Crystal for 75% starting NP). Oberon 10/10/10 in the back line,
  unreachable because Order Change is not in the action set.
- The support list REORDERS between visits. The MXP04 Koyanskaya was selected by
  verifying the row each time, never by a blind first-row tap.
- Codex: `gpt-5.6-sol`, low reasoning, `--objective farm_fast`, dedicated account.

## Results

| Run | Policy | Outcome | Turns | Actions | Model calls | Wall |
|---|---|---|---|---|---|---|
| `20260922T213146Z-a01b21ba` | legacy | victory | **8** | 32 | 0 | 820s |
| `20260922T214820Z-9487d46b` | codex | victory | **3** | 16 | 8 | 476s |
| `20260922T215857Z-33185da4` | legacy | victory | **7** | 29 | 0 | 706s |
| `20260922T221234Z-76a67291` + `20260922T221927Z-4e3d969f` | codex, **assisted** | victory | **6** | 8 + 15 | 5 + 10 | 300s + 459s |

Legacy fired all nine available skills on turn 1, cleared wave 1 with the NP, then
had no charge left and ground wave 2 with face cards for five turns (turns 2-6).
It has no concept of budgeting batteries across waves.

Codex followed the written three-turn procedure step by step:

| Wave | Enemy HP | Planned |
|---|---|---|
| 1 | 88,050 | dealer's OWN charge (75 to 100) + his ATK + both Koyanskaya Buster buffs, attack. Both batteries kept. |
| 2 | 121,706 | `s1-1-t3` alone, plan ended (to 32%); `s2-1-t3` alone, plan ended (to 82%); the two cooldown cuts brought the dealer's own skills back; his ATK + self-charge, attack, NP. |
| 3 | 137,991 | no battery left; Koyanskaya S2s on the dealer; buffed Buster face cards cleared it. |

The "end the plan after a cooldown-reducing battery" rule fired twice on wave 2,
which is exactly how one battery becomes two. Wave 3 had no NP because the level-2
support battery charged only +32% (the own level-10 copy gave +50%), leaving the
budget one charge short; a maxed support or Oberon on the field would supply it.

## Why wave 3 decides the turn count with this party

Codex's second run matched the first move for move through wave 2, then a model
call stalled at `turn.started` for the full 60s timeout (normal calls take 7-21s).
The runner stopped without input; the battle was resumed in a new process. Wave 3
was a single 137,991 HP enemy, and by then no battery remained on the field, so it
took four face-card turns. The same wave died in one face-card turn in run 1.

Charges measured in these runs:

| Source | Charge |
|---|---|
| Winter Crystal starting gauge | 75% |
| Arjuna Alter S2, turn 1 | at least +25% |
| Support Koyanskaya S1, skill level 2 | +32% |
| Own Koyanskaya S1, skill level 10 | +50% |
| Arjuna Alter S2 again, freed by two cooldown cuts | about +30% |

Roughly 210-240% against the 300% three NPs need. Both Codex runs spent it exactly
as the procedure prescribes for waves 1 and 2. Nothing on the field can charge wave
3; in this comp that is Oberon's job, and he sits in the back line. **With this
party, a true three-NP clear needs Order Change** (or a charge source on the
field). A maxed support narrows the gap but does not close it. Until then wave 3 is
decided by face-card luck, so further runs of this exact party mostly measure luck.

Fix made from this run: the battle runner now retries a failed decision once from a
FRESH observation (`policy_retries=1`), never sending input in between. A single
stalled model call no longer ends a battle.

## Earlier assisted run

`20260922T211159Z-d5d04ec1` + continuation `20260922T212207Z-4957b580` also
cleared in 3 turns, but the first process halted on a confirmation bug (below),
so it is an assisted continuation, not an uninterrupted clear.

## Fixes made during this session

1. **Confirmation of a cast whose cooldown numeral is unreadable.** A targeted
   battery landed (target NP 0 to 32, target cooldowns cut by 2) but the used
   skill's cooldown read as 0 — a two-digit "10" losing its leading digit in the
   26px crop. The controller only confirmed on a readable increased cooldown, so it
   timed out and halted the battle. The observer now reports an impossible 0 on a
   dark icon as unreadable, and the controller confirms consumption from stable,
   same-turn darkness: two frames when corroborated (target dialog completed, or an
   ally's NP/HP rose), three frames when merely specific (another previously ready
   skill stayed lit, so the screen was not globally dimmed). Accepting darkness can
   at worst skip a re-tap; missing it halts the battle.
2. **A written three-turn procedure** in PLAYBOOK.md (user-requested): role
   assignment (dealer, batteries, amplifiers, lasting buffs), a battery budget
   across waves, per-turn steps, the end-plan-early rule, card-phase order, and
   explicit abandon conditions. The guide grew past the 16,000-character guard,
   which would have failed every request; the guard is now 20,000 and a test loads
   the shipped guide.

## Limits

- One clean run per policy so far on this quest. Not a win rate.
- Legacy's 8 turns may partly reflect card-draw luck on wave 2; its turn-1
  over-spend is structural and visible in the trace regardless.
- Apples used: see PROJECT-STATE.md for the running count.
