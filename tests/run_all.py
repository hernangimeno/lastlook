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
               "test_template_integrity.py", "test_recap.py")


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
    return failures


def run_cli():
    """Exit codes are part of the public contract: people gate sends on them."""
    failures = 0
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

    for label, argv, want in [
        ("missing file -> 3", ["check", "/tmp/_nope.jsonl"], 3),
        ("unknown rule -> 3", ["check", "/tmp/_ll.jsonl", "--disable", "NOPE"], 3),
        ("rules catalog -> 0", ["rules"], 0),
    ]:
        r = subprocess.run([sys.executable, "-m", "lastlook.cli"] + argv,
                           cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == want
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label} (got {r.returncode})")
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
