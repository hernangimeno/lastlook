"""fleet.py — run campaign-preflight across all of ONE client's live campaigns.

Whole-client mode. Given a manifest of a single client's active campaigns
(across Instantly and HeyReach), it pulls, renders, and checks each one, then
prints a ranked summary — worst campaigns first — so you can see at a glance
which of a client's live campaigns are shipping broken.

The assistant builds the manifest from Airtable (client -> campaigns + keys);
this script just executes the fleet and aggregates. Leads are sampled
(--max-leads, default 200) so a many-campaign scan stays fast; any campaign that
flags can be re-run in full with the single-campaign pipeline.

Manifest JSON: [{"platform":"instantly"|"heyreach","campaign":"<id>","key":"<api key>","name":"..."}]

Usage:
    python3 fleet.py --manifest /tmp/globex_manifest.json --client Globex --max-leads 200
"""

import argparse
import csv
import json
import sys

from .adapters import instantly as pull_instantly
from .adapters import heyreach as pull_heyreach
from . import render
from . import check

BLOCKER = check.BLOCKER


def scan_one(entry, max_leads):
    platform = entry["platform"]
    if platform == "instantly":
        norm = pull_instantly.pull(entry["key"], entry["campaign"], max_leads)
    elif platform == "heyreach":
        norm = pull_heyreach.pull(entry["key"], entry["campaign"], max_leads)
    else:
        raise ValueError(f"unknown platform {platform}")

    rows = list(render.iter_rendered(norm))
    findings = check.run(rows, check.load_spam_words(None), norm, None)
    issues = check.dedup_issues(findings)
    blk = [i for i in issues if i["severity"] == BLOCKER]
    blockers, warnings = len(blk), len(issues) - len(blk)
    nleads = len(norm.get("leads", []))
    # UNDEFINED_TAG lives in the template, so it hits EVERY lead on that step —
    # not the 1 "(campaign-level)" pseudo-lead. Count its impact as the full audience.
    template_level = any(i["check"] == "UNDEFINED_TAG" for i in blk)
    # Distinct leads, not (lead, variant) pairs — same counting rule as
    # check.verdict_block, and for the same reason: a lead receives one variant.
    per_lead = {f["lead_id"] for f in findings
                if f["severity"] == BLOCKER and f["lead_id"] != "(campaign-level)"}
    leads_broken = nleads if template_level else len(per_lead)
    top = ""
    if blk:
        t = next((i for i in blk if i["check"] == "UNDEFINED_TAG"), blk[0])
        top = t["evidence"][:72] if t["check"] == "UNDEFINED_TAG" else f'{t["check"]} (~{t["leads"]} leads)'
    return {
        "name": norm["campaign"]["name"] or entry.get("name", ""),
        "platform": platform, "leads": len(norm.get("leads", [])),
        "messages": len(rows), "blockers": blockers, "warnings": warnings,
        "leads_broken": leads_broken, "top_blocker": top,
        "verdict": "NOT CLEAR" if blockers else ("CAUTION" if warnings else "CLEAR"),
    }


def main():
    ap = argparse.ArgumentParser(description="Preflight a whole client's live campaigns.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--client", default="")
    ap.add_argument("--max-leads", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        entries = json.load(f)

    results = []
    for e in entries:
        label = f'{e["platform"]}:{e.get("name", e["campaign"])}'
        try:
            r = scan_one(e, args.max_leads)
        except Exception as ex:
            r = {"name": e.get("name", e["campaign"]), "platform": e["platform"],
                 "leads": 0, "messages": 0, "blockers": 0, "warnings": 0,
                 "leads_broken": 0, "top_blocker": f"ERROR: {type(ex).__name__}: {ex}",
                 "verdict": "ERROR"}
        results.append(r)
        print(f"  scanned {label}: {r['verdict']} "
              f"({r['blockers']}B/{r['warnings']}W, {r['leads_broken']}/{r['leads']} broken)",
              file=sys.stderr)

    rank = {"NOT CLEAR": 0, "CAUTION": 1, "ERROR": 2, "CLEAR": 3}
    results.sort(key=lambda r: (rank[r["verdict"]], -r["leads_broken"]))

    title = f"FLEET PREFLIGHT — {args.client}" if args.client else "FLEET PREFLIGHT"
    n_nc = sum(1 for r in results if r["verdict"] == "NOT CLEAR")
    print("\n" + "=" * 92)
    print(f"{title}   ({len(results)} live campaigns, ~{args.max_leads} leads sampled each)")
    print("=" * 92)
    print(f"{'verdict':<10}{'plat':<10}{'B':>3}{'W':>4}{'broken':>9}  campaign / top blocker")
    for r in results:
        flag = {"NOT CLEAR": "🔴", "CAUTION": "🟡", "CLEAR": "🟢", "ERROR": "⚠️"}[r["verdict"]]
        print(f"{flag}{r['verdict']:<8}{r['platform']:<10}{r['blockers']:>3}{r['warnings']:>4}"
              f"{r['leads_broken']:>9}  {r['name'][:46]}")
        if r["top_blocker"]:
            print(f"{'':>36}↳ {r['top_blocker']}")
    print("=" * 92)
    print(f"{n_nc} of {len(results)} campaigns are NOT CLEAR (have blockers).")

    out = args.out or f"/tmp/fleet_{args.client or 'scan'}_summary.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["verdict", "platform", "name", "leads", "messages",
                                          "blockers", "warnings", "leads_broken", "top_blocker"])
        w.writeheader()
        w.writerows(results)
    print(f"\nSummary -> {out}")


if __name__ == "__main__":
    main()
