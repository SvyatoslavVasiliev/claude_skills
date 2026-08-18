# meta-flag-recon

Extracts experiment / gating signal from the JavaScript that instagram.com and
facebook.com serve to a browser, and groups it into something you can read as a
product roadmap rather than a symbol dump.

## Why it needs two steps

Meta compiles gatekeepers down to opaque numeric ids — `gkx.get(28174)` — so the
ids on their own tell you nothing. What survives minification is the module
wrapper: `__d("PolarisReelsAutoAdvanceGating", ...)`. Attributing every id to the
modules that read it is what turns an anonymous number into a legible bet. That
join is the whole point of `extract.py`.

Three sources get combined:

| source | what it gives you |
| --- | --- |
| `__d()` module names | readable names for surfaces under active gating |
| `gkx` / `qex` call sites | which switches each surface is wired to |
| `gkxData` / `qexData` in the HTML bootstrap | which of those switches are **on** for your session |
| `/data/shared_data/` (Instagram) | quick experiments with readable names, groups and params |

## Usage

```bash
npm install                       # playwright-core; Chromium is already on the box
node collect.mjs --site ig --out out/ig
python3 extract.py out/ig --label instagram
```

Same for `--site fb`. Each run writes `report.md` (the readable summary) and
`flags.json` (everything, for your own querying) into the collection dir.

### Logged-in collection

Logged out you get the marketing shell and a fraction of the chunks. To capture
the real surface, produce a session state once:

```bash
node collect.mjs --site ig --state ig-state.json --login   # log in, press Enter
node collect.mjs --site ig --state ig-state.json --out out/ig
```

`--login` needs a display, so run it on your own machine, not in a headless
container. Treat the resulting `*-state.json` as a credential — it is one.

## Reading the output

- **Gatekeepers ON for this session** — the live experiment assignment for *your*
  account. Different accounts land in different buckets, so collect from a few to
  tell a launched feature apart from a narrow test.
- **Densest gating modules** — where the most switches converge. Heavy gating
  means a surface actively being reshaped; that is where the current bets are.
- **Gating modules by product area** — the roadmap view.
- **Readable flag names** — the highest-signal entries, since someone chose to
  ship those strings uncompiled.

## Caveats

- Only reads what a browser is already served; nothing here bypasses auth.
  Automated collection against a logged-in account still runs against Meta's
  terms of service — that is your call to make.
- Flag *names* are hypotheses about intent, not confirmation. A flag can be dead
  code, a killswitch for something long-since launched, or a rename. Corroborate
  before drawing conclusions.
- Bundle hashes rotate constantly; treat every collection as a point-in-time
  snapshot and re-run rather than comparing against stale output.

## fixture/

Hand-written synthetic input in Meta's bundle *shape*, used to verify the
extractor end to end. Not real data — never read findings out of it.
