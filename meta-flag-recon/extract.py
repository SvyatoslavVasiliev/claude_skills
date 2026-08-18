#!/usr/bin/env python3
"""Pull experiment/gating signal out of collected Meta web bundles.

    python3 extract.py out/ig --label instagram
    python3 extract.py out/fb --label facebook

Meta ships gatekeepers as opaque numeric ids (`gkx.get(12345)`), so the ids
alone say nothing. The leverage is that the surrounding `__d("ModuleName")`
wrapper keeps its human-readable name: attributing each id to the module that
reads it turns an anonymous number into "this gates Reels autoplay". That
attribution is what this script is built around.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

MODULE_DEF = re.compile(r'__d\(\s*["\']([A-Za-z0-9_.$\-]+)["\']')
# Bundles reach the switch two ways: inline (`c("gkx").get(123)`) or through a
# hoisted alias (`var g=c("gkx"); ... g.get(123)`). The hoisted form loses the
# name, so it is only trusted inside a module that mentions gkx/qex at all.
GKX_DIRECT = re.compile(r'gkx["\']?\s*\)?\s*\.\s*(?:get|_)\s*\(\s*["\']?(\d{3,})')
GKX_HOISTED = re.compile(r'\.\s*(?:get|_)\s*\(\s*(\d{4,})\s*[,)]')
QEX_DIRECT = re.compile(r'qex["\']?\s*\)?\s*\.\s*(?:_|get)\s*\(\s*["\']([\w:.\-]{3,})["\']')
QEX_HOISTED = re.compile(r'\.\s*_\s*\(\s*["\']([a-z][\w:.\-]{4,})["\']\s*[,)]')
STRING_LIT = re.compile(r'["\']([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})["\']')
RELAY_OP = re.compile(r'name\s*:\s*["\']([A-Za-z0-9_]+(?:Query|Mutation|Subscription))["\']')

# Module names that advertise themselves as gating surfaces.
GATING_MODULE = re.compile(
    r'(Gating|Gatekeeper|GK|QuickExperiment|Experiment|Rollout|Holdout|'
    r'Killswitch|KillSwitch|FeatureFlag|Trial|Nux|EarlyAccess)',
)

# Prefixes Meta actually uses for named flags, per surface.
FLAG_PREFIX = re.compile(
    r'^(ig|igd|igds|polaris|fb|fbandroid|comet|web|msgr|messenger|wa|whatsapp|'
    r'ads|xfb|meta|ent|reels|stories|threads|xdt|bloks)_',
)
FLAG_SUBSTR = re.compile(
    r'(_enabled|_killswitch|_rollout|_holdout|_universe|_gating|_experiment|'
    r'_variant|_treatment|_launch|_ramp|_is_active|_should_)',
)

# Coarse product buckets so the report reads as a roadmap, not a symbol dump.
PRODUCTS = [
    # Ordered by specificity: an "AdsRanking" module is an ads bet, not a feed
    # bet, so the narrower buckets must get first refusal.
    ('Ads & monetization', r'Ads|Advert|Monetiz|Commerce|Shopping|Checkout|Payment|Subscription|Paid'),
    ('Reels / short video', r'Reels|Clips|ShortForm|Watch|Video'),
    ('Feed & ranking', r'Feed|Ranking|Rank|Recommend|Discover|Explore'),
    ('Messaging / DM', r'Direct|Msgr|Messenger|Thread(?!s\b)|Inbox|Chat'),
    ('AI features', r'(?<=[a-z])AI(?![a-z])|Genai|GenAI|Llama|Assistant|Imagine|SmartReply|Summar'),
    ('Creator tools', r'Creator|Camera|Editor|Remix|Music|Effect|Broadcast'),
    ('Stories', r'Stories|Story|Fbstory'),
    ('Growth & onboarding', r'Nux|Onboard|Signup|Registration|Invite|Referral|Reengage'),
    ('Privacy / safety / teen', r'Privacy|Safety|Teen|Minor|Consent|Gdpr|Dsa|Restrict|Report|Block'),
    ('Search', r'Search|Query|Typeahead'),
    ('Profile & identity', r'Profile|Identity|Account|Avatar'),
    ('Notifications', r'Notif|Push|Badge'),
]


def read_bundles(root: Path):
    for path in sorted(root.glob('js/*.js')):
        try:
            yield path, path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue


def split_modules(source: str):
    """Yield (module_name, body) for each __d() definition in a bundle."""
    marks = [(m.start(), m.group(1)) for m in MODULE_DEF.finditer(source)]
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(source)
        yield name, source[start:end]


def balanced_object(text: str, start: int):
    """Extract the JSON object beginning at the first '{' at or after start."""
    open_at = text.find('{', start)
    if open_at == -1:
        return None
    depth, in_str, escaped = 0, False, False
    for i in range(open_at, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[open_at:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def scrape_bootstrap(root: Path):
    """Recover the gatekeeper values the server decided for this session."""
    gkx, qex = {}, {}
    for path in list(root.glob('html/*.html')) + list(root.glob('*.json')):
        text = path.read_text(encoding='utf-8', errors='replace')
        for key, sink in (('gkxData', gkx), ('qexData', qex)):
            for m in re.finditer(re.escape(f'"{key}"'), text):
                obj = balanced_object(text, m.end())
                if isinstance(obj, dict):
                    sink.update(obj)
    # Instagram's shared_data probe carries readable quick-experiment names.
    named_qe = {}
    for path in root.glob('probe*.json'):
        try:
            blob = json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(blob, dict) and isinstance(blob.get('qe'), dict):
            named_qe.update(blob['qe'])
    return gkx, qex, named_qe


def products_of(name: str) -> list:
    """All areas a module touches — a DM composer can also be an AI bet."""
    hits = [label for label, pattern in PRODUCTS if re.search(pattern, name)]
    return hits or ['Other / platform']


def analyze(root: Path):
    modules = {}
    gkx_owners = defaultdict(set)
    qex_owners = defaultdict(set)
    flag_owners = defaultdict(set)
    relay_ops = Counter()
    bundle_count = 0

    for path, source in read_bundles(root):
        bundle_count += 1
        relay_ops.update(RELAY_OP.findall(source))
        for name, body in split_modules(source):
            gk = set(GKX_DIRECT.findall(body))
            if 'gkx' in body:
                gk |= set(GKX_HOISTED.findall(body))
            qe = set(QEX_DIRECT.findall(body))
            if 'qex' in body:
                qe |= set(QEX_HOISTED.findall(body))
            flags = {
                s for s in STRING_LIT.findall(body)
                if FLAG_PREFIX.match(s) or FLAG_SUBSTR.search(s)
            }
            if not (gk or qe or flags or GATING_MODULE.search(name)):
                continue
            entry = modules.setdefault(
                name, {'gkx': set(), 'qex': set(), 'flags': set(), 'files': set()},
            )
            entry['gkx'] |= gk
            entry['qex'] |= qe
            entry['flags'] |= flags
            entry['files'].add(path.name)
            for i in gk:
                gkx_owners[i].add(name)
            for i in qe:
                qex_owners[i].add(name)
            for f in flags:
                flag_owners[f].add(name)

    gkx_values, qex_values, named_qe = scrape_bootstrap(root)
    return {
        'bundles': bundle_count,
        'modules': modules,
        'gkx_owners': gkx_owners,
        'qex_owners': qex_owners,
        'flag_owners': flag_owners,
        'relay_ops': relay_ops,
        'gkx_values': gkx_values,
        'qex_values': qex_values,
        'named_qe': named_qe,
    }


def truthy(value) -> bool:
    if isinstance(value, dict):
        return bool(value.get('r'))
    return bool(value)


def render(data, label: str) -> str:
    mods = data['modules']
    out = [f'# Experiment surface: {label}', '']
    out.append(f"- bundles parsed: **{data['bundles']}**")
    out.append(f"- modules touching gating: **{len(mods)}**")
    out.append(f"- distinct gatekeeper ids referenced: **{len(data['gkx_owners'])}**")
    out.append(f"- distinct quick-experiment ids referenced: **{len(data['qex_owners'])}**")
    out.append(f"- readable flag-name literals: **{len(data['flag_owners'])}**")
    out.append(f"- server-supplied gatekeeper values: **{len(data['gkx_values'])}**")
    out.append(f"- named quick experiments (shared_data): **{len(data['named_qe'])}**")
    out.append('')

    # Which experiments are switched ON for this session, and what they gate.
    live = [(i, v) for i, v in data['gkx_values'].items() if truthy(v)]
    if live:
        out += ['## Gatekeepers ON for this session', '',
                '| id | reading modules |', '| --- | --- |']
        attributed = [(i, sorted(data['gkx_owners'].get(i, ()))) for i, _ in live]
        attributed.sort(key=lambda kv: (not kv[1], kv[0]))
        for gk_id, owners in attributed[:80]:
            out.append(f"| `{gk_id}` | {', '.join(owners[:6]) or '_unattributed_'} |")
        out.append('')

    if data['named_qe']:
        out += ['## Named quick experiments', '',
                '| experiment | group | params |', '| --- | --- | --- |']
        for name, cfg in sorted(data['named_qe'].items())[:120]:
            group = cfg.get('g', '') if isinstance(cfg, dict) else ''
            params = cfg.get('p', {}) if isinstance(cfg, dict) else {}
            keys = ', '.join(sorted(params)[:6]) if isinstance(params, dict) else ''
            out.append(f'| `{name}` | {group or "—"} | {keys or "—"} |')
        out.append('')

    # Hot spots: the modules wired to the most switches are where the bets are.
    hot = sorted(mods.items(), key=lambda kv: -(len(kv[1]['gkx']) + len(kv[1]['qex'])))
    out += ['## Densest gating modules', '',
            '| module | product area | gkx | qex |', '| --- | --- | --: | --: |']
    for name, e in hot[:60]:
        if not (e['gkx'] or e['qex']):
            break
        areas = ' + '.join(products_of(name))
        out.append(f"| `{name}` | {areas} | {len(e['gkx'])} | {len(e['qex'])} |")
    out.append('')

    out += ['## Gating modules by product area', '']
    by_product = defaultdict(list)
    for name in mods:
        for area in products_of(name):
            by_product[area].append(name)
    for label_, names in sorted(by_product.items(), key=lambda kv: -len(kv[1])):
        out.append(f'### {label_} ({len(names)} modules)')
        out.append('')
        for name in sorted(names)[:40]:
            out.append(f'- `{name}`')
        if len(names) > 40:
            out.append(f'- _…{len(names) - 40} more_')
        out.append('')

    out += ['## Readable flag names', '']
    by_prefix = defaultdict(list)
    for flag in data['flag_owners']:
        m = FLAG_PREFIX.match(flag)
        by_prefix[m.group(1) if m else 'other'].append(flag)
    for prefix, flags in sorted(by_prefix.items(), key=lambda kv: -len(kv[1])):
        out.append(f'### `{prefix}_*` ({len(flags)})')
        out.append('')
        for flag in sorted(flags)[:60]:
            owners = sorted(data['flag_owners'][flag])[:3]
            out.append(f"- `{flag}` — {', '.join(owners) or '?'}")
        if len(flags) > 60:
            out.append(f'- _…{len(flags) - 60} more_')
        out.append('')

    return '\n'.join(out)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1])
    label = sys.argv[sys.argv.index('--label') + 1] if '--label' in sys.argv else root.name
    if not root.exists():
        sys.exit(f'no such collection dir: {root}')

    data = analyze(root)
    report = render(data, label)
    (root / 'report.md').write_text(report, encoding='utf-8')
    (root / 'flags.json').write_text(json.dumps({
        'modules': {
            k: {kk: sorted(vv) for kk, vv in v.items()} for k, v in data['modules'].items()
        },
        'gkx_owners': {k: sorted(v) for k, v in data['gkx_owners'].items()},
        'qex_owners': {k: sorted(v) for k, v in data['qex_owners'].items()},
        'flag_owners': {k: sorted(v) for k, v in data['flag_owners'].items()},
        'gkx_values': data['gkx_values'],
        'qex_values': data['qex_values'],
        'named_qe': data['named_qe'],
        'relay_ops': data['relay_ops'].most_common(),
    }, indent=2), encoding='utf-8')
    print(report[:4000])
    print(f'\nwrote {root/"report.md"} and {root/"flags.json"}')


if __name__ == '__main__':
    main()
