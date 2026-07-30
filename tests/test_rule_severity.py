"""The `lastlook rules` severity column must match what the rules actually report.

check.RULE_SEVERITY is a hand-held table, and a table that describes code is a
table that drifts. This re-derives it from the check bodies and fails on any
disagreement, so the catalog cannot advertise WARNING for a rule that blocks.

Also asserts the two accounting sets stay honest: every rule has a severity, and
every CAMPAIGN_ONLY_RULES name is a real rule.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check

fails = 0


def case(label, got, expect):
    global fails
    ok = got == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:52} got={got!r} expected={expect!r}")


src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "lastlook", "check.py")
with open(src_path, encoding="utf-8") as fh:
    src = fh.read()

found = collections.defaultdict(set)
for pat in (r'"([A-Z][A-Z_0-9]+)",\s*(BLOCKER|WARNING)',
            r'"check":\s*"([A-Z][A-Z_0-9]+)",\s*"severity":\s*(BLOCKER|WARNING)'):
    for m in re.finditer(pat, src):
        if m.group(1) in check.RULES:
            found[m.group(1)].add(m.group(2))

derived = {name: ("either" if len(sevs) > 1 else next(iter(sevs)))
           for name, sevs in found.items()}

case("every rule has a derived severity",
     sorted(derived) == sorted(check.RULES), True)
drift = {n: (check.RULE_SEVERITY.get(n), derived[n])
         for n in derived if check.RULE_SEVERITY.get(n) != derived[n]}
case("RULE_SEVERITY matches the check bodies", drift, {})
case("every rule appears in RULE_SEVERITY",
     sorted(check.RULE_SEVERITY) == sorted(check.RULES), True)
case("CAMPAIGN_ONLY_RULES are all real rules",
     sorted(set(check.CAMPAIGN_ONLY_RULES) - set(check.RULES)), [])

# --- the accounting itself ----------------------------------------------------
ran, skipped = check.rules_actually_run(campaign_json=None)
case("no campaign JSON: campaign rules are reported skipped",
     set(check.CAMPAIGN_ONLY_RULES) <= set(skipped), True)
case("no campaign JSON: ran + skipped covers the catalog",
     sorted(ran | set(skipped)) == sorted(check.RULES), True)
case("LINK_HEALTH sits out without --check-links",
     skipped.get("LINK_HEALTH"), "needs --check-links")

ran, skipped = check.rules_actually_run(enabled={"EM_DASH"}, campaign_json={"leads": []})
case("--only EM_DASH really means one rule ran", sorted(ran), ["EM_DASH"])
case("--only EM_DASH reports 34 skipped", len(skipped), len(check.RULES) - 1)

full = check.rules_actually_run(campaign_json={"leads": []}, check_links=True,
                               forbidden_terms=["x"])
case("everything supplied: nothing is skipped", full[1], {})

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
