"""Lead-list hygiene: every rule needs a trigger AND a near-miss that stays quiet."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check

def run(leads, **kw):
    out = check.chk_lead_list({"leads": leads}, **kw)
    return {(f["check"], f["lead_email"]) for f in out}

cases = []

# --- triggers
cases.append(("duplicate", [{"id": "1", "email": "a@x.com"}, {"id": "2", "email": "A@X.com"}],
              {("LEAD_DUPLICATE", "A@X.com")}))
cases.append(("role inbox", [{"id": "1", "email": "info@x.com"}],
              {("LEAD_ROLE_ADDRESS", "info@x.com")}))
cases.append(("role w/ dot", [{"id": "1", "email": "no.reply@x.com"}],
              {("LEAD_ROLE_ADDRESS", "no.reply@x.com")}))
cases.append(("invalid", [{"id": "1", "email": "not-an-email"}],
              {("LEAD_INVALID_EMAIL", "not-an-email")}))
cases.append(("no email", [{"id": "1", "email": ""}],
              {("LEAD_NO_EMAIL", "")}))
cases.append(("freemail", [{"id": "1", "email": "jane@gmail.com"}],
              {("LEAD_FREEMAIL", "jane@gmail.com")}))

# --- near-misses that must stay silent
cases.append(("normal b2b address", [{"id": "1", "email": "jane.doe@acme.com"}], set()))
cases.append(("subdomain address", [{"id": "1", "email": "j@mail.acme.co.uk"}], set()))
cases.append(("plus addressing", [{"id": "1", "email": "jane+ab@acme.com"}], set()))
# "sam" contains "sa" but is not a role account; "information" is not "info"
cases.append(("name resembling role", [{"id": "1", "email": "information.systems@acme.com"}], set()))
cases.append(("4 at one domain, under threshold",
              [{"id": str(i), "email": f"p{i}@acme.com"} for i in range(4)], set()))

fails = 0
for label, leads, expect in cases:
    got = run(leads)
    ok = got == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:34} {sorted(got) if got else '(silent)'}")
    if not ok:
        print(f"        expected {sorted(expect) if expect else '(silent)'}")

# over-contact fires at the threshold
got = {f["check"] for f in check.chk_lead_list({"leads": [{"id": str(i), "email": f"p{i}@acme.com"} for i in range(5)]})}
ok = "LEAD_OVER_CONTACT" in got
fails += not ok
print(f"{'PASS' if ok else 'FAIL'}  {'5 at one domain, over threshold':34} {sorted(got)}")

print("\nall pass" if not fails else f"\n{fails} FAILURES")
