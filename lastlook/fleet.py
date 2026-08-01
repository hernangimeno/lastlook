"""fleet.py — audit every campaign in a manifest, worst first.

Whole-account mode. Given a manifest of campaigns (across Instantly and
HeyReach), it pulls, renders and checks each one, then prints a ranked summary —
worst campaigns first — so you can see at a glance which of your live campaigns
are shipping broken.

Leads are sampled (--max-leads, default 200) so a many-campaign scan stays fast;
any campaign that flags can be re-run in full with `lastlook audit`.

Manifest JSON, one object per campaign:

    [
      {"platform": "instantly", "campaign": "Q3 Outbound",
       "key_env": "ACME_INSTANTLY_KEY", "name": "Q3"},
      {"platform": "heyreach", "campaign": "12345",
       "key_env": "ACME_HEYREACH_KEY", "name": "LI"}
    ]

Usage:
    lastlook fleet --manifest manifest.json --label acme --max-leads 200
"""

import csv
import json
import os
import sys

from .adapters import instantly as pull_instantly
from .adapters import heyreach as pull_heyreach
from . import render
from . import check

BLOCKER = check.BLOCKER
ENV_VAR = {"instantly": "INSTANTLY_API_KEY", "heyreach": "HEYREACH_API_KEY"}


def key_for_entry(entry):
    """Resolve a fleet key without requiring secrets in the manifest.

    `key` remains supported for compatibility, but `key_env` (or the platform's
    standard environment variable) keeps credentials out of a file users are
    likely to commit next to their project.
    """
    if entry.get("key"):
        return str(entry["key"]).strip()
    platform = entry.get("platform")
    env_name = entry.get("key_env") or ENV_VAR.get(platform)
    if not env_name:
        raise ValueError(f"unknown platform {platform!r}")
    key = os.environ.get(env_name)
    if not key:
        raise ValueError(f"missing API key: set ${env_name} or add key_env to the manifest")
    return key.strip()


def scan_one(entry, max_leads):
    platform = entry["platform"]
    api_key = key_for_entry(entry)
    if platform == "instantly":
        norm = pull_instantly.pull(api_key, entry["campaign"], max_leads)
    elif platform == "heyreach":
        norm = pull_heyreach.pull(api_key, entry["campaign"], max_leads)
    else:
        raise ValueError(f"unknown platform {platform}")

    rows = list(render.iter_rendered(norm))
    if not rows:
        raise ValueError("rendered 0 messages — nothing was checked")
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


def run(args):
    """The `lastlook fleet` body. args needs: manifest, label, max_leads, out."""
    try:
        with open(args.manifest, encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        print(f"lastlook: no such manifest: {args.manifest}", file=sys.stderr)
        return 3
    except json.JSONDecodeError as e:
        print(f"lastlook: {args.manifest} is not valid JSON: {e}", file=sys.stderr)
        return 3
    if not isinstance(entries, list) or not entries:
        print(f"lastlook: {args.manifest} must be a non-empty list of campaigns. "
              f"Nothing was checked.", file=sys.stderr)
        return 3
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            print(f"lastlook: manifest entry {i} must be an object. Nothing was checked.",
                  file=sys.stderr)
            return 3
        missing = [k for k in ("platform", "campaign") if not e.get(k)]
        if missing:
            print(f"lastlook: manifest entry {i} is missing {', '.join(missing)}. "
                  f"Nothing was checked.", file=sys.stderr)
            return 3
        if e.get("key"):
            print(f"lastlook: manifest entry {i} contains a plaintext API key. "
                  f"Prefer key_env so the secret is not committed with the manifest.",
                  file=sys.stderr)

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

    title = f"FLEET AUDIT — {args.label}" if args.label else "FLEET AUDIT"
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

    out = args.out or f"lastlook.fleet.{args.label or 'scan'}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["verdict", "platform", "name", "leads", "messages",
                                          "blockers", "warnings", "leads_broken", "top_blocker"])
        w.writeheader()
        w.writerows(results)
    print(f"\nSummary -> {out}")

    # Same exit-code contract as every other command: 2 if anything blocks, 3 if
    # a campaign errored and was therefore NOT checked, 1 for warnings only.
    if any(r["verdict"] == "ERROR" for r in results):
        return 3
    if n_nc:
        return 2
    return 1 if any(r["warnings"] for r in results) else 0
