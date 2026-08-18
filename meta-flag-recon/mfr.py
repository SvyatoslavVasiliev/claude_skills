#!/usr/bin/env python3
"""
mfr.py — turn a snapshot of Meta's shipped frontend into a flag/surface inventory.

Subcommands:
  extract <snapshot-dir>            -> writes <snapshot>/inventory.json
  report  <snapshot-dir>            -> markdown report on stdout
  diff    <old-snapshot> <new-snap> -> markdown diff on stdout  (the roadmap signal)

Standard library only.

What it looks for and why:

  module names        __d("PolarisReelsAdsSurface", ...) — Meta ships module names
                      unminified. This is the single richest signal: it names the
                      surface, not just the fact that a flag exists.
  gkx ids             require("gkx")("25102") — Gatekeepers. The *name* is stripped
                      server-side, only a numeric id ships. The id alone is opaque;
                      the module it sits in is what gives it meaning, and a flip
                      between snapshots is what makes it interesting.
  qex keys            require("qex")._("ig_web_xyz") — QuickExperiments. These keep
                      readable keys far more often than GKs do.
  relay providers     __relay_internal__pv__<Name>relayprovider — how a GK/QE is
                      injected into a GraphQL query. Named, and the resolved value
                      for the logged-in account shows up in graphql.jsonl.
  relay operations    "name":"...Query","operationKind":"query" — the data model of
                      each surface, including surfaces with no UI shipped yet.
  logged events       "instagram_web_..." / "comet_..." falco event names — what they
                      are *measuring*, i.e. the metric the hypothesis is stated in.
  route paths         url patterns from *Routes modules — unreleased surfaces show up
                      here before they are reachable from the UI.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

# --- patterns ---------------------------------------------------------------

RE_MODULE = re.compile(r'__d\(\s*"([A-Za-z0-9_$.\-]{2,})"')
RE_DEFINE = re.compile(r'(?<![\w.])define\(\s*"([A-Za-z0-9_$.\-]{2,})"')
RE_GKX = re.compile(r'\(\s*["\']gkx["\']\s*\)\s*\(\s*["\'](\d{3,})["\']')
RE_GKX_BARE = re.compile(r'(?<![\w.])gkx\(\s*["\'](\d{3,})["\']')
RE_QEX = re.compile(r'\(\s*["\']qex["\']\s*\)\s*\.\s*_\(\s*["\']([\w.:\-]{3,})["\']')
RE_QEX_BARE = re.compile(r'(?<![\w.])qex\s*\.\s*_\(\s*["\']([\w.:\-]{3,})["\']')
RE_RELAY_PV = re.compile(r'__relay_internal__pv__([A-Za-z0-9_]+)')
RE_RELAY_OP = re.compile(r'"name"\s*:\s*"([A-Za-z0-9_]+)"\s*,\s*"operationKind"\s*:\s*"(query|mutation|subscription)"')
RE_DOC_ID = re.compile(r'"doc_id"\s*:\s*"?(\d{6,})')
# Relay's generated params object: {"id":"<doc id>","metadata":{},"name":"XQuery",...}
RE_RELAY_ID = re.compile(r'"id"\s*:\s*"(\d{6,})"\s*,\s*"metadata"')
RE_EVENT = re.compile(
    r'["\']((?:instagram|ig|fb|comet|messenger|msgr|meta|polaris|xfb)_[a-z0-9_]{6,64})["\']'
)
RE_ROUTE = re.compile(r'["\'](/(?:[a-z0-9_\-]+/)*(?::[a-z_]+|[a-z0-9_\-]+)/?)["\']', re.I)
RE_QUOTED_NUM = re.compile(r'["\'](\d{4,6})["\']')

# JSON blobs embedded in the HTML document.
HTML_JSON_KEYS = ("gkxData", "qexData", "consistency", "rollout_hash", "qplData")

# --- product taxonomy -------------------------------------------------------
# Ordered: first match wins, so put the specific buckets above the generic ones.
TAXONOMY = [
    ("ai", r"MetaAI|GenAI|Imagine|Llama|AIStudio|AiStudio|Muse|Restyle|Genie|LLM|Assistant|Vibes|SmartReply|AutoDub|Translat"),
    ("ads-monetization", r"Ads?[A-Z]|Sponsored|Monetiz|Payout|Bonus|Subscription|Paid|Checkout|Commerce|Shopping|Payment|MetaVerified|Boost|Promote|Revenue"),
    ("messaging", r"Direct|Thread|Messenger|MSGR|Msgr|Chat|Inbox|LSPlatform|Lightspeed|Presence|Call|Rtc"),
    ("reels-video", r"Reel|Clips|Video|Watch|Player|Autoplay|Playback|Shorts|Vod|Live"),
    ("creation", r"Camera|Composer|Editor|Creat(e|ion)|Upload|Sticker|Music|Template|Edits|Draft|Caption|Remix|Filter|Crop"),
    ("ranking-discovery", r"Feed|Rank|Recommend|Explore|Discover|Suggest|ForYou|Interest|Topic"),
    ("search", r"Search|Typeahead|Autocomplete|Query[A-Z]|Serp"),
    ("creator-business", r"Creator|Insight|Analytic|Professional|Business|Partnership|Collab|Affiliate|Brand"),
    ("integrity-safety", r"Teen|Minor|Parental|Guardian|Safety|Integrity|Report|Restrict|Sensitive|AgeVerif|Abuse|Block|Mute|Hide|Warning|Moderat"),
    ("privacy-regulatory", r"Consent|Gdpr|GDPR|DMA|DSA|Regulat|Privacy|Cookie|Jurisdic|Compliance|DataPolicy|OptOut"),
    ("growth-lifecycle", r"Onboard|Nux|NUX|Login|Signup|Signin|Registration|Growth|Invite|Reactivat|Notification|Push|Email|Reengag|Retention|Referral"),
    ("social-graph", r"Follow|Friend|Group|Community|Event|Marketplace|Page|Profile|Contact|Close(Friends)?"),
    ("stories", r"Stor(y|ies)|Tray|Highlight|Fleet|Note"),
    ("infra-perf", r"QPL|Bootload|Prefetch|Cache|Relay|Perf|Metric|Logger|Scheduler|Worker|Bundle|Resource|Polyfill|ErrorBoundary"),
]
TAXONOMY = [(name, re.compile(rx)) for name, rx in TAXONOMY]

# Modules with these substrings are the ones that *hold* a decision, not just use one.
DECISION_SUFFIX = re.compile(r"(Config|Gating|Gate|Experiment|QE|Flags?|Killswitch|Rollout|Eligib|Policy|Variant|Treatment)$|"
                             r"(Config|Gating|Experiment|Killswitch|Rollout)[A-Z]")


def classify(name: str) -> str:
    for label, rx in TAXONOMY:
        if rx.search(name):
            return label
    return "other"


def _segments(text: str):
    """Split a bundle into (module_name, body) segments.

    Meta emits one __d("Name",...) per module, so slicing at those offsets
    attributes every flag reference to the module that actually reads it.
    """
    marks = sorted(
        [(m.start(), m.group(1)) for m in RE_MODULE.finditer(text)]
        + [(m.start(), m.group(1)) for m in RE_DEFINE.finditer(text)]
    )
    if not marks:
        yield None, text
        return
    if marks[0][0] > 0:
        yield None, text[: marks[0][0]]
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        yield name, text[pos:end]


def _slice_json(text: str, start: int) -> str | None:
    """Return the JSON value that begins at `start` ({ or [), brace-matched."""
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if not closer:
        return None
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def extract(snapshot: str) -> dict:
    modules: dict[str, dict] = {}
    events: Counter = Counter()
    doc_ids: dict[str, str] = {}
    relay_ops: dict[str, str] = {}
    routes: Counter = Counter()
    html_blobs: dict[str, object] = {}
    files = 0

    js_dir = os.path.join(snapshot, "js")
    for root, _, names in os.walk(js_dir):
        for n in sorted(names):
            p = os.path.join(root, n)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            files += 1
            _scan(text, modules, events, doc_ids, relay_ops, routes, source=n)

    html_dir = os.path.join(snapshot, "html")
    for root, _, names in os.walk(html_dir):
        for n in sorted(names):
            p = os.path.join(root, n)
            text = open(p, encoding="utf-8", errors="replace").read()
            files += 1
            _scan(text, modules, events, doc_ids, relay_ops, routes, source=n)
            for key in HTML_JSON_KEYS:
                for m in re.finditer(r'"%s"\s*:\s*' % key, text):
                    j = m.end()
                    if j < len(text) and text[j] in "{[":
                        raw = _slice_json(text, j)
                        if raw:
                            try:
                                html_blobs.setdefault(key, {})
                                blob = json.loads(raw)
                                if isinstance(blob, dict):
                                    html_blobs[key].update(blob)
                            except (ValueError, AttributeError):
                                pass

    gql = os.path.join(snapshot, "graphql.jsonl")
    provider_values: dict[str, object] = {}
    if os.path.exists(gql):
        for line in open(gql, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(line)
                if rec.get("name") and rec.get("doc_id"):
                    doc_ids[rec["doc_id"]] = rec["name"]
                variables = json.loads(rec.get("variables") or "{}")
                for k, v in variables.items():
                    if k.startswith("__relay_internal__pv__"):
                        provider_values[k[len("__relay_internal__pv__"):]] = v
            except (ValueError, TypeError):
                continue

    for name, rec in modules.items():
        rec["area"] = classify(name)
        rec["decision_module"] = bool(DECISION_SUFFIX.search(name))
        for key in ("gkx", "qex", "relay_providers", "sources"):
            rec[key] = sorted(rec[key])

    return {
        "snapshot": os.path.abspath(snapshot),
        "files_scanned": files,
        "modules": modules,
        "events": dict(events.most_common()),
        "doc_ids": doc_ids,
        "relay_operations": relay_ops,
        "relay_provider_values": provider_values,
        "routes": dict(routes.most_common(400)),
        "html_blobs": html_blobs,
    }


def _scan(text, modules, events, doc_ids, relay_ops, routes, source):
    for name, body in _segments(text):
        if name is None:
            name = f"<inline:{source}>"
        rec = modules.setdefault(
            name,
            {"gkx": set(), "qex": set(), "relay_providers": set(), "sources": set(), "gk_candidates": []},
        )
        rec["sources"].add(source)
        for m in RE_GKX.finditer(body):
            rec["gkx"].add(m.group(1))
        for m in RE_GKX_BARE.finditer(body):
            rec["gkx"].add(m.group(1))
        for m in RE_QEX.finditer(body):
            rec["qex"].add(m.group(1))
        for m in RE_QEX_BARE.finditer(body):
            rec["qex"].add(m.group(1))
        for m in RE_RELAY_PV.finditer(body):
            rec["relay_providers"].add(m.group(1))
        for m in RE_EVENT.finditer(body):
            events[m.group(1)] += 1
        ops_here = [m.group(1) for m in RE_RELAY_OP.finditer(body)]
        for m in RE_RELAY_OP.finditer(body):
            relay_ops[m.group(1)] = m.group(2)
        ids_here = [m.group(1) for m in RE_RELAY_ID.finditer(body)]
        # A Relay artifact module holds exactly one params object, so a 1:1 zip
        # is safe here and gives doc_id -> operation name without any traffic.
        for doc, op in zip(ids_here, ops_here):
            doc_ids.setdefault(doc, op)
        for m in RE_DOC_ID.finditer(body):
            doc_ids.setdefault(m.group(1), name)
        if re.search(r"Routes?$|RouteMap|UriMap", name or ""):
            for m in RE_ROUTE.finditer(body):
                routes[m.group(1)] += 1
        # A module that pulls in "gkx" but whose call site we could not parse:
        # keep its short numeric string literals as low-confidence gk ids.
        if '"gkx"' in body and not rec["gkx"]:
            rec["gk_candidates"] = sorted({m.group(1) for m in RE_QUOTED_NUM.finditer(body)})[:12]


# --- reporting --------------------------------------------------------------

def _area_table(modules):
    per_area = defaultdict(lambda: {"modules": 0, "gkx": 0, "qex": 0, "decision": 0})
    for name, rec in modules.items():
        a = per_area[rec["area"]]
        a["modules"] += 1
        a["gkx"] += len(rec["gkx"])
        a["qex"] += len(rec["qex"])
        a["decision"] += 1 if rec["decision_module"] else 0
    return per_area


def report(inv: dict) -> str:
    modules = inv["modules"]
    out = [f"# Flag inventory — {inv['snapshot']}", ""]
    out.append(f"- files scanned: **{inv['files_scanned']}**")
    out.append(f"- modules seen: **{len(modules)}**")
    out.append(f"- distinct GK ids: **{len({g for r in modules.values() for g in r['gkx']})}**")
    out.append(f"- distinct QEX keys: **{len({q for r in modules.values() for q in r['qex']})}**")
    out.append(f"- relay providers: **{len({p for r in modules.values() for p in r['relay_providers']})}**")
    out.append("")

    out.append("## Where the gating lives")
    out.append("")
    out.append("| area | modules | gated (GK) | QEX | decision modules |")
    out.append("|---|---:|---:|---:|---:|")
    for area, a in sorted(_area_table(modules).items(), key=lambda kv: -kv[1]["gkx"]):
        out.append(f"| {area} | {a['modules']} | {a['gkx']} | {a['qex']} | {a['decision']} |")
    out.append("")

    named = sorted({q for r in modules.values() for q in r["qex"]})
    if named:
        out.append("## QuickExperiment keys (readable — the highest-value rows)")
        out.append("")
        for q in named:
            owners = [n for n, r in modules.items() if q in r["qex"]][:3]
            out.append(f"- `{q}` — {', '.join(owners)}")
        out.append("")

    vals = inv.get("relay_provider_values", {})
    provs = sorted({p for r in modules.values() for p in r["relay_providers"]} | set(vals))
    if provs:
        out.append("## Relay provider flags (GK/QE injected into GraphQL)")
        out.append("")
        for p in provs:
            v = vals.get(p, vals.get(p + "relayprovider"))
            out.append(f"- `{p}`" + (f" = `{json.dumps(v)}`" if v is not None else ""))
        out.append("")

    gkx_data = inv.get("html_blobs", {}).get("gkxData") or {}
    if gkx_data:
        on, off = [], []
        for gid, rec in gkx_data.items():
            owners = [n for n, r in modules.items() if gid in r["gkx"]]
            row = f"- `{gid}`" + (f" — {', '.join(owners[:4])}" if owners else " — (no call site in the shipped bundles)")
            (on if (rec or {}).get("result") else off).append(row)
        out.append(f"## Gatekeepers resolved for this account ({len(on)} on / {len(off)} off)")
        out.append("")
        out.append("### On")
        out.append("")
        out.extend(on[:150])
        out.append("")
        out.append("### Off — the ones with a named call site are the live experiments")
        out.append("")
        out.extend([r for r in off if "no call site" not in r][:150])
        out.append("")

    out.append("## Most heavily gated modules")
    out.append("")
    hot = sorted(modules.items(), key=lambda kv: -(len(kv[1]["gkx"]) + len(kv[1]["qex"])))[:40]
    for name, rec in hot:
        if not rec["gkx"] and not rec["qex"]:
            break
        out.append(f"- `{name}` [{rec['area']}] — GK: {', '.join(rec['gkx']) or '—'}"
                   + (f" · QEX: {', '.join(rec['qex'])}" if rec["qex"] else ""))
    out.append("")

    # QEX keys share the ig_/fb_ prefix shape; they are already listed above.
    qex_keys = {q for r in modules.values() for q in r["qex"]}
    ev = {k: v for k, v in inv["events"].items() if k not in qex_keys}
    if ev:
        out.append("## Logged events (what they are measuring)")
        out.append("")
        for name, count in list(ev.items())[:60]:
            out.append(f"- `{name}` ×{count}")
        out.append("")

    if inv["routes"]:
        out.append("## Route patterns")
        out.append("")
        out.append(", ".join(f"`{r}`" for r in list(inv["routes"])[:120]))
        out.append("")
    return "\n".join(out)


def diff(old: dict, new: dict) -> str:
    om, nm = old["modules"], new["modules"]
    added = sorted(set(nm) - set(om))
    removed = sorted(set(om) - set(nm))

    def flags(inv, key):
        found = {f for r in inv["modules"].values() for f in r[key]}
        if key == "relay_providers":
            found |= set(inv.get("relay_provider_values", {}))
        return found

    new_gk = sorted(flags(new, "gkx") - flags(old, "gkx"))
    new_qex = sorted(flags(new, "qex") - flags(old, "qex"))
    new_prov = sorted(flags(new, "relay_providers") - flags(old, "relay_providers"))
    new_events = sorted(set(new["events"]) - set(old["events"]))
    new_ops = sorted(set(new["relay_operations"]) - set(old["relay_operations"]))
    new_routes = sorted(set(new["routes"]) - set(old["routes"]))

    flipped = []
    ov, nv = old.get("relay_provider_values", {}), new.get("relay_provider_values", {})
    for k in sorted(set(ov) & set(nv)):
        if ov[k] != nv[k]:
            flipped.append((k, ov[k], nv[k]))

    def gk_results(inv):
        return {k: (v or {}).get("result")
                for k, v in (inv.get("html_blobs", {}).get("gkxData") or {}).items()}

    og, ng = gk_results(old), gk_results(new)
    gk_flips = [(k, og[k], ng[k]) for k in sorted(set(og) & set(ng)) if og[k] != ng[k]]

    out = [f"# Snapshot diff", "", f"- old: `{old['snapshot']}`", f"- new: `{new['snapshot']}`", ""]

    def section(title, items, fmt=lambda x: f"- `{x}`"):
        out.append(f"## {title} ({len(items)})")
        out.append("")
        out.extend(fmt(i) for i in items[:200])
        if len(items) > 200:
            out.append(f"- … and {len(items) - 200} more")
        out.append("")

    section("New modules", added,
            lambda n: f"- `{n}` [{nm[n]['area']}]"
                      + (f" — GK {', '.join(nm[n]['gkx'])}" if nm[n]["gkx"] else ""))
    section("Gatekeepers that flipped", gk_flips,
            lambda t: f"- `{t[0]}`: {t[1]} → {t[2]}"
                      + (" — " + ", ".join(n for n, r in nm.items() if t[0] in r["gkx"])[:160]))
    section("New GK ids", new_gk,
            lambda g: f"- `{g}` — in " + ", ".join(n for n, r in nm.items() if g in r["gkx"])[:160])
    section("New QEX keys", new_qex)
    section("New relay providers", new_prov)
    section("Provider values that flipped", flipped,
            lambda t: f"- `{t[0]}`: `{json.dumps(t[1])}` → `{json.dumps(t[2])}`")
    section("New GraphQL operations", new_ops)
    section("New logged events", new_events)
    section("New routes", new_routes)
    section("Modules that disappeared", removed)
    return "\n".join(out)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "extract":
        inv = extract(argv[2])
        path = os.path.join(argv[2], "inventory.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inv, fh, indent=1, sort_keys=True)
        print(f"{path}: {len(inv['modules'])} modules, {inv['files_scanned']} files", file=sys.stderr)
        return 0
    if cmd == "report":
        inv = _load(argv[2])
        print(report(inv))
        return 0
    if cmd == "diff":
        print(diff(_load(argv[2]), _load(argv[3])))
        return 0
    print(__doc__)
    return 2


def _load(p):
    if os.path.isdir(p):
        p = os.path.join(p, "inventory.json")
    if not os.path.exists(p):
        sys.exit(f"no inventory at {p} — run `mfr.py extract` first")
    return json.load(open(p, encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
