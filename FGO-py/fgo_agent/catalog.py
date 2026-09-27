"""Explicitly refreshed, cached English strategy data; never read as game state."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

DEFAULT_CATALOG = Path(__file__).parent / 'assets' / 'strategy' / 'na.json'
DEFAULT_SERVANT_INDEX = Path(__file__).parent / 'assets' / 'strategy' / 'servant_index.json'
SERVANT_INDEX_URL = 'https://api.atlasacademy.io/export/NA/basic_servant.json'
CLASS_ATTACK_RATE_URL = 'https://api.atlasacademy.io/export/NA/NiceClassAttackRate.json'
FULL_EXPORT_URL = 'https://api.atlasacademy.io/export/NA/nice_servant.json'


def fetch_class_attack_rates(url=CLASS_ATTACK_RATE_URL):
    """Published per-class attack multipliers, as fractions of 1.0.

    Raw attack alone ranks servants wrongly: a Berserker at 1.1 out-hits a higher
    attack Assassin at 0.9.  These are official exported constants, not estimates.
    """
    request = Request(url, headers={'User-Agent': 'FGO-NA-strategy-prototype/1'})
    with urlopen(request, timeout=30) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('Class attack rate export exceeds the size limit')
    rates = {}
    for name, value in json.loads(raw).items():
        if type(value) is int and 0 < value <= 10_000:
            rates[str(name)] = round(value / 1000, 3)
    if not rates:
        raise ValueError('Class attack rate export contained no usable rates')
    return rates


def clean_text(value, limit=1800):
    return re.sub(r'<[^>]*>', '', str(value or '')).replace('[g]', '').replace('[/g]', '')[:limit]


CLASS_RELATION_URL = 'https://api.atlasacademy.io/export/NA/NiceClassRelation.json'
DEFAULT_CLASS_RELATION = Path(__file__).parent / 'assets' / 'strategy' / 'class_relation.json'

# Buff types that change damage, kept with their numbers for the damage estimate.
DAMAGE_BUFFS = {'upCommandall', 'upAtk', 'upNpdamage', 'upDamage', 'downDefence', 'upDefence', 'downAtk',
                'downCommandall', 'downNpdamage', 'upCriticaldamage', 'pierceDefence'}
CARD_CODES = {'1': 'arts', '2': 'buster', '3': 'quick'}


def numeric_effects(functions):
    """Compact numeric effects of one skill or NP from Atlas 'nice' functions.

    ``values`` lists the Atlas Value per skill level (10) or per NP level (5):
    buffs in tenths of a percent (500 = +50%), NP damage in tenths of a percent of
    ATK (9000 = 900%), NP charge in hundredths (3000 = 30%). Traits name the card
    type a card buff affects (self) and the enemy trait a bonus needs (target);
    ``requires`` names a state the user must already hold for the effect to apply.
    """
    effects = []
    for f in functions or []:
        kind = f.get('funcType') or ''
        buff = (f.get('buffs') or [{}])[0] if f.get('buffs') else {}
        btype = buff.get('type')
        damage = kind.startswith('damageNp')
        grants = btype == 'addIndividuality'
        if not (damage or kind == 'gainNp' or btype in DAMAGE_BUFFS or grants):
            continue
        svals = f.get('svals') or []
        effect = {'fn': kind, 'to': f.get('funcTargetType'), 'buff': btype,
                  'values': [v.get('Value') for v in svals],
                  'turns': svals[-1].get('Turn') if svals else None,
                  'count': svals[-1].get('Count') if svals else None}
        card = [t.get('name') for t in buff.get('ckSelfIndv') or [] if str(t.get('name', '')).startswith('card')]
        if card:
            effect['card'] = [c[4:].lower() for c in card]
        needs = [t.get('name') for t in buff.get('ckOpIndv') or [] if t.get('name')]
        if needs:
            effect['target_traits'] = needs[:4]
        requires = [t.get('id') for t in f.get('functvals') or [] if type(t.get('id')) is int]
        if requires:
            effect['requires'] = requires[:3]
        if grants and svals and type(svals[-1].get('Value')) is int:
            effect['grants_trait'] = svals[-1]['Value']
            effect['state'] = clean_text(buff.get('name'), 60)
        if damage:
            effect['correction'] = svals[0].get('Correction') if svals else None
            effect['conditional'] = kind != 'damageNp' and kind != 'damageNpPierce'
        effects.append(effect)
    return effects


def normalize_servant(data, source_url, raw_hash, fetched_at, class_rates=None):
    """Retain upgrade alternatives instead of guessing the user's unlocked variant."""
    collection = data.get('collectionNo')
    if type(collection) is not int or collection <= 0:
        raise ValueError('Atlas response has no valid collection number')
    skills = []
    for slot in (1, 2, 3):
        variants = [{"id": s['id'], "name": clean_text(s.get('name'), 160),
                     "description": clean_text(s.get('detail')),
                     # Base cooldown at skill levels 1..10; the account's level is unknown.
                     "cooldown_by_level": [c for c in s.get('coolDown') or [] if type(c) is int][:10],
                     "effects": numeric_effects(s.get('functions'))}
                    for s in data.get('skills', []) if s.get('num') == slot]
        skills.append({'slot': slot, 'variants': variants})
    nps = [{'id': n['id'], 'name': clean_text(n.get('name'), 160),
            'description': clean_text(n.get('detail')), 'card': n.get('card'),
            'np_type': clean_text(n.get('effectFlags'), 180),
            'effects': numeric_effects(n.get('functions'))}
           for n in data.get('noblePhantasms', [])]
    def stat(key):
        value = data.get(key)
        return value if type(value) is int and 0 < value <= 1_000_000 else None

    rate = (class_rates or {}).get(str(data.get('className')))
    rate = rate if type(rate) is float and 0 < rate <= 10 else None
    atk_max = stat('atkMax')
    # Attack times the class multiplier is what actually ranks damage dealers.
    effective = round(atk_max * rate) if atk_max is not None and rate is not None else None

    return {'collection_no': collection, 'name': clean_text(data.get('name'), 160),
            'class_name': data.get('className'), 'skills': skills, 'noble_phantasms': nps,
            'cards': [CARD_CODES.get(str(c)) for c in data.get('cards') or []],
            # Always-on class passives, with numbers (e.g. Quick +11%).
            'passives': [e for ps in data.get('classPassive') or [] for e in numeric_effects(ps.get('functions'))],
            'rarity': stat('rarity'), 'attribute': data.get('attribute'),
            'class_attack_rate': rate, 'effective_atk_max': effective,
            # Max-level base values. They rank servants by attack power; they are not
            # this account's actual stats, which depend on level, Fou and Craft Essence.
            'atk_max': stat('atkMax'), 'hp_max': stat('hpMax'), 'level_max': stat('lvMax'),
            'source_url': source_url, 'source_sha256': raw_hash, 'fetched_at': fetched_at,
            'limitations': ['Reference descriptions, not observed active effects.',
                            'Skill levels and unlocked upgrade variants are unknown.',
                            'Descriptions may omit numeric amounts; do not invent them.',
                            'cooldown_by_level lists base cooldowns for skill levels 1..10; the '
                            'account level is unknown, so an observed cooldown outranks it.',
                            'atk_max/hp_max are max-level base stats for ranking only; the '
                            'account level, Fou and Craft Essence are unknown.',
                            'effective_atk_max is atk_max times the official class attack rate, '
                            'for comparing damage dealers; it is still not an account value.',
                            'Cached NA metadata may differ from the account or battle rules.']}


def load_catalog(path=DEFAULT_CATALOG):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('region') != 'NA' or data.get('version') != 1:
        raise ValueError('Unsupported strategy catalog')
    return {int(k): v for k, v in data['servants'].items()}


def refresh_catalog(ids, path=DEFAULT_CATALOG):
    """Download only explicitly requested public records, outside battle execution."""
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 30 or any(type(i) is not int or not 1 <= i <= 10000 for i in ids):
        raise ValueError('Provide 1..30 servant collection numbers')
    records = load_catalog(path)
    class_rates = fetch_class_attack_rates()
    for ident in ids:
        url = f'https://api.atlasacademy.io/nice/NA/servant/{ident}?lang=en'
        request = Request(url, headers={'User-Agent': 'FGO-NA-strategy-prototype/1'})
        with urlopen(request, timeout=30) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('Atlas response exceeds the size limit')
        data = json.loads(raw)
        if data.get('collectionNo') != ident:
            raise ValueError('Atlas response does not match requested collection number')
        records[ident] = normalize_servant(data, url, hashlib.sha256(raw).hexdigest(),
                                          datetime.now(timezone.utc).isoformat(), class_rates)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace only after every requested record has been validated.
    staging = path.with_suffix('.json.tmp')
    staging.write_text(json.dumps({'version': 1, 'region': 'NA', 'servants': records},
                                  ensure_ascii=False, indent=2), encoding='utf-8')
    staging.replace(path)
    return records


def refresh_full_catalog(path=DEFAULT_CATALOG, url=FULL_EXPORT_URL):
    """Replace the catalog with every playable NA servant from one Atlas export.

    Story parties are whatever the quest allows, so a catalog of a few farming
    servants left most story allies without skill text. One bulk export is lighter
    on Atlas than hundreds of per-servant requests.
    """
    request = Request(url, headers={'User-Agent': 'FGO-NA-strategy-prototype/1'})
    with urlopen(request, timeout=300) as response:
        raw = response.read(150_000_001)
    if len(raw) > 150_000_000:
        raise ValueError('Atlas servant export exceeds the size limit')
    raw_hash, fetched_at = hashlib.sha256(raw).hexdigest(), datetime.now(timezone.utc).isoformat()
    class_rates = fetch_class_attack_rates()
    records = {}
    for data in json.loads(raw):
        ident = data.get('collectionNo')
        if data.get('type') not in ('normal', 'heroine') or type(ident) is not int or ident <= 0:
            continue
        records[ident] = normalize_servant(data, f'{url}#collectionNo={ident}', raw_hash, fetched_at, class_rates)
    if len(records) < 100:
        raise ValueError(f'Atlas export yielded only {len(records)} servants; keeping the old catalog')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix('.json.tmp')
    staging.write_text(json.dumps({'version': 1, 'region': 'NA', 'servants': records},
                                  ensure_ascii=False, indent=1), encoding='utf-8')
    staging.replace(path)
    return records


def refresh_class_relation(path=DEFAULT_CLASS_RELATION, url=CLASS_RELATION_URL):
    """Cache the official attacker -> defender class damage multipliers (1000 = 1.0)."""
    request = Request(url, headers={'User-Agent': 'FGO-NA-strategy-prototype/1'})
    with urlopen(request, timeout=60) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError('Class relation export exceeds the size limit')
    table = {str(a): {str(d): v for d, v in row.items() if type(v) is int}
             for a, row in json.loads(raw).items() if isinstance(row, dict)}
    if len(table) < 10:
        raise ValueError('Class relation export looks incomplete')
    path = Path(path)
    path.write_text(json.dumps({'version': 1, 'region': 'NA', 'relation': table}, indent=0), encoding='utf-8')
    return table


def load_class_relation(path=DEFAULT_CLASS_RELATION) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8')).get('relation', {})


def refresh_servant_index(path=DEFAULT_SERVANT_INDEX, url=SERVANT_INDEX_URL):
    """Cache every playable NA servant's collection number, name and class.

    Used only to turn a name read off the Order Change screen into an id. Names
    are not unique across classes, so resolution stays conservative elsewhere.
    """
    request = Request(url, headers={'User-Agent': 'FGO-NA-strategy-prototype/1'})
    with urlopen(request, timeout=60) as response:
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ValueError('Servant index exceeds the size limit')
    servants = {}
    for row in json.loads(raw):
        ident = row.get('collectionNo')
        if row.get('type') not in ('normal', 'heroine') or type(ident) is not int or ident <= 0:
            continue
        servants[str(ident)] = {'name': clean_text(row.get('name'), 160), 'class_name': row.get('className')}
    if not servants:
        raise ValueError('Servant index export contained no servants')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix('.json.tmp')
    staging.write_text(json.dumps({'version': 1, 'region': 'NA', 'source_url': url,
                                   'source_sha256': hashlib.sha256(raw).hexdigest(),
                                   'fetched_at': datetime.now(timezone.utc).isoformat(),
                                   'servants': servants}, ensure_ascii=False, indent=1), encoding='utf-8')
    staging.replace(path)
    return {int(k): v for k, v in servants.items()}


def load_servant_index(path=DEFAULT_SERVANT_INDEX):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('region') != 'NA' or data.get('version') != 1:
        raise ValueError('Unsupported servant index')
    return {int(k): v for k, v in data['servants'].items()}
