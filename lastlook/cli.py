"""lastlook — see every message a cold campaign would send, before it sends.

    lastlook audit instantly --campaign "Q3 ACME" --key $K
    lastlook pull instantly --campaign "Q3 ACME" --key $K -o campaign.json
    lastlook render campaign.json -o rendered.jsonl
    lastlook check rendered.jsonl --campaign-json campaign.json
    lastlook coverage campaign.json
    lastlook rules

Exit codes: 0 clear, 1 warnings only, 2 blockers, 3 tool error.
"""

import argparse
import csv
import json
import os
import sys

from . import check, recap, render
from .validate import CampaignError, validate

EXIT_CLEAR, EXIT_WARN, EXIT_BLOCK, EXIT_ERROR = 0, 1, 2, 3


def _die(msg):
    print(f"lastlook: {msg}", file=sys.stderr)
    raise SystemExit(EXIT_ERROR)


def _load_campaign(path):
    try:
        with open(path, encoding="utf-8") as f:
            campaign = json.load(f)
    except FileNotFoundError:
        _die(f"no such file: {path}")
    except json.JSONDecodeError as e:
        _die(f"{path} is not valid JSON: {e}")
    try:
        return validate(campaign)
    except CampaignError as e:
        _die(f"{path}: {e}\n"
             f"       The expected shape is lastlook/schema/campaign.schema.json")


def _adapter(name):
    if name == "instantly":
        from .adapters import instantly
        return instantly
    if name == "heyreach":
        from .adapters import heyreach
        return heyreach
    _die(f"unknown platform {name!r}. Known: instantly, heyreach.")


def _key_for(platform, explicit):
    env = {"instantly": "INSTANTLY_API_KEY", "heyreach": "HEYREACH_API_KEY"}[platform]
    key = explicit or os.environ.get(env)
    if not key:
        _die(f"no API key. Pass --key or set {env}.")
    return key


def _forbidden(arg):
    if not arg:
        return []
    if os.path.exists(arg):
        with open(arg, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return [t.strip() for t in arg.split(",") if t.strip()]


def _render_rows(campaign):
    rows = list(render.iter_rendered(campaign))
    if not rows:
        # Reporting CLEAR on zero messages is the worst thing this tool can do:
        # it reads as a pass. Always an error, never a verdict.
        _die("rendered 0 messages — the campaign has no live variants or no leads. "
             "Nothing was checked.")
    return rows


def _write_findings(findings, path):
    cols = ["severity", "check", "step", "variant", "channel",
            "lead_id", "lead_email", "evidence"]
    order = {check.BLOCKER: 0, check.WARNING: 1}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in sorted(findings, key=lambda r: (order[r["severity"]], r["check"])):
            w.writerow({k: row.get(k, "") for k in cols})


def _run_checks(rows, campaign, args):
    try:
        enabled = check.resolve_enabled(
            disable=[s.strip() for s in (args.disable or "").split(",") if s.strip()],
            only=[s.strip() for s in (args.only or "").split(",") if s.strip()])
    except ValueError as e:
        _die(str(e))
    findings = check.run(
        rows, check.load_spam_words(getattr(args, "spam_words", None)),
        campaign_json=campaign,
        instantly_key=getattr(args, "instantly_key", None),
        check_links=getattr(args, "check_links", False),
        forbidden_terms=_forbidden(getattr(args, "forbidden_terms", None)),
        enabled=enabled)

    out = getattr(args, "findings_out", None) or "lastlook.findings.csv"
    _write_findings(findings, out)

    issues = check.dedup_issues(findings)
    print(check.verdict_block(rows, findings))
    if not getattr(args, "no_recap", False):
        print(recap.render(issues, rules_run=check.RULES))
    print(f"\nFull findings -> {out}")

    if any(i["severity"] == check.BLOCKER for i in issues):
        return EXIT_BLOCK
    return EXIT_WARN if issues else EXIT_CLEAR


# --- commands -----------------------------------------------------------------

def cmd_pull(args):
    ad = _adapter(args.platform)
    key = _key_for(args.platform, args.key)
    campaign = ad.pull(key, args.campaign, max_leads=args.max_leads)
    out = args.out or "campaign.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(campaign, f, ensure_ascii=False, indent=2)
    nvar = sum(len(s.get("variants", [])) for s in campaign.get("steps", []))
    print(f"{campaign['campaign'].get('name')}: {len(campaign['steps'])} steps, "
          f"{nvar} variants, {len(campaign['leads'])} leads -> {out}")
    return EXIT_CLEAR


def cmd_render(args):
    campaign = _load_campaign(args.campaign_json)
    rows = _render_rows(campaign)
    out = args.out or "rendered.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"rendered {len(rows)} messages -> {out}")
    return EXIT_CLEAR


def cmd_check(args):
    rows = []
    try:
        with open(args.rendered, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    except FileNotFoundError:
        _die(f"no such file: {args.rendered}")
    if not rows:
        _die(f"{args.rendered} is empty — nothing was checked.")
    campaign = _load_campaign(args.campaign_json) if args.campaign_json else None
    return _run_checks(rows, campaign, args)


def cmd_audit(args):
    ad = _adapter(args.platform)
    key = _key_for(args.platform, args.key)
    campaign = ad.pull(key, args.campaign, max_leads=args.max_leads)
    if args.platform == "instantly" and not getattr(args, "instantly_key", None):
        args.instantly_key = key
    rows = _render_rows(campaign)
    nvar = sum(len(s.get("variants", [])) for s in campaign.get("steps", []))
    print(f"{campaign['campaign'].get('name')}: {len(campaign['steps'])} steps, "
          f"{nvar} variants, {len(campaign['leads'])} leads")
    print(f"rendered {len(rows)} messages")
    return _run_checks(rows, campaign, args)


def cmd_coverage(args):
    from . import coverage
    campaign = _load_campaign(args.campaign_json)
    return coverage.report(campaign, min_fill=args.min_fill)


def cmd_rules(args):
    width = max(len(r) for r in check.RULES)
    print(f"{len(check.RULES)} rules\n")
    for name, desc in check.RULES.items():
        print(f"  {name:<{width}}  {desc}")
    return EXIT_CLEAR


# --- argument wiring ----------------------------------------------------------

def _add_check_flags(p):
    p.add_argument("--forbidden-terms", default=None,
                   help="comma-separated terms, or a path to a newline-delimited file")
    p.add_argument("--spam-words", default=None, help="extra spam words, one per line")
    p.add_argument("--check-links", action="store_true", help="probe every URL in the copy")
    p.add_argument("--disable", default="", help="comma-separated rules to skip")
    p.add_argument("--only", default="", help="comma-separated rules to run exclusively")
    p.add_argument("--no-recap", action="store_true", help="verdict table only")
    p.add_argument("-o", "--findings-out", default=None, help="findings CSV path")


def build_parser():
    ap = argparse.ArgumentParser(prog="lastlook", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("pull", help="fetch a live campaign into normalized JSON")
    p.add_argument("platform", choices=["instantly", "heyreach"])
    p.add_argument("--campaign", required=True, help="campaign name or id")
    p.add_argument("--key", default=None)
    p.add_argument("--max-leads", type=int, default=None)
    p.add_argument("-o", "--out", default=None)
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("render", help="render every variant x lead message")
    p.add_argument("campaign_json")
    p.add_argument("-o", "--out", default=None)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("check", help="run the rules over rendered messages")
    p.add_argument("rendered")
    p.add_argument("--campaign-json", default=None,
                   help="enables the campaign-level rules (list, structure, handoffs)")
    p.add_argument("--instantly-key", default=os.environ.get("INSTANTLY_API_KEY"))
    _add_check_flags(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("audit", help="pull, render and check in one pass")
    p.add_argument("platform", choices=["instantly", "heyreach"])
    p.add_argument("--campaign", required=True)
    p.add_argument("--key", default=None)
    p.add_argument("--max-leads", type=int, default=None)
    p.add_argument("--instantly-key", default=os.environ.get("INSTANTLY_API_KEY"))
    _add_check_flags(p)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("coverage", help="merge-tag fill rate across the lead list")
    p.add_argument("campaign_json")
    p.add_argument("--min-fill", type=float, default=None)
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("rules", help="print the rule catalog")
    p.set_defaults(func=cmd_rules)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return EXIT_CLEAR
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
