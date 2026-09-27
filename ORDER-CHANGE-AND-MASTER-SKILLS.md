# Master skills and Order Change: reuse survey and plan

Requested so the basic three-turn comp can clear stages where the back-line
servant (typically Oberon) must reach the field, and so story battles can use
master skills. **Order Change is now implemented and validated live; see the last
section.** The sections below record the original survey, which says what both codebases
already solve, so the work is mechanical rather than exploratory, per the
project rule to inspect FGA and upstream FGO-py before adding a feature.

## Why it is currently impossible

The agent's action set is `skill`, `skill_target`, `enemy_target`, `attack`,
`cards`. There is no master-skill action and no Order Change, so a plan can never
bring a back-line servant forward. The strategy guide therefore tells the model
explicitly not to build plans that depend on it, which is honest but limiting:
Oberon's one-turn NP amplifier is unreachable unless he starts on the field.

## Upstream FGO-py already implements it

`FGO-py/fgoKernel.py:240` `castMasterSkill` handles all three master skills
including Order Change, and `fgoKernel.py:195` carries the `masterSkill`
configuration table. Order Change swaps entries in `self.orderChange`, which
`getSkillInfo` and `getHouguInfo` then use to keep skill and NP metadata pointing
at the right servant after a swap — a detail the agent would need to copy, since
its own `servant_metadata` is keyed by observed servant id per slot.

Upstream drives input as **keystrokes** (`fgoDevice.device.perform` with
`'TYUIOP'`, `'Z'`, `'\xBB'`), not coordinates. Our driver taps coordinates, so the
key mapping is not directly reusable; the sequencing logic is.

## FGA supplies the coordinates

FGA's script space is 2560x1440 (`FgoGameAreaManager.kt:65-66`, images 1280x720),
so **every FGA location halves into our normalized 1280x720 frame**. Its own
`xFromCenter()` offsets are relative to x=640 in our space.

Cross-checking FGA against coordinates we already use and have confirmed live,
which is independent corroboration of the existing driver:

| Control | FGA (2560x1440) | Halved | Ours (`driver.py`) |
|---|---|---|---|
| Servant A skill 1 | 148, 1158 | 74, 579 | 73, 573 |
| Servant B skill 2 | 960, 1158 | 480, 579 | 483, 573 |
| Servant C skill 3 | 1770, 1158 | 885, 579 | 891, 573 |

New coordinates this feature needs, from `BattleScreenLocations.kt` and
`MasterLocations.kt`, halved into our frame:

| Control | Halved coordinate |
|---|---|
| Order Change, starting A / B / C | (140, 350) / (340, 350) / (540, 350) |
| Order Change, sub A / B / C | (740, 350) / (940, 350) / (1140, 350) |
| Order Change confirm | (640, 630) |
| Master skill open | approx (1131, 320) |
| Master skill A / B / C | approx (850, 310) / (940, 310) / (1020, 310) |

The master-skill coordinates are **approximate on purpose**. FGA does not hardcode
them: it template-matches the Battle Menu image and works from that offset,
falling back to `Location(-298, 0).xFromRight()`, because that row is right-aligned
differently between UI versions. Verify against our own screenshots, or match the
button, before sending input.

## Plan

1. **Observer**: recognize two new phases, `master_skill_open` and
   `order_change`, plus which six portraits the Order Change screen shows. Without
   a verified phase the controller must refuse, exactly as it does for the skill
   target dialog today.
2. **Models**: add action kinds `master_skill` (with a target where the skill takes
   one) and `order_change` (a field slot and a sub slot).
3. **Driver**: tap the coordinates above, gated on the observed phase, reusing the
   existing pattern where `can_target` refuses an ambiguous dialog.
4. **Controller**: confirm a master skill by its own cooldown appearing, the same
   two-consecutive-frames rule used for servant skills. Confirm an Order Change by
   the field slot's observed servant id changing to the expected one — strong,
   cheap evidence the existing observation already provides.
5. **Planner**: enumerate these only when the phase and the six slots are verified.
   After a confirmed swap, the metadata for that slot must follow the new servant;
   `_clear_replaced_actors` already handles a slot's id changing, so it should
   extend naturally.
6. **Guide**: replace the "never plan an Order Change" paragraph with when it is
   worth one, namely bringing a damage amplifier in for the heaviest wave.
7. **Evidence**: offline replay cases first, then one live battle on an authorized
   stage, with the usual screenshot-hash audit.

## Risks

- Order Change is a **three-tap sequence on a modal screen** (field slot, sub slot,
  confirm). A half-completed swap leaves the battle on a screen no current phase
  recognizes. The controller must treat an unconfirmed swap as `uncertain` and stop
  rather than guessing, and the runner already stops on any non-confirmed action.
- Master skills are per-battle, not per-wave, and are easy to waste. Their
  cooldowns are not currently observed at all.
- Command Spells appear next to master skills in FGA's layout. They cost quartz in
  some contexts and must stay out of the action set.

## Implemented and validated live (2026-09-22, evening)

Status changed from "surveyed, not built" to **built and working live**.

Capture (`agent-runs/order-change-capture`, fixtures in `tests/fixtures/order_change`):
the user's Mystic Code is the Chaldea Combat Uniform; Order Change is master skill
3 with a 13-turn cooldown. Closing the member screen with its X leaves Order Change
UNSPENT and returns to the tactical screen with the palette still open. Neither
upstream nor FGA recognizes these screens; both tap on fixed delays.

Built: `fgo_agent/order_change.py` (templates cut from the real frames, member-name
OCR, selection markers, Replace state, palette ready/used), observer phase
`order_change` recognized before the unrecognized-modal refusal, `master_palette`
signal on tactical frames, driver taps gated on the observed screen, a verified
controller sequence (back out through the X before Replace on any mismatch; after
Replace, confirm only when the field slot's servant id changes in two consecutive
frames), a one-time back-line reconnaissance per battle that leaves the skill
unspent, planner actions `oc-fF-bB` that must end their plan, `--order-change`
(opt-in), a cached NA name index (`servant_index.json`) for name-to-id resolution
that refuses ambiguous names, and guide text for when a swap is worth it.

Live evidence on Rider/Saber Extreme (Archer/Caster had rotated out):

- `20260922T231252Z-74a4c790`: reconnaissance read the back line and left Order
  Change unspent; waves 1 and 2 each fell in one turn; on wave 3 the policy chose
  the swap, but the controller refused because OCR read "Oberon+++" and cancelled
  UNSPENT. Correct safety, false refusal. Fixed: names are stripped of edge
  punctuation and matched with a 0.9 similarity floor (it still rejects
  "Koyanskaya of Light" vs "of Dark").
- `20260922T231252Z` + continuation `20260922T232303Z-231b8d95`: **swap confirmed
  live**, `field slot 1 servant changed 314 -> 316 in two consecutive tactical
  frames`. Victory; wave 3 took three turns.

Why wave 3 took three turns: after the swap the dealer was at 0%; Oberon S1 (+20%
party) and S2 (+50%) reached only 70%. The policy then also spent S3 on the dealer.
S2's own text says its gauge decreases at end of turn, and S3 is a one-turn
amplifier ending in "removes Buffs & Eternal Slumber" on its target, so both were
wasted and the dealer's buffs were stripped (70% -> 64%). The guide now has a
general rule, with a replay case built from this exact state: when the NP cannot
fire this turn, spend lasting charges and build gauge with the dealer's cards, and
hold expiring charges, one-turn amplifiers and delayed-penalty skills for the NP
turn. The replay now plans only `['s1-1-t2', 'attack']`: the lasting party charge.

Charge budget with the 2/6/4 support stays short of three NPs even with Oberon;
a 10/10/10 support Koyanskaya adds about +18% and should make wave 3 reachable.

## Clean three-NP clear (2026-09-22, late evening)

`20260922T233701Z-ac378eda`: Rider/Saber Extreme, **uninterrupted**, victory in
**3 turns with three NPs**, 20 actions, 9 model calls (11 actions served from
plans), 616s. Support was a **10/10/10** Koyanskaya of Light (Mokasoon), not the
2/6/4 support of the earlier runs; party otherwise identical, Oberon in the back.

| Wave | Policy's turn | Dealer NP |
|---|---|---|
| 1 | dealer self-charge (75 -> 100), his ATK buff, both Koyanskaya Buster buffs, NP | 100 |
| 2 | Koyanskaya A charge (+50), Koyanskaya B charge (+50), dealer ATK buff freed by the two cooldown cuts, both Koyanskaya S2, NP | 100 |
| 3 | dealer self-charge freed again by the cuts (+30), Order Change slot 2 <-> Oberon (`314 -> 316` confirmed in two frames), Oberon S1 (+20), S2 (+50), S3 amplifier ON the NP turn, NP | 100 |

This is the standard double-Koyanskaya + Oberon three-turn rotation, reached from
the persisted guide and the fetched skill text, with every step confirmed. It also
validates the hold-turn-limited-skills rule live: Oberon's expiring charge and his
delayed-penalty amplifier were spent on the turn the NP fired.

Not yet like-for-like: the legacy Extreme runs (7 and 8 turns) used the 2/6/4
support. A legacy run with a 10/10/10 support is still needed before claiming
"Codex beats legacy" for this exact setup.
