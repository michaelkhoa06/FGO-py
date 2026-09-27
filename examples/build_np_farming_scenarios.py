"""Build wave-per-turn NP farming scenarios for the replay harness.

Synthetic observations with hand-authored objectives.  They test whether the
policy reaches the standard farming plan from the cached English skill text:
charge and buff the servant whose AREA NP is meant to clear this wave, then
advance to the card phase.  They are not evidence about win rates.

Party used throughout, all with AREA Buster NPs:
  slot 1  314  Koyanskaya of Light  S1 charge + cooldown reduction, S3 Buster up
  slot 2  316  Oberon               S1/S2 charge, S3 one-turn NP damage amplifier
  slot 3  247  Arjuna (Alter)       the damage dealer, S2 self charge
"""
import json
from pathlib import Path

KOYANSKAYA_LIGHT, OBERON, ARJUNA_ALTER = 314, 316, 247


def skills(*ready_slots, cooldown=5):
    return [{'slot': slot, 'ready': slot in ready_slots,
             'cooldown': None if slot in ready_slots else cooldown}
            for slot in (1, 2, 3)]


def ally(slot, servant, np_gauge, ready_slots, hp=12000):
    return {'slot': slot, 'id': servant, 'hp': hp, 'np': np_gauge,
            'skills': skills(*ready_slots)}


def enemies(*hps):
    return [{'slot': index, 'hp': hp, 'charge': [0, 5], 'priority_label': None}
            for index, hp in enumerate(hps, start=1)]


def observation(name, wave, turn, allies, enemy_hps, stage_turn=1):
    return {'id': name, 'phase': 'tactical', 'captured_at': 'synthetic',
            'allies': allies, 'enemies': enemies(*enemy_hps), 'cards': [],
            'wave': wave, 'turn': turn, 'target_count': None, 'errors': [],
            'signals': {'stage_total': 3, 'stage_turn': stage_turn,
                        'enemy_layout': 'standard3', 'enemy_target_verifiable': True,
                        'selected_enemy': 1}}


FULL_PARTY = [ally(1, KOYANSKAYA_LIGHT, 0, (1, 2, 3)),
              ally(2, OBERON, 0, (1, 2, 3)),
              ally(3, ARJUNA_ALTER, 0, (1, 2, 3))]

CASES = [
    {
        'name': 'np-farm-durable-first-wave-needs-setup',
        'source': 'synthetic_with_cached_NA_servant_descriptions',
        'objective': {'fields': {'kind': 'skill'}},
        'objective_rationale': (
            'Three 30,000 HP enemies will not fall to face cards, so the turn must begin by '
            'charging or buffing the area NP rather than advancing straight to cards.'),
        'observation': observation('np-farm-durable-first-wave-needs-setup', 1, 1,
                                   FULL_PARTY, (30000, 30000, 30000)),
    },
    {
        'name': 'np-farm-trivial-wave-still-spends-nothing',
        'source': 'synthetic_with_cached_NA_servant_descriptions',
        'objective': {'fields': {'kind': 'attack'}},
        'objective_rationale': (
            'The same party against 1,372 HP trash must NOT spend the farming setup; face '
            'cards clear it, so the cheapest correct plan is to advance to the card phase.'),
        'observation': observation('np-farm-trivial-wave-still-spends-nothing', 1, 1,
                                   FULL_PARTY, (1372, 1372, 1372)),
    },
    {
        'name': 'np-farm-final-wave-amplifier-before-np',
        'source': 'synthetic_with_cached_NA_servant_descriptions',
        'objective': {'any_of': [
            {'fields': {'kind': 'skill', 'servant': 2, 'skill': 3, 'target': 3}},
            {'fields': {'kind': 'skill', 'servant': 1, 'skill': 3, 'target': 3}},
        ]},
        'objective_rationale': (
            'The dealer is already at 100% NP on the heaviest wave, so the remaining value is a '
            'one-turn damage amplifier aimed at it: Oberon S3 NP Strength or Koyanskaya S3 '
            'Buster effectiveness. Charging further is wasted and attacking unbuffed is weaker.'),
        'observation': observation('np-farm-final-wave-amplifier-before-np', 3, 3,
                                   [ally(1, KOYANSKAYA_LIGHT, 20, (3,)),
                                    ally(2, OBERON, 20, (3,)),
                                    ally(3, ARJUNA_ALTER, 100, ())],
                                   (45000, 45000, 45000)),
    },
    {
        'name': 'np-farm-wave-survived-so-stop-repeating',
        'source': 'synthetic_with_cached_NA_servant_descriptions',
        'objective': {'fields': {'kind': 'skill'}},
        'objective_rationale': (
            'stage_turn 2 is observed proof a plain card turn did not clear this wave. Repeating '
            'attack is the mistake; the held buffs and charges should now be spent.'),
        'observation': observation('np-farm-wave-survived-so-stop-repeating', 2, 3,
                                   FULL_PARTY, (18000, 18000, 0), stage_turn=2),
    },
]

# The damage dealer sits in slot 1 here, so a policy that buffs by slot
# order rather than by class-adjusted attack power fails this case.
CASES.append({
    'name': 'np-farm-buff-follows-attack-power-not-slot-order',
    'source': 'synthetic_with_cached_NA_servant_descriptions',
    'objective': {'any_of': [
        {'fields': {'kind': 'skill', 'servant': 2, 'skill': 1, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 2, 'skill': 3, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 3, 'skill': 1, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 3, 'skill': 2, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 3, 'skill': 3, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 1, 'skill': 1, 'target': 1}},
        {'fields': {'kind': 'skill', 'servant': 1, 'skill': 2, 'target': 1}},
    ]},
    'objective_rationale': (
        'Arjuna Alter is in slot 1 and has the highest class-adjusted attack (11,669 x 1.1 = '
        '12,836) versus Koyanskaya of Light in slot 2 (11,616 x 0.9 = 10,454). Selectable '
        'charges and Buster buffs belong on slot 1. Raw attack alone would not separate them.'),
    'observation': observation('np-farm-buff-follows-attack-power-not-slot-order', 1, 1,
                               [ally(1, ARJUNA_ALTER, 0, (1, 2, 3)),
                                ally(2, KOYANSKAYA_LIGHT, 0, (1, 2, 3)),
                                ally(3, OBERON, 0, (1, 2, 3))],
                               (30000, 30000, 30000)),
})

# The user's real field comp: two Koyanskaya of Light batteries plus Arjuna Alter,
# who starts at 75% NP from a Winter Crystal craft essence. Oberon sits in the back
# line and is reachable only through Order Change (see the final back-line case).
DOUBLE_KOYANSKAYA = [ally(1, KOYANSKAYA_LIGHT, 0, (1, 2, 3)),
                     ally(2, KOYANSKAYA_LIGHT, 0, (1, 2, 3)),
                     ally(3, ARJUNA_ALTER, 75, (1, 2, 3))]

CASES.append({
    'name': 'np-farm-double-battery-opens-on-the-dealer',
    'source': 'synthetic_with_cached_NA_servant_descriptions',
    'objective': {'any_of': [{'fields': {'kind': 'skill', 'servant': slot, 'target': 3}}
                             for slot in (1, 2, 3)]},
    'objective_rationale': (
        'Two Koyanskaya batteries and Arjuna Alter at 75% against three 30,000 HP enemies. '
        'Every worthwhile opening action is a charge or Buster buff aimed at slot 3, the only '
        'area-NP damage dealer and the highest class-adjusted attack on the field.'),
    'observation': observation('np-farm-double-battery-opens-on-the-dealer', 1, 1,
                               DOUBLE_KOYANSKAYA, (30000, 30000, 30000)),
})

CASES.append({
    'name': 'np-farm-second-wave-uses-the-untouched-battery',
    'source': 'synthetic_with_cached_NA_servant_descriptions',
    'objective': {'any_of': [{'fields': {'kind': 'skill', 'servant': 2, 'skill': 1, 'target': 3}},
                             {'fields': {'kind': 'skill', 'servant': 2, 'skill': 3, 'target': 3}}]},
    'objective_rationale': (
        'The dealer spent his NP on wave 1 and sits at 0%, and the slot 1 battery is on '
        'cooldown. The slot 2 Koyanskaya is untouched, so her charge is what can produce a '
        'second wave-clearing NP. Its cooldown reduction also lands on the dealer.'),
    'observation': observation('np-farm-second-wave-uses-the-untouched-battery', 2, 2,
                               [ally(1, KOYANSKAYA_LIGHT, 20, ()),
                                ally(2, KOYANSKAYA_LIGHT, 20, (1, 2, 3)),
                                ally(3, ARJUNA_ALTER, 0, ())],
                               (28000, 28000, 28000)),
})

# Wave 3 after both field batteries are spent: the measured charge budget of the
# live Extreme runs. Order Change is the only remaining source of charge.
BACKLINE = [{'slot': 1, 'name': 'Oberon', 'servant_id': OBERON},
            {'slot': 2, 'name': 'Nitocris (Alter)', 'servant_id': 370},
            {'slot': 3, 'name': 'Space Ereshkigal', 'servant_id': None}]
_final = observation('np-farm-final-wave-brings-in-the-back-line', 3, 3,
                     [ally(1, KOYANSKAYA_LIGHT, 0, (2,)),
                      ally(2, KOYANSKAYA_LIGHT, 0, (2,)),
                      ally(3, ARJUNA_ALTER, 0, (3,))],
                     (137991, 0, 0))
_final['signals'].update({'order_change_available': True, 'backline': BACKLINE})
CASES.append({
    'name': 'np-farm-final-wave-brings-in-the-back-line',
    'source': 'synthetic_with_cached_NA_servant_descriptions',
    'objective': {'plan_includes_any': ['oc-f1-b1', 'oc-f2-b1']},
    'objective_rationale': (
        'Both Koyanskaya batteries are spent and the dealer is at 0% facing a 137,991 HP final '
        'wave, as measured live. The back-line Oberon supplies charge and a one-turn NP '
        'amplifier, so the turn must swap a spent Koyanskaya (never the dealer in slot 3) for '
        'him. Spending the last useful skill of the outgoing servant before the swap is fine.'),
    'observation': _final,
})

# Measured live (20260922T232303Z-231b8d95): right after swapping Oberon in, the
# dealer sits at 0% and Oberon's two charges give only +70%, so no NP this turn.
# His single charge expires at end of turn and his one-turn amplifier ends by
# stripping the target's buffs, so both belong to the NEXT turn.
_post_swap = observation('np-farm-hold-turn-limited-skills-when-np-is-unreachable', 3, 3,
                         [ally(1, OBERON, 0, (1, 2, 3)),
                          ally(2, KOYANSKAYA_LIGHT, 0, ()),
                          ally(3, ARJUNA_ALTER, 0, ())],
                         (135175, 0, 0))
CASES.append({
    'name': 'np-farm-hold-turn-limited-skills-when-np-is-unreachable',
    'source': 'synthetic_from_live_run_20260922T232303Z',
    'objective': {'plan_excludes_all': ['s1-2-t3', 's1-3-t3']},
    'objective_rationale': (
        'Live, spending Oberon S2 (charge that decreases at end of turn) and S3 (one-turn '
        'amplifier ending in buff removal and Eternal Slumber) on the 0% dealer left him at 70%, '
        'then 64% and debuffed; wave 3 took three turns. Neither may be spent on the dealer on a '
        'turn his NP cannot fire.'),
    'observation': _post_swap,
})

if __name__ == '__main__':
    path = Path(__file__).parent / 'np-farming-scenarios.json'
    path.write_text(json.dumps({'version': 1, 'cases': CASES}, indent=1) + '\n', encoding='utf-8')
    print(f'{path}: {len(CASES)} cases')
