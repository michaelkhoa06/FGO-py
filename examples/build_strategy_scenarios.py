"""Regenerate hand-authored snapshot fixtures; no game or model connection."""
import json
from pathlib import Path


def ally(slot, ident, hp=12000, np=0, ready=()):
    return {'slot': slot, 'id': ident, 'hp': hp, 'np': np,
            'skills': [{'slot': i, 'ready': i in ready, 'cooldown': None if i in ready else 5} for i in (1, 2, 3)]}


def enemy(slot=1, hp=80000, charge=(1, 5)):
    return {'slot': slot, 'hp': hp, 'charge': list(charge)}


def faces():
    return [{'slot': i, 'type': color, 'servant': owner, 'visualgroupid': owner,
             'sealed': False, 'affinity': 'neutral', 'critical_rate': 0}
            for i, color, owner in [(1, 'buster', 3), (2, 'arts', 1), (3, 'buster', 3),
                                    (4, 'quick', 2), (5, 'arts', 2)]]


cases = []
def add(name, allies, enemies, objective, rationale, cards=None, wave=1, total=3):
    cases.append({'name': name, 'source': 'synthetic_with_cached_NA_servant_descriptions',
                  'objective': objective, 'objective_rationale': rationale,
                  'observation': {'id': name, 'phase': 'cards' if cards else 'tactical',
                                  'captured_at': 'synthetic', 'allies': allies, 'enemies': enemies,
                                  'cards': cards or [], 'wave': wave, 'turn': wave,
                                  'signals': {'stage_total': total, 'stage_turn': 1}}})

add('conserve-on-weak-opening',
    [ally(1,314,np=13,ready=(1,2,3)), ally(2,314,ready=(1,2,3)), ally(3,247,np=75,ready=(1,2,3))],
    [enemy(i,1300) for i in (1,2,3)], {'fields': {'kind': 'attack'}},
    'Very low-HP enemies and healthy party: avoid spending skills before normal attacks.')
add('charge-near-ready-damage-dealer',
    [ally(1,314,np=13,ready=(1,)), ally(2,37,np=0), ally(3,247,np=75)],
    [enemy(i,90000) for i in (1,2,3)], {'fields': {'kind':'skill','servant':1,'skill':1,'target':3}},
    'Target the near-ready area damage NP rather than the lowest-NP support; exact charge magnitude remains unknown.')
add('merlin-defense-before-enemy-np',
    [ally(1,150,ready=(2,)), ally(2,2,hp=3000), ally(3,65,hp=4000)],
    [enemy(hp=160000,charge=(5,5))], {'fields': {'kind':'skill','servant':1,'skill':2}},
    'Available party invincibility is useful before full enemy charge.')
add('unknown-party-state-does-not-invent-skill-input',
    [ally(1,None,hp=None,np=None,ready=(1,2,3)), ally(2,284,hp=None,np=None,ready=(1,2,3)), ally(3,215,np=None)],
    [enemy(hp=None,charge=(None,None))], {'fields': {'kind':'attack'}},
    'Only Attack is legal; unknown state must not manufacture targets or a skill.')
add('save-skadi-charge-at-full-np',
    [ally(1,215,np=100,ready=(3,)), ally(2,65,np=100), ally(3,2,np=100)],
    [enemy(hp=90000)], {'fields': {'kind':'attack'}},
    'All known NP gauges are already full; avoid spending a targeted charge skill for no observed need.')
add('mash-defend-low-party',
    [ally(1,1,hp=2500,ready=(1,)), ally(2,284,hp=2400), ally(3,2,hp=2700)],
    [enemy(hp=120000,charge=(5,5))], {'fields': {'kind':'skill','servant':1,'skill':1}},
    'Party defense is available before an enemy with full charge; exact upgrade remains unknown.')

cards = faces() + [{'slot':8,'type':'np','servant':3,'sealed':False,'ready':True}]
add('area-np-on-large-wave', [ally(1,37),ally(2,284),ally(3,247,np=100)],
    [enemy(i,90000) for i in (1,2,3)], {'fields':{'kind':'cards'},'cards_include':[8]},
    'A ready area NP addresses several high-HP enemies.',cards=cards)
add('save-np-on-weak-nonfinal-wave', [ally(1,37),ally(2,284),ally(3,247,np=100)],
    [enemy(i,1000) for i in (1,2,3)], {'fields':{'kind':'cards'},'cards_exclude':[8]},
    'Healthy party and weak early wave: conserve a ready NP for later waves.',cards=cards)
sealed = faces() + [{'slot':8,'type':'np','servant':3,'sealed':True,'ready':True}]
add('sealed-np-is-never-selected', [ally(1,150),ally(2,215),ally(3,2,np=100)],
    [enemy(hp=180000)], {'fields':{'kind':'cards'},'cards_exclude':[8]},
    'Seal evidence overrides readiness.',cards=sealed,wave=3)
grouped = faces()
for c in grouped:
    c['servant'] = None
grouped[3]['sealed'] = True
add('unknown-ownership-retains-visual-cards', [ally(1,314),ally(2,314),ally(3,65)],
    [enemy(hp=15000)], {'fields':{'kind':'cards'},'cards_exclude':[4]},
    'Use visible unsealed cards without inventing field ownership for duplicate servants.',cards=grouped)

favorable = [{'slot': i, 'type': 'buster', 'servant': 3 if i <= 3 else 1,
              'visualgroupid': 3 if i <= 3 else 1, 'sealed': False,
              'affinity': 'weak' if i <= 3 else 'resist', 'critical_rate': 0}
             for i in range(1,6)]
add('enemy-weak-means-favorable-affinity', [ally(1,150),ally(2,37),ally(3,2)],
    [enemy(hp=150000)], {'fields':{'kind':'cards'},'cards_include':[1,2,3]},
    'Regression from live explanations: three otherwise comparable weak-affinity Buster cards are advantageous, while resist is unfavorable.',
    cards=favorable,wave=3)

path = Path(__file__).with_name('strategy-scenarios.json')
path.write_text(json.dumps({'version':1,'cases':cases},indent=2),encoding='utf-8')
print(path)
