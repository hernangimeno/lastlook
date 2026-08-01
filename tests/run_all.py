"""Everything lastlook has to keep passing.

    python3 tests/run_all.py            # verify
    python3 tests/run_all.py --regen    # rewrite goldens (read the diff first)

Four layers:
  behavioural  every rule has a case that fires AND a near-miss that stays quiet
  golden       the findings for each fixture do not drift
  schema       every fixture validates against the published contract
  cli          exit codes mean what the README says they mean
"""

import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "golden")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, ROOT)

BEHAVIOURAL = ("test_dangling.py", "test_leadlist.py",
               "test_template_integrity.py", "test_recap.py", "test_fix.py", "test_false_green.py", "test_credentials.py", "test_prompt.py",
               "test_render_edges.py", "test_no_private_data.py", "test_rule_severity.py")


def run_behavioural():
    failures = 0
    for name in BEHAVIOURAL:
        r = subprocess.run([sys.executable, os.path.join(HERE, name)],
                           capture_output=True, text=True)
        ok = r.returncode == 0 and "all pass" in r.stdout
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures += 1
            print(r.stdout[-2000:], r.stderr[-1000:])
    return failures


def _findings(campaign_path):
    from lastlook import check, render
    with open(campaign_path, encoding="utf-8") as f:
        campaign = json.load(f)
    rows = list(render.iter_rendered(campaign))
    return check.run(rows, check.load_spam_words(None), campaign_json=campaign)


def _keys(findings):
    return sorted({(f["check"], f["severity"], str(f.get("lead_id")), str(f.get("variant")))
                   for f in findings})


def run_golden(regen=False):
    os.makedirs(GOLDEN, exist_ok=True)
    failures = 0
    for fx in sorted(os.listdir(FIXTURES)):
        if not fx.endswith(".json"):
            continue
        base = fx[:-5]
        got = _keys(_findings(os.path.join(FIXTURES, fx)))
        want_path = os.path.join(GOLDEN, f"{base}.findings.csv")

        if regen or not os.path.exists(want_path):
            with open(want_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["check", "severity", "lead_id", "variant"])
                w.writerows(got)
            print(f"WROTE {base} ({len(got)} findings)")
            continue

        with open(want_path, encoding="utf-8") as f:
            want = sorted(tuple(r) for r in list(csv.reader(f))[1:])
        if got == want:
            print(f"PASS  {base} ({len(got)} findings)")
        else:
            failures += 1
            print(f"FAIL  {base}")
            for x in sorted(set(want) - set(got)):
                print("        LOST: ", x)
            for x in sorted(set(got) - set(want)):
                print("        NEW:  ", x)
    return failures


def run_schema():
    from lastlook.validate import CampaignError, validate
    failures = 0
    for fx in sorted(os.listdir(FIXTURES)):
        if not fx.endswith(".json"):
            continue
        with open(os.path.join(FIXTURES, fx), encoding="utf-8") as f:
            try:
                validate(json.load(f))
                print(f"PASS  {fx} validates")
            except CampaignError as e:
                failures += 1
                print(f"FAIL  {fx}: {e}")
    # And the guard must actually reject something.
    try:
        validate({"platform": "x", "campaign": {"name": "n"}, "leads": [],
                  "steps": [{"step": 1, "delay_days": "three", "variants": [{"id": "A"}]}]})
        print("FAIL  a bad delay_days was accepted")
        failures += 1
    except CampaignError:
        print("PASS  a bad field is rejected by name")
    for label, bad_delay in (("boolean delay", False), ("negative delay", -1),
                             ("non-finite delay", float("nan"))):
        try:
            validate({"platform": "x", "campaign": {"name": "n"}, "leads": [],
                      "steps": [{"step": 1, "delay_days": bad_delay,
                                 "variants": [{"id": "A"}]}]})
            print(f"FAIL  {label} was accepted")
            failures += 1
        except CampaignError:
            print(f"PASS  {label} is rejected")
    return failures


def run_cli():
    """Exit codes are part of the public contract: people gate sends on them."""
    failures = 0
    noleads = os.path.join(FIXTURES, "_noleads.json")
    with open(os.path.join(FIXTURES, "fixture_conditional.json"), encoding="utf-8") as f:
        stripped = json.load(f)
    stripped["leads"] = []
    with open(noleads, "w", encoding="utf-8") as f:
        json.dump(stripped, f)
    cases = [
        ("clean fixture -> 0", ["fixture_conditional.json"], 0),
        ("blocker fixture -> 2", ["fixture_planted_bugs.json"], 2),
    ]
    for label, (fx,), want in [(l, a, w) for l, a, w in cases]:
        campaign = os.path.join(FIXTURES, fx)
        subprocess.run([sys.executable, "-m", "lastlook.cli", "render", campaign,
                        "-o", "/tmp/_ll.jsonl"], cwd=ROOT, capture_output=True)
        r = subprocess.run([sys.executable, "-m", "lastlook.cli", "check", "/tmp/_ll.jsonl",
                            "--campaign-json", campaign, "-o", "/tmp/_ll.csv"],
                           cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == want
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label} (got {r.returncode})")

    clean = os.path.join(FIXTURES, "fixture_conditional.json")
    bugs = os.path.join(FIXTURES, "fixture_planted_bugs.json")
    for label, argv, want in [
        ("missing file -> 3", ["check", "/tmp/_nope.jsonl"], 3),
        ("unknown rule -> 3", ["check", "/tmp/_ll.jsonl", "--disable", "NOPE"], 3),
        ("rules catalog -> 0", ["rules"], 0),
        # A usage error is a TOOL error. Exit 2 would read as "blockers found".
        ("unknown subcommand -> 3", ["audits"], 3),
        ("bad flag value -> 3", ["coverage", clean, "--min-fill", "abc"], 3),
        ("NaN fill percentage -> 3", ["coverage", clean, "--min-fill", "nan"], 3),
        ("zero max-leads -> 3", ["audit", "instantly", "--campaign", "x",
                                 "--max-leads", "0"], 3),
        ("missing required flag -> 3", ["fleet"], 3),
        ("no arguments at all -> 3", [], 3),
        # --forbidden-terms used to scan templates only, so without the campaign
        # JSON it checked nothing and said CLEAR. It now scans rendered output,
        # where a term arriving through lead data actually shows up: 2, not 0.
        ("forbidden term in rendered output -> 2",
         ["check", "/tmp/_ll.jsonl", "--forbidden-terms", "acme"], 2),
        # A mistyped path became a literal banned term, silently.
        ("forbidden-terms nonexistent file -> 3",
         ["check", "/tmp/_ll.jsonl", "--campaign-json", bugs,
          "--forbidden-terms", "./_nope_terms.txt"], 3),
        ("spam-words nonexistent file -> 3",
         ["check", "/tmp/_ll.jsonl", "--spam-words", "./_nope_spam.txt"], 3),
        # Rule names are shouted in the catalog; case alone must not be a wall.
        ("lowercase rule name is accepted",
         ["check", "/tmp/_ll.jsonl", "--campaign-json", bugs, "--only", "em_dash"], 2),
        # A pass over nothing is never a pass.
        ("coverage on a lead-less campaign -> 3",
         ["coverage", os.path.join(FIXTURES, "_noleads.json")], 3),
        # The campaign JSON handed to `check` gets a pointer, not a JSON error.
        ("campaign JSON passed to check -> 3", ["check", bugs], 3),
        ("unwritable render output -> 3", ["render", clean, "-o", "/_nodir_/x.jsonl"], 3),
    ]:
        r = subprocess.run([sys.executable, "-m", "lastlook.cli"] + argv,
                           cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == want
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label} (got {r.returncode})")

    # A narrowed run must SAY what it did not check. Silence here reads as coverage.
    r = subprocess.run([sys.executable, "-m", "lastlook.cli", "check", "/tmp/_ll.jsonl",
                        "--only", "EM_DASH", "--campaign-json",
                        os.path.join(FIXTURES, "fixture_conditional.json"),
                        "-o", "/tmp/_ll2.csv"], cwd=ROOT, capture_output=True, text=True)
    ok = "NOT CHECKED: 34 rule" in r.stdout and "35 checks ran" not in r.stdout
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  --only reports the 34 rules that did not run")

    # ... and so must --no-recap, which suppresses the recap, not the hole.
    r = subprocess.run([sys.executable, "-m", "lastlook.cli", "check", "/tmp/_ll.jsonl",
                        "--only", "EM_DASH", "--no-recap", "-o", "/tmp/_ll2.csv"],
                       cwd=ROOT, capture_output=True, text=True)
    ok = "NOT CHECKED" in r.stdout
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  --no-recap still reports the coverage hole")

    os.remove(noleads)
    return failures


if __name__ == "__main__":
    regen = "--regen" in sys.argv
    print("— behavioural —"); f1 = run_behavioural()
    print("\n— golden —");     f2 = run_golden(regen)
    print("\n— schema —");     f3 = run_schema()
    print("\n— cli —");        f4 = run_cli()
    total = f1 + f2 + f3 + f4
    print(f"\n{'ALL PASS' if not total else f'{total} FAILING'}")
    sys.exit(1 if total else 0)
