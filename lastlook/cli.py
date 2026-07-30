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

from . import check, fix as fixmod, recap, render
from .validate import CampaignError, validate

EXIT_CLEAR, EXIT_WARN, EXIT_BLOCK, EXIT_ERROR = 0, 1, 2, 3

__version__ = "0.1.0"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "schema", "campaign.schema.json")


def _explain(exc):
    """Turn a platform failure into something a human can act on.

    Anything that reaches here used to surface as a raw httpx traceback and,
    worse, exited 1 — which this tool documents as "warnings only". A rejected
    key reading as a passing campaign is the most dangerous thing the CLI could
    do, so every one of these ends at exit 3.
    """
    import httpx

    if isinstance(exc, LookupError):
        # Adapters raise this with a message already written for a human
        # (campaign not found, list not found). No prefix, no traceback.
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        host = exc.request.url.host
        hint = {
            401: "the API key was rejected — check it is current and pasted whole",
            403: "the key is valid but lacks permission for this call",
            402: "the platform is refusing on billing/plan grounds, not on the key",
            404: "no campaign with that id or name on this account",
            429: "rate limited — wait and retry",
        }.get(code, "unexpected response")
        extra = ""
        if code in (401, 403):
            plat = "instantly" if "instantly" in host else "heyreach"
            extra = f"\n       Get a fresh one: {WHERE_TO_GET.get(plat, '')}"
        return f"{host} returned HTTP {code} — {hint}.{extra}"
    if isinstance(exc, httpx.ConnectError):
        return f"could not reach {exc.request.url.host} — check your network."
    if isinstance(exc, httpx.TimeoutException):
        return f"{exc.request.url.host} timed out."
    return f"{type(exc).__name__}: {exc}"


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
             f"       The expected shape is {SCHEMA_PATH}")


def _adapter(name):
    if name == "instantly":
        from .adapters import instantly
        return instantly
    if name == "heyreach":
        from .adapters import heyreach
        return heyreach
    _die(f"unknown platform {name!r}. Known: instantly, heyreach.")


WHERE_TO_GET = {
    "instantly": "Instantly → Settings → Integrations → API Key",
    "heyreach":  "HeyReach → Settings → API keys",
}
ENV_VAR = {"instantly": "INSTANTLY_API_KEY", "heyreach": "HEYREACH_API_KEY"}


def _load_dotenv():
    """Read KEY=value from ./.env if present. Real env vars always win.

    No python-dotenv dependency: this is twelve lines and the alternative is
    making everyone install a package to avoid putting an API key in their shell
    history."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def _prompt_for_key(platform):
    """Ask for the key, but ONLY when a human is actually there to answer.

    Guarded on stdin AND stderr being a TTY. A prompt in a cron job, a CI step,
    or behind a pipe does not get answered — it hangs forever, and a hung job is
    far worse than a clear error. Non-interactive callers fall through to the
    message and exit 3.
    """
    import getpass

    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return None
    print(f"No {platform} API key found.", file=sys.stderr)
    print(f"  Get one: {WHERE_TO_GET[platform]}", file=sys.stderr)
    try:
        # getpass, so the key is not echoed and does not land in a scrollback
        # buffer someone screen-shares later.
        key = getpass.getpass(f"  Paste your {platform} API key (input hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return None
    if not key:
        return None
    _offer_to_save(platform, key)
    return key


def _offer_to_save(platform, key):
    """Offer to persist it, so nobody pastes the same key every run."""
    path = os.path.join(os.getcwd(), ".env")
    try:
        answer = input(f"  Save to {path} so you do not need to paste it again? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return
    if answer.strip().lower() not in ("y", "yes"):
        return
    line = f"{ENV_VAR[platform]}={key}\n"
    try:
        existing = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if ENV_VAR[platform] in existing:
            print(f"  {ENV_VAR[platform]} is already in {path}; not touching it.", file=sys.stderr)
            return
        with open(path, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(line)
        os.chmod(path, 0o600)
        print(f"  Saved. {path} is chmod 600 — make sure your .gitignore covers it.",
              file=sys.stderr)
    except OSError as e:
        print(f"  Could not write {path}: {e}", file=sys.stderr)


def _key_for(platform, explicit):
    env = ENV_VAR.get(platform)
    if env is None:
        # A hand-written campaign JSON can carry any platform string; without
        # this guard it surfaced as a raw KeyError instead of a sentence.
        _die(f"unknown platform {platform!r} — lastlook has API keys for: "
             f"{', '.join(ENV_VAR)}")
    key = explicit or os.environ.get(env)
    if not key:
        key = _prompt_for_key(platform)
    if not key:
        _die(f"no {platform} API key.\n"
             f"       Get one:  {WHERE_TO_GET[platform]}\n"
             f"       Then either:\n"
             f"         export {env}=...        (or put it in a .env file here)\n"
             f"         lastlook ... --key ...   (visible in your shell history AND to "
             f"anyone who can run ps)")
    return key.strip().strip("\"'")


def _forbidden(arg):
    if not arg:
        return []
    if os.path.exists(arg):
        with open(arg, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    # A mistyped path used to be read as a literal banned term, so the rule ran
    # against the word "./terms.txt" and reported the campaign clean. Anything
    # shaped like a path has to exist.
    if "/" in arg or arg.lower().endswith((".txt", ".csv")):
        _die(f"no such forbidden-terms file: {arg}\n"
             f"       Pass a comma-separated list, or a path that exists.")
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
        # Upper-cased for the user: rule names are shouted in the catalog, and
        # `--only em_dash` failing on case alone is a pointless wall.
        enabled = check.resolve_enabled(
            disable=[s.strip().upper() for s in (args.disable or "").split(",") if s.strip()],
            only=[s.strip().upper() for s in (args.only or "").split(",") if s.strip()])
    except ValueError as e:
        _die(str(e))
    forbidden = _forbidden(getattr(args, "forbidden_terms", None))
    check_links = getattr(args, "check_links", False)
    findings = check.run(
        rows, check.load_spam_words(getattr(args, "spam_words", None)),
        campaign_json=campaign,
        instantly_key=getattr(args, "instantly_key", None),
        check_links=check_links,
        forbidden_terms=forbidden,
        enabled=enabled)
    ran, skipped = check.rules_actually_run(
        enabled=enabled, campaign_json=campaign, check_links=check_links,
        forbidden_terms=forbidden)

    issues = check.dedup_issues(findings)
    print(check.verdict_block(rows, findings))
    if not getattr(args, "no_recap", False):
        # rules_run is what RAN, never the catalog: see check.rules_actually_run.
        print(recap.render(issues, rules_run=sorted(ran), skipped=skipped,
                           campaign_path=getattr(args, 'campaign_json', None),
                           fixable_rules=fixmod.fixable_rules(campaign) if campaign else None))
    elif skipped:
        # --no-recap suppresses the recap, not the coverage hole.
        print(f"\nNOT CHECKED: {len(skipped)} rule(s) did not run "
              f"(drop --no-recap for the breakdown).")
    # Written AFTER the verdict is on screen. An unwritable -o path used to raise
    # before the verdict printed, so a campaign with blockers exited 3 with the
    # blockers never shown.
    out = getattr(args, "findings_out", None) or "lastlook.findings.csv"
    try:
        _write_findings(findings, out)
        print(f"\nFull findings -> {out}")
    except OSError as e:
        print(f"\nlastlook: could not write the findings CSV to {out}: "
              f"{e.strerror}. The verdict above still stands.", file=sys.stderr)

    if any(i["severity"] == check.BLOCKER for i in issues):
        return EXIT_BLOCK
    return EXIT_WARN if issues else EXIT_CLEAR


# --- commands -----------------------------------------------------------------

def cmd_pull(args):
    ad = _adapter(args.platform)
    key = _key_for(args.platform, args.key)
    campaign = ad.pull(key, args.campaign, max_leads=args.max_leads)
    out = args.out or "campaign.json"
    with _open_out(out) as f:
        json.dump(campaign, f, ensure_ascii=False, indent=2)
    nvar = sum(len(s.get("variants", [])) for s in campaign.get("steps", []))
    print(f"{campaign['campaign'].get('name')}: {len(campaign['steps'])} steps, "
          f"{nvar} variants, {len(campaign['leads'])} leads -> {out}")
    return EXIT_CLEAR


def _open_out(path):
    """Open an output file, or explain why not. A raw FileNotFoundError on -o reads
    as "your input is missing" when the problem is the destination."""
    try:
        return open(path, "w", encoding="utf-8", newline="")
    except IsADirectoryError:
        _die(f"cannot write {path}: that is a directory.")
    except FileNotFoundError:
        _die(f"cannot write {path}: the directory does not exist.")
    except PermissionError:
        _die(f"cannot write {path}: permission denied.")
    except OSError as e:
        _die(f"cannot write {path}: {e.strerror}.")


def cmd_render(args):
    campaign = _load_campaign(args.campaign_json)
    rows = _render_rows(campaign)
    out = args.out or "rendered.jsonl"
    with _open_out(out) as f:
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
    except json.JSONDecodeError as e:
        # The likeliest mistake here by a mile: handing `check` the campaign JSON,
        # since both files end in son and the two commands sit next to each other
        # in the README. It used to surface as a raw JSONDecodeError.
        hint = ""
        try:
            with open(args.rendered, encoding="utf-8") as f:
                if f.read(1) == "{":
                    hint = ("\n       This looks like a campaign JSON. `check` wants the "
                            "rendered .jsonl:\n"
                            f"         lastlook render {args.rendered} -o rendered.jsonl\n"
                            f"         lastlook check rendered.jsonl --campaign-json {args.rendered}")
        except OSError:
            pass
        _die(f"{args.rendered} is not valid JSONL: {e}{hint}")
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


def cmd_fix(args):
    campaign = _load_campaign(args.campaign_json)
    enabled = None
    if args.only_fixes:
        enabled = {t.strip() for t in args.only_fixes.split(",") if t.strip()}
        known = {f[0] for f in fixmod.TEMPLATE_FIXES}
        bad = enabled - known
        if bad:
            _die(f"unknown fix {sorted(bad)}. Known: {sorted(known)}")

    edits = fixmod.plan_template_fixes(campaign, enabled)
    data = fixmod.plan_data_fixes(campaign)
    comms = None
    if args.communities:
        comms = ({ln.strip() for ln in open(args.communities, encoding="utf-8")
                  if ln.strip() and not ln.startswith("#")}
                 if os.path.exists(args.communities)
                 else {t.strip() for t in args.communities.split(",") if t.strip()})
    removals = fixmod.plan_removals(campaign, comms)

    print(fixmod.render_diff(edits))
    if data:
        out = args.data_out or "lastlook.fixes.csv"
        fixmod.write_data_csv(data, out)
        print(f"\n{len(data)} data value(s) to correct -> {out}")
        for r in data[:5]:
            print(f"    {r['field']:<13} {r['current'][:34]!r} -> {r['suggested'][:34]!r}")
        if len(data) > 5:
            print(f"    ... {len(data) - 5} more in the CSV")

    if removals:
        rout = args.removals_out or "lastlook.removals.csv"
        fixmod.write_removals_csv(removals, rout)
        from collections import Counter
        spread = ", ".join(f"{n} {r}" for r, n in Counter(x["reason"] for x in removals).items())
        print(f"\n{len(removals)} lead(s) worth REMOVING rather than correcting -> {rout}")
        print(f"    {spread}")
        for r in removals[:5]:
            who = r["name"] or r["lead_email"] or r["lead_id"]
            print(f"    {who[:24]:<24} {r['evidence'][:66]}")
        if len(removals) > 5:
            print(f"    ... {len(removals) - 5} more in the CSV")
        print("    Suggestions only — lastlook never drops a lead. Exclude them at the source.")

    if not args.apply:
        if edits:
            print(f"\n{len(edits)} template edit(s). Nothing written. "
                  f"Re-run with --apply to push them to the platform.")
        return EXIT_CLEAR

    if not edits:
        print("\nNothing to apply.")
        return EXIT_CLEAR

    # Typed confirmation: --apply mutates a live campaign, and a flag on its own
    # is too easy to reach for from shell history.
    print(f"\nAbout to overwrite {len(edits)} template field(s) in "
          f"'{campaign['campaign'].get('name')}' on {campaign.get('platform')}.")
    if campaign.get("platform") == "heyreach":
        print("HeyReach refuses a sequence write while running, so the campaign will be "
              "PAUSED, updated, then RESUMED. If resume fails you will be told loudly.")
    if not args.yes:
        try:
            if input("Type the campaign name to confirm: ").strip() != campaign["campaign"].get("name"):
                print("Name did not match. Nothing written.")
                return EXIT_CLEAR
        except EOFError:
            _die("--apply needs a terminal to confirm, or pass --yes.")

    key = _key_for(campaign.get("platform"), args.key)
    try:
        n = fixmod.apply_template_fixes(campaign, edits, key, enabled)
    except fixmod.ApplyUnsupported as e:
        _die(str(e))
    except fixmod.WrongCampaign as e:
        # Checked against the live campaign, before any write. Kept separate from
        # the generic handler below, whose "nothing may have been written" is a
        # maybe — this one is a certainty.
        _die(str(e))
    except Exception as e:
        _die(f"apply failed, nothing may have been written: {e}")
    print(f"Applied {n} field(s). Re-run `lastlook audit` to confirm.")
    return EXIT_CLEAR


def cmd_coverage(args):
    from . import coverage
    campaign = _load_campaign(args.campaign_json)
    if not (campaign.get("leads") or []):
        # Same rule as render/check: a pass over nothing is the most dangerous
        # output this tool can produce. 0% fill across 0 leads is not a finding.
        _die("the campaign has no leads — there is nothing to measure coverage over.")
    return coverage.report(campaign, min_fill=args.min_fill)


def cmd_fleet(args):
    from . import fleet
    return fleet.run(args)


def cmd_rules(args):
    width = max(len(r) for r in check.RULES)
    print(f"{len(check.RULES)} rules. "
          f"BLOCKER = do not launch, WARNING = launch with eyes open.\n")
    for name, desc in check.RULES.items():
        sev = check.RULE_SEVERITY.get(name, "")
        print(f"  {name:<{width}}  {sev:<7}  {desc}")
    print("\nRules that need --campaign-json: "
          + ", ".join(sorted(check.CAMPAIGN_ONLY_RULES)))
    return EXIT_CLEAR


# --- argument wiring ----------------------------------------------------------

def _add_check_flags(p):
    p.add_argument("--forbidden-terms", default=None,
                   help="comma-separated terms, or a path to a newline-delimited file")
    p.add_argument("--spam-words", default=None, help="extra spam words, one per line")
    p.add_argument("--check-links", action="store_true", help="probe every URL in the copy")
    p.add_argument("--disable", default="",
                   help="comma-separated rules to skip; the recap lists what did not run")
    p.add_argument("--only", default="",
                   help="comma-separated rules to run exclusively (everything else "
                        "is reported as NOT CHECKED)")
    p.add_argument("--no-recap", action="store_true", help="verdict table only")
    p.add_argument("-o", "--findings-out", default=None,
                   help="findings CSV path (default: lastlook.findings.csv)")


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error. Exit 2 is this tool's "BLOCKERS — do not
    launch", so a typo'd flag was indistinguishable from a campaign that would
    damage your domain. Usage errors are tool errors: exit 3."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"lastlook: {message}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)


def build_parser():
    ap = _Parser(prog="lastlook", description=__doc__,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"lastlook {__version__}")
    sub = ap.add_subparsers(dest="cmd", parser_class=_Parser)

    p = sub.add_parser("pull", help="fetch a live campaign into normalized JSON",
                       description="Fetch a live campaign and write it as normalized JSON. "
                                   "No rendering, no checks.")
    p.add_argument("platform", choices=["instantly", "heyreach"],
                   help="which platform the campaign lives on")
    p.add_argument("--campaign", required=True, help="campaign name or id")
    p.add_argument("--key", default=None,
                   help="API key; defaults to the platform env var, .env, then a prompt")
    p.add_argument("--max-leads", type=int, default=None,
                   help="sample at most N leads (default: every lead)")
    p.add_argument("-o", "--out", default=None, help="output path (default: campaign.json)")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("render", help="render every variant x lead message",
                       description="Render every (step, variant, lead) message exactly as it "
                                   "would send. Writes JSONL, one message per line.")
    p.add_argument("campaign_json", help="campaign JSON from `lastlook pull`")
    p.add_argument("-o", "--out", default=None, help="output path (default: rendered.jsonl)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("check", help="run the rules over rendered messages",
                       description="Run the rules over rendered messages. Pass --campaign-json "
                                   "or the 19 campaign-level rules cannot run.")
    p.add_argument("rendered", help="the .jsonl written by `lastlook render`")
    p.add_argument("--campaign-json", default=None,
                   help="enables the campaign-level rules (list, structure, handoffs)")
    p.add_argument("--instantly-key", default=os.environ.get("INSTANTLY_API_KEY"),
                   help="key for the handoff check (default: $INSTANTLY_API_KEY)")
    _add_check_flags(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("audit", help="pull, render and check in one pass",
                       description="Pull, render and check a live campaign in one pass. "
                                   "The command to reach for by default.")
    p.add_argument("platform", choices=["instantly", "heyreach"],
                   help="which platform the campaign lives on")
    p.add_argument("--campaign", required=True, help="campaign name or id")
    p.add_argument("--key", default=None,
                   help="API key; defaults to the platform env var, .env, then a prompt")
    p.add_argument("--max-leads", type=int, default=None,
                   help="sample at most N leads (default: every lead)")
    p.add_argument("--instantly-key", default=os.environ.get("INSTANTLY_API_KEY"),
                   help="key for the handoff check (default: $INSTANTLY_API_KEY)")
    _add_check_flags(p)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("fix", help="show (and optionally apply) the safe fixes",
                       description="Show the mechanical fixes as a diff. Without --apply "
                                   "nothing is written anywhere.")
    p.add_argument("campaign_json", help="campaign JSON from `lastlook pull`")
    p.add_argument("--apply", action="store_true", help="write template edits to the platform")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.add_argument("--key", default=None)
    p.add_argument("--only-fixes", default=None, help="comma-separated fix ids")
    p.add_argument("--data-out", default=None,
                   help="CSV of suggested value corrections (default: lastlook.fixes.csv)")
    p.add_argument("--removals-out", default=None,
                   help="CSV of leads worth excluding (default: lastlook.removals.csv)")
    p.add_argument("--communities", default=None,
                   help="comma-separated names, or a file: communities enrichment "
                        "mistakes for employers (Pavilion, Exit Five, ...)")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("coverage", help="merge-tag fill rate across the lead list",
                       description="Fill rate per merge tag across the lead list: the "
                                   "proactive view of the blank-merge problem.")
    p.add_argument("campaign_json", help="campaign JSON from `lastlook pull`")
    p.add_argument("--min-fill", type=float, default=None,
                   help="flag variables filling below this %% (default: 95)")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("fleet", help="audit many campaigns from a manifest, worst first",
                       description="Audit every campaign in a manifest and rank them worst "
                                   "first. Leads are sampled; re-run a flagged campaign in "
                                   "full with `lastlook audit`.")
    p.add_argument("--manifest", required=True,
                   help='JSON list of {"platform","campaign","key","name"}')
    p.add_argument("--label", default="", help="a name for this scan, used in the title")
    p.add_argument("--max-leads", type=int, default=200,
                   help="leads sampled per campaign (default: 200)")
    p.add_argument("--out", default=None,
                   help="summary CSV path (default: lastlook.fleet.<label>.csv)")
    p.set_defaults(func=cmd_fleet)

    p = sub.add_parser("rules", help="print the rule catalog")
    p.set_defaults(func=cmd_rules)

    return ap


def main(argv=None):
    _load_dotenv()
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return EXIT_ERROR      # nothing ran; 0 would read as "clear"
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as e:  # noqa: BLE001 - the CLI boundary; nothing escapes as a traceback
        _die(_explain(e))


if __name__ == "__main__":
    sys.exit(main())
