# Codex versus legacy on Daily Ember Gathering Novice — 2026-09-22

Head-to-head measurement on one repeatable quest, after three changes to the AI
policy. User authorized unrestricted MuMu use for this stage only. All in-battle
decisions were made by a policy; the operator only navigated menus (open quest,
pick support, Start/Repeat), because the battle runner deliberately never starts
a quest.

## Conditions held constant

- Quest: Daily Ember Gathering `<Archer/Caster>` Novice, 10 AP, 3 waves.
- Party every run: support Karna (Lancer Lv.90), Koyanskaya of Light (Assassin
  Lv.90), Arjuna Alter (Berserker Lv.90). Started via the in-game Repeat dialog,
  which reuses the same party, then the first listed support.
- Waves 1 and 2 are three enemies at 1,372 HP each. Wave 3 is heavier and varies
  (observed up to 4,390 + 1,372 + 2,548 = 8,310 HP).
- Enemy classes vary between runs; card draws vary. This is inherent quest RNG.
- Codex: `gpt-5.6-sol`, low reasoning, `--objective farm_fast`, dedicated
  account (the dedicated account, Plus). Legacy is unchanged upstream code.
- A battle cannot end in fewer turns than it has waves, so **3 turns is the floor**.

## What changed in the policy

1. **Objective context.** The request now carries `quest_context.objective`
   (`farm_fast` / `survive_and_win`), and the guide reasons about it. Version 1
   of the guide told the model to conserve skills against low-HP enemies, which
   is true of every Ember wave, so the model correctly never used one. That was
   the direct cause of the reported "doesn't select skills".
2. **Legacy-scored card candidates.** The card phase enumerated up to 336 opaque
   permutations. They are now scored with the upstream heuristic scorer and
   trimmed to a ranked shortlist carrying score, colour chain, Brave chain,
   affinity, critical rate, and a `baseline_choice` marker. Every kept id is
   still an action the observation enumerated, so legality is unchanged. This
   gives the model the chain and position arithmetic it previously had to guess,
   which was the cause of the reported "picks cards at random".
3. **Turn plans.** A tactical response may return an ordered `plan` of allowed
   action ids for the turn. Later entries are served without another model call,
   but only after being re-derived from a fresh observation's allowed set, and
   the queue is dropped on a phase change, a turn change, or any unconfirmed
   action. `action_id` responses remain valid.

Two further corrections came out of the live runs themselves:

4. **Asymmetric cost.** The first `farm_fast` run under-spent and took 4 turns,
   because the guide let the model treat "plain cards will clear this" as
   reliable. The guide now states that an extra turn costs far more than an
   unnecessary skill activation, that own-damage is the value it cannot observe,
   and that close calls should be resolved by buffing rather than hoping.
5. **Wave feedback.** `signals.stage_turn` (turns already spent on this wave) was
   computed by the driver but withheld from the request. It is now a salient
   signal, so a value above 1 is observed proof that a plain card turn did not
   clear the wave. Absent values are still absent, not invented.

## Results

Legacy, unchanged, 4 distinct battles:

| Run | Outcome | Turns | Actions | Skills | Wall |
|---|---|---|---|---|---|
| `20260922T020458Z-1bb97588` | victory | 3 | 12 | 6 | 305s |
| `20260922T023821Z-c7152b1b` + `20260922T024337Z-ed41b42a` | **failed**, twice | — | 3 | 3 | — |
| `20260922T025121Z-90aa1ce7` | victory | 3 | 15 | 9 | 383s |
| `20260922T030504Z-0aa1dcd5` | victory | 3 | 13 | 7 | 324s |

Codex with all five changes, 4 battles:

| Run | Outcome | Turns | Actions | Skills | Model calls | Wall |
|---|---|---|---|---|---|---|
| `20260922T023244Z-ab2907fe` | victory | 3 | 6 | 0 | 6 | 210s |
| `20260922T024523Z-87dbbbe7` | victory | 3 | 7 | 1 | 6 | 256s |
| `20260922T025918Z-77b46de1` | victory | 3 | 7 | 1 | 6 | 250s |
| `20260922T031200Z-15659b3b` | victory | 3 | 8 | 2 | 6 | 260s |

Comparing only clean full battles (Codex 210/250/260s, legacy 305/383/324s):

- **Turns: 3 versus 3.** Both reach the floor. Codex does not beat it and cannot.
- **Actions: 7.0 versus 13.3 mean.** 47% fewer device interactions.
- **Wall clock: 240s versus 337s mean.** 29% faster despite six model calls.
- **Completion: 4/4 versus 3/4 battles.**
- **Resources: 0-2 skills versus 6-9.** Codex spends a charge skill on the final
  wave to enable an area NP, and otherwise holds cooldowns and gauge.

## The reliability finding

Legacy could not finish the battle at `20260922T023821Z`, and failed again on
retry with the same message: `action was rejected: skill is not observably ready`.
The first tactical observation of that battle shows why — HP and skill readiness
OCR flickered for two of three slots:

```
slot 1 id 85  hp None  np 0  [(1,None,None),(2,None,None),(3,None,None)]
slot 2 id 314 hp 13081 np 50 [(1,False,6),(2,False,8),(3,True,None)]
slot 3 id 247 hp None  np 75 [(1,None,None),(2,None,None),(3,None,None)]
```

Legacy proposes a skill on a frame where a slot looks ready, the controller
re-observes before sending, readiness is gone, and the runner stops on the
non-confirmed action. It repeats that forever, spending skills each attempt.
Codex then won the same battle (`20260922T024523Z-87dbbbe7`) in 3 turns.

This is not better perception. Codex avoided the trap because on trivial waves it
does not need the flickering skill at all, so it never proposes it. The
underlying readiness instability is unfixed and would still affect Codex on a
battle where it does want that skill.

## Limits

- Four battles per policy on ONE quest with a fixed party. Not a win rate, and
  not evidence about story or challenge content.
- Card draws and enemy classes varied between runs; turn counts did not.
- The 3-turn floor means wall clock and action count are the only headroom here.
  A quest where legacy cannot one-turn a wave has not been tested.
- `20260922T022359Z-e1962f7a` (4 turns, 8 actions) predates corrections 4 and 5
  and is retained as the evidence that motivated them.
- `20260922T021401Z-333c15f1` stopped because the **development** Codex account
  hit its usage limit until 2026-09-28; it is not a code fault. Use
  `--account dedicated`.
- `20260922T021744Z-5efe74b1` stopped with `unknown observation limit reached`:
  resuming a fresh process directly onto a card screen can leave the turn counter
  unreadable behind an NP portrait, and a fresh process has no earlier tactical
  frame to carry the turn from. Unchanged known limitation.

## Reproducing

```powershell
.\Agent.ps1 battle --serial 127.0.0.1:16384 --policy codex --objective farm_fast --account dedicated
.\Agent.ps1 battle --serial 127.0.0.1:16384 --policy legacy
```

`agent-runs/ab_loop.sh <legacy|codex>` advances the result screens, repeats the
quest, picks the first support and runs one battle under that policy.

## Wave-per-turn NP farming (added after the Ember runs)

Ember Novice cannot demonstrate this: its waves die to face cards, so the correct
play there is to spend nothing. Real farming nodes are the opposite, so the guide
gained a `Wave-per-turn NP farming` section teaching the executable pattern —
charge and buff the servant whose AREA NP is meant to clear this wave, in the same
tactical phase, then fire it from the card phase.

Deliberately general, with servant specifics left in the catalog:

- Gauge is never computed. An NP card is selectable only when the game marks it
  ready, so the rule is "charge until it is offered".
- Charge and buff magnitudes are absent from the English text. They stay unknown
  quantities; the observed `np` on the next confirmed frame is the evidence.
- "Reduce skill cooldown" is named as the mechanism that lets one battery charge
  again on a later wave.
- Buster NPs are called out as refunding little gauge, so a second NP needs
  another charge rather than an assumption.
- **Order Change, master skills and command spells are not in the action set**, so
  the guide forbids plans that depend on swapping in a back-line servant. Comps
  that rely on an Order Change into Oberon are therefore only partly reachable.

### Upstream gap found and fixed: effect class 0

`_known_effect` required an upstream effect class of 1-9. Oberon's S3 — "Increase
Buster Card effectiveness (1 turn) & boost NP Strength Up (1 turn)", the one-turn
amplifier a heavy final wave wants — is **class 0**, so the planner never offered
it, and `legacy_policy` skips class 0 as well. Class 0 means upstream never
classified the semantics, not that the skill is unusable.

Class 0 is rare: 2 of 1,395 classified skills across 465 servants, both with
ordinary target modes. The planner now accepts class 0 when the target mode is
known and non-special, and labels it
`effect_class_semantics: unknown_upstream_rely_on_description` so the model uses
the English text rather than a missing category. Special modes 6/7/8 and malformed
tuples stay excluded. `legacy_policy` is untouched, so the baseline is unchanged.

### Offline result

`examples/np-farming-scenarios.json` (built by `build_np_farming_scenarios.py`)
holds four hand-authored cases using Koyanskaya of Light (314), Oberon (316) and
Arjuna Alter (247), all AREA Buster NPs. **Planner 4/4 objectives, baseline 2/4.**

The policy derived the rotation from the cached descriptions without being told it:

| Case | Planned turn |
|---|---|
| Durable 3x30,000 first wave | `s3-1-t3, s3-2-t3, s1-1-t3, s2-1-t2, attack` |
| Trivial 3x1,372 wave | `attack` |
| Final wave, dealer already at 100% | `s1-3-t3, s2-3-t3, attack` |
| Wave already survived a turn (`stage_turn` 2) | 7 skills then `attack`, one call |

Notes on those choices:

- The durable-wave plan is a textbook Buster setup: the dealer's own battery and
  ATK buff, then Koyanskaya's charge and cooldown reduction aimed at the dealer,
  then Oberon's party charge and NP-strength buff, then advance.
- The final-wave plan spends both one-turn amplifiers on the full-gauge dealer,
  including the newly reachable Oberon S3.
- On the trivial wave the same party correctly spends nothing, so the farming
  guidance did not turn into blanket over-spending.
- In an earlier pass the model *declined* Oberon S3, reasoning from its demerit
  text that Eternal Slumber "could disable the intended wave-3 attacker". It is
  reading the drawbacks, not only the buffs.
- `_MAX_PLAN` was 6, which truncated the terminal `attack` off a full setup turn.
  It is now 8, so seven skills plus `attack` fit in a single model call.

### Still unproven

NP farming is verified **offline only**. Hand-authored objectives are not proof of
good play, and no live battle has exercised it, because a node where plain cards
fail is outside the authorized Ember Novice stage. Whether the rotation actually
one-turns a durable wave in game is untested.

## Conditional fallbacks and buff targeting (user-requested)

Three conditional rules were requested, plus one found while implementing them.
All are written as general conditions, with servant facts left in the catalog.

1. **The NP plan is checked for feasibility before it is committed to.** The real
   condition is not a named support but whether the party's "Increase NP Gauge"
   skills can plausibly cover one NP per remaining wave. A borrowed support with
   no charge skill leaves you a charger short, which is the common case where a
   wave-per-turn clear becomes unreachable; the guide says to name that in
   `reason` and drop to ordinary judgement rather than half-committing.
2. **An already-failed plan is abandoned, not chased.** `stage_turn` above 1 means
   the one-turn-per-wave schedule is gone for this battle, so nothing should be
   reserved for it any more: end the current wave, keep enough defence, finish.
3. **Selectable buffs and charges go to the strongest attacker**, because a damage
   buff multiplies the holder's own attack. This needed new data — see below.
4. **Prefer plans that do not hinge on unknown values.** Added from today's legacy
   deadlock: when HP or readiness reads unknown, perception is unstable, and an
   action whose precondition is invisible may be rejected before it is sent.

### Attack power was not observable at all

Rule 3 was unactionable: an observation carries `hp`, `np` and skill readiness,
and **no attack value**. The catalog now fetches `atkMax`, `hpMax`, `rarity`,
`attribute` and `lvMax` from the same Atlas record, and the official
`NiceClassAttackRate` export, storing `class_attack_rate` and a derived
`effective_atk_max`. The planner exposes `catalog_effective_atk_max`,
`catalog_atk_max`, `catalog_class_attack_rate` and `catalog_hp_max`.

Raw attack ranks this comp wrongly, which is why the derived value matters:

| Servant | Class | atk_max | rate | effective |
|---|---|---|---|---|
| Arjuna (Alter) | berserker | 11,669 | 1.1 | **12,836** |
| Karna | lancer | 11,976 | 1.05 | 12,575 |
| Oberon | pretender | 11,810 | 1.0 | 11,810 |
| Koyanskaya of Dark | foreigner | 11,220 | 1.0 | 11,220 |
| Koyanskaya of Light | assassin | 11,616 | 0.9 | 10,454 |

Karna has the highest raw attack but Arjuna Alter hits hardest, so the guide
forbids ranking by `atk_max` alone. These are max-level base stats for ranking,
never account values: level, Fou and Craft Essence stay unknown. `hp_max` also
gives the first real reference for judging whether an observed HP is low —
Arjuna's catalog `hp_max` of 13,837 matched the in-game HP exactly.

### Offline result

The suite is now 5 cases: **planner 5/5, baseline 3/5**. The added case puts the
damage dealer in slot 1, so buffing by slot order fails it. The planner aimed both
Koyanskaya's charge and her Buster buff at slot 1:
`['s1-1-t1', 's1-2-t1', 's2-1-t1', 's2-3-t1', 's3-1-t2', 'attack']`.

Rule 1 is deliberately **not** covered by an offline objective. "Fall back to best
judgement" has no single correct action to assert, and inventing one would test a
preference rather than a behaviour. It rests on the guide text only.

### Gaps that remain invisible to the agent

- **Craft Essence and Append skills.** Starting NP charge from a charge CE, and
  Append 2's extra charge, are what make many real 3-turn rotations work. Neither
  appears in an observation, so the agent cannot account for them.
- **Order Change and master skills.** Not in the action set, so comps that swap a
  back-line servant in for the final wave are only partly reachable.
- **Enemy class.** Only per-card affinity is observed, not each enemy's class, so
  "which of my servants is advantaged against this wave" is inferred indirectly.

## Double Koyanskaya comp, and the readiness deadlock fixed

### Attack power is observable in game after all

An earlier note here said attack power was not observable. That was wrong: the
in-battle servant profile popup shows it directly (`ATK 13,235` for Arjuna Alter),
and the same popup shows the equipped craft essence, which is where the observed
75% starting NP comes from — Winter Crystal, "Begin battle with your NP charged at
75%". The catalog's max-level base of 11,669 is *lower* than the real value
because level, Fou and the CE are not in it, so the in-game number is strictly
better where exactness matters.

It is still not in an observation, because reading it needs a new screen
interaction: tap the portrait, OCR the popup, close it, per servant. For *ranking*
buff targets the catalog's `effective_atk_max` already orders this comp correctly,
so the popup is only worth wiring up if damage estimation needs the true number.

### The readiness deadlock, fixed in the controller

`_validate` refused a skill whenever readiness was `is not True`, which lumped
**unreadable** together with **observed cooldown**. That is the same category
error this project already fixed for HP, and it is what made legacy fail the same
battle twice in a row on 2026-09-22.

The controller now separates them. An observed `False` refuses immediately and
sends nothing. An unreadable value is re-read up to three times, inside the same
deadline as every other input, and refuses with its own distinct reason
(`skill readiness is unobserved`) if it never resolves. A context change during the
re-read also refuses. Three tests cover the reappearing, observed-false and
permanently-unreadable cases.

This lives in the controller, so **it repairs the baseline too**, not only the AI
policy. The underlying icon-brightness heuristic is still unchanged.

### Live run with the user's double-Koyanskaya comp

`20260922T035240Z-c0a3648a`: victory, 3 turns, 11 actions, 6 model calls, 351s,
with Koyanskaya of Light in slots 1 and 2 and Arjuna Alter in slot 3 at 75% NP.
A duplicate-servant field also exercises the card-ownership ambiguity path.

On the final wave it planned a six-action turn in ONE model call — five skills
(Arjuna's own battery, a Koyanskaya battery, then attack and Buster stacking)
followed by `attack` — and then fired the area NP as `[8, 5, 2]`.
`actions_served_from_plan` was 5.

Wall clock was 351s against the 240s mean of the earlier runs, because those five
skill animations cost real time and, on a wave this weak, were probably
unnecessary. That is the asymmetric-cost guidance behaving as designed: it prefers
over-spending when it cannot predict its own damage. It reached the turn floor and
won, but this is the cost side of that trade and is worth revisiting if wall clock
matters more than turn safety.

### Offline rotation check, now 7 cases

Two cases model the real field comp. **Planner 7/7 objectives, baseline 4/7.**

| Case | Planned turn |
|---|---|
| Double battery, durable wave 1, dealer at 75% | `s3-1-t3, s3-2-t3, s1-3-t3, s2-3-t3, attack` |
| Wave 2, dealer at 0%, one battery on cooldown | `s2-1-t3, s2-3-t3, attack` |

The wave-1 plan is the notable one: it used Arjuna's own battery to cover 75% to
full and took only the two Buster buffs, **conserving both Koyanskaya charges** for
later waves rather than dumping them. The wave-2 plan then spends the untouched
battery on the dealer and leaves the cooldowned one alone. That is resource-aware
looping rather than firing everything available.

Still offline. Oberon remains unreachable from the back line until Order Change
exists; see `ORDER-CHANGE-AND-MASTER-SKILLS.md` for the survey and plan.
