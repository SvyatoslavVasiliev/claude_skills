#!/usr/bin/env python3
"""Asserts the parser against fixtures shaped like real Meta bundles."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A = os.path.join(HERE, "fixtures", "snap-a")
B = os.path.join(HERE, "fixtures", "snap-b")

failures = []


def check(label, cond):
    if not cond:
        failures.append(label)
    print(("ok   " if cond else "FAIL ") + label)


inv_a = json.load(open(os.path.join(A, "inventory.json")))
inv_b = json.load(open(os.path.join(B, "inventory.json")))
mods_b = inv_b["modules"]

check("module names survive minification",
      "PolarisReelsAdsSurfaceRoot" in mods_b)
check("gk id is attributed to the module that reads it",
      "25102" in mods_b["PolarisReelsAdsSurfaceRoot"]["gkx"])
check("qex key is attributed to its module",
      "ig_web_reels_ad_density_v2" in mods_b["PolarisReelsAdsSurfaceRoot"]["qex"])
check("a qex call without a gk still registers",
      "ig_web_direct_inbox_tabs" in mods_b["PolarisDirectThreadListRoot"]["qex"])
check("relay artifact yields doc_id -> operation name",
      inv_b["doc_ids"].get("9876543210123") == "PolarisReelsRootQuery")
check("relay operation kind is captured",
      inv_b["relay_operations"].get("PolarisAIComposerQuery") == "query")
check("relay provider values come from observed graphql traffic",
      inv_b["relay_provider_values"].get("PolarisAIComposerEnabledrelayprovider") is True)
check("gkxData from the html document is parsed",
      inv_b["html_blobs"]["gkxData"]["40199"]["result"] is True)
check("routes are collected from route modules",
      "/ai/studio/" in inv_b["routes"])
check("logged events are collected",
      "instagram_web_ai_composer_open" in inv_b["events"])
check("taxonomy: ai module lands in the ai bucket",
      mods_b["PolarisMetaAIComposerEntrypoint"]["area"] == "ai")
check("taxonomy: ads module lands in ads-monetization",
      mods_b["PolarisReelsAdsSurfaceRoot"]["area"] == "ads-monetization")
check("taxonomy: teen gating lands in integrity-safety",
      mods_b["PolarisTeenAccountDefaultsGating"]["area"] == "integrity-safety")
check("config/gating modules are marked as decision modules",
      mods_b["PolarisFeedRankingConfig"]["decision_module"] is True)
check("a plain surface module is not marked as a decision module",
      mods_b["PolarisDirectThreadListRoot"]["decision_module"] is False)

diff = subprocess.run([sys.executable, os.path.join(ROOT, "mfr.py"), "diff", A, B],
                      capture_output=True, text=True, check=True).stdout
check("diff reports the new ai module", "PolarisMetaAIComposerEntrypoint" in diff)
check("diff reports a flipped gatekeeper", "`31007`: False → True" in diff)
check("diff reports the new route", "/ai/studio/" in diff)
check("diff does not invent removals", "Modules that disappeared (0)" in diff)

report = subprocess.run([sys.executable, os.path.join(ROOT, "mfr.py"), "report", B],
                        capture_output=True, text=True, check=True).stdout
check("report separates gatekeepers that are on", "Gatekeepers resolved for this account" in report)
check("report lists readable qex keys", "ig_web_ai_composer_suggestions" in report)
check("report does not repeat qex keys as events",
      report.count("`ig_web_ai_composer_suggestions`") == 1)

probe = subprocess.run(
    [sys.executable, os.path.join(ROOT, "probe-diff.py"),
     os.path.join(HERE, "fixtures", "probes", "a.json"),
     os.path.join(HERE, "fixtures", "probes", "b.json"),
     os.path.join(B, "inventory.json")],
    capture_output=True, text=True, check=True).stdout
check("probe-diff finds the account split", "`31007`: false vs true" in probe)
check("probe-diff names the owning module", "PolarisFeedRankingConfig" in probe)
check("probe-diff ignores flags that agree", "25102" not in probe)

print()
if failures:
    print(f"{len(failures)} failing check(s)")
    sys.exit(1)
print("all checks passed")
