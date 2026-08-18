#!/usr/bin/env python3
"""probe-diff.py A.json B.json — flags that differ between two probed sessions.

A flag whose value differs across two accounts on the same build is, by
definition, in an active experiment rather than a finished rollout. That
distinction is the whole point of probing more than one profile.
"""
import json, sys

def load(p):
    return json.load(open(p, encoding="utf-8"))

def main(a_path, b_path, inv_path=None):
    a, b = load(a_path), load(b_path)
    owners = {}
    if inv_path:
        inv = load(inv_path)
        for name, rec in inv["modules"].items():
            for g in rec["gkx"]:
                owners.setdefault(g, []).append(name)
            for q in rec["qex"]:
                owners.setdefault(q, []).append(name)

    for kind in ("gkx", "qex"):
        rows = []
        for key in sorted(set(a.get(kind, {})) & set(b.get(kind, {}))):
            if a[kind][key] != b[kind][key]:
                who = ", ".join(owners.get(key, [])[:3])
                rows.append(f"- `{key}`: {json.dumps(a[kind][key])} vs {json.dumps(b[kind][key])}"
                            + (f" — {who}" if who else ""))
        print(f"## {kind} split between the two sessions ({len(rows)})\n")
        print("\n".join(rows) or "- none")
        print()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
