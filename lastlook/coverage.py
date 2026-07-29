"""coverage.py — campaign-preflight merge-tag coverage report.

Reads a normalized campaign JSON and reports, for every merge variable used in
the templates, how well it fills across the lead list: resolved / blank / missing.

This is the proactive view of the blank-merge problem. Instead of finding one
empty {{title}} at a time, it says up front: "title: 0% filled, 640 missing" —
the exact class of bug that shipped live in a real campaign. Low fill rate on a
variable is the single best predictor that a campaign will send broken.

Reuses render.py's resolution logic (lead_vars, key normalization, SYSTEM_VARS)
so "resolved" here means exactly what the renderer would substitute.

Usage:
    python3 coverage.py --in preflight_acme.json
    python3 coverage.py --in preflight_acme.json --min-fill 90
"""

import argparse
import csv
import json
import re
import sys

from .render import VAR_RE, SYSTEM_VARS, lead_vars, _norm_key

# tags that are not lead-data variables and shouldn't be scored for fill
NON_DATA = SYSTEM_VARS | {"random"}


def template_tags(campaign):
    """Distinct merge variables used anywhere in the campaign's templates.
    Returns {norm_key: display_name} so we match normalized but report readable."""
    tags = {}
    for step in campaign.get("steps", []):
        for v in step.get("variants", []):
            text = (v.get("subject") or "") + "\n" + (v.get("body") or "")
            for m in VAR_RE.finditer(text):
                name = m.group(1).strip()
                key = _norm_key(name)
                if key and key not in NON_DATA:
                    tags.setdefault(key, name)
    return tags


def score(campaign, tags):
    leads = campaign.get("leads", [])
    n = len(leads)
    rows = []
    for key, name in tags.items():
        resolved = blank = missing = 0
        for lead in leads:
            vmap = lead_vars(lead)
            if key not in vmap:
                missing += 1
            elif vmap[key].strip() == "":
                blank += 1
            else:
                resolved += 1
        fill = (resolved / n * 100) if n else 0.0
        rows.append({"variable": name, "fill_pct": round(fill, 1),
                     "resolved": resolved, "blank": blank, "missing": missing,
                     "total": n})
    rows.sort(key=lambda r: r["fill_pct"])  # worst first
    return rows


def report(campaign, min_fill=None, out=None):
    """Print the coverage table. Returns 0 always — coverage informs, never gates."""
    if min_fill is None:
        min_fill = 95.0
    tags = template_tags(campaign)
    rows = score(campaign, tags)
    n = len(campaign.get("leads", []))

    print("=" * 64)
    print(f"MERGE-TAG COVERAGE — {campaign.get('campaign', {}).get('name', '')} ({n} leads)")
    print("=" * 64)
    if not rows:
        print("No data merge variables found in templates.")
    else:
        print(f"{'variable':<26} {'fill%':>6} {'resolved':>9} {'blank':>6} {'missing':>8}")
        risky = 0
        for r in rows:
            flag = "!!" if r["fill_pct"] < min_fill else "  "
            risky += r["fill_pct"] < min_fill
            print(f"{flag} {r['variable']:<24} {r['fill_pct']:>5}% {r['resolved']:>9} "
                  f"{r['blank']:>6} {r['missing']:>8}")
        print("=" * 64)
        print(f"{risky} of {len(rows)} variables fill below {min_fill:g}% "
              f"— these are where the campaign will send broken.")
    if out:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["variable", "fill_pct", "resolved",
                                              "blank", "missing", "total"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nFull coverage -> {out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Per-variable merge-tag fill-rate report.")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outfile", default=None)
    ap.add_argument("--min-fill", type=float, default=95.0,
                    help="flag variables filling below this %% (default 95)")
    args = ap.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        campaign = json.load(f)

    tags = template_tags(campaign)
    rows = score(campaign, tags)
    n = len(campaign.get("leads", []))

    print("=" * 64)
    print(f"MERGE-TAG COVERAGE — {campaign.get('campaign', {}).get('name', '')} "
          f"({n} leads)")
    print("=" * 64)
    if not rows:
        print("No data merge variables found in templates.")
    else:
        print(f"{'variable':<26} {'fill%':>6} {'resolved':>9} {'blank':>6} {'missing':>8}")
        risky = 0
        for r in rows:
            flag = "🔴" if r["fill_pct"] < args.min_fill else "  "
            if r["fill_pct"] < args.min_fill:
                risky += 1
            print(f"{flag} {r['variable']:<24} {r['fill_pct']:>5}% {r['resolved']:>9} "
                  f"{r['blank']:>6} {r['missing']:>8}")
        print("=" * 64)
        print(f"{risky} of {len(rows)} variables fill below {args.min_fill:g}% "
              f"— these are where the campaign will send broken.")

    out = args.outfile or re.sub(r"\.json$", "", args.infile) + ".coverage.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["variable", "fill_pct", "resolved", "blank", "missing", "total"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nFull coverage -> {out}")


if __name__ == "__main__":
    main()
