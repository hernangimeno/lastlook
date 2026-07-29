"""Template-integrity rules: placeholders, forbidden terms, variant and subject.

Every rule needs a case that FIRES and a near-miss that stays quiet. These rules
scan ordinary English ("test", "your company"), so the quiet cases are where the
real risk is: a checker that flags clean copy gets switched off.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check


def campaign(variants, channel="email", step=1):
    return {"steps": [{"step": step, "channel": channel, "limits": {}, "variants": variants}],
            "leads": []}


def two_steps(v1, v2):
    return {"steps": [{"step": 1, "channel": "email", "limits": {}, "variants": v1},
                      {"step": 2, "channel": "email", "limits": {}, "variants": v2}],
            "leads": []}


def V(vid, subject="a subject", body="Hi {{firstName}},\n\nA normal note.\n\nHernan"):
    return {"id": vid, "subject": subject, "body": body}


fails = 0


def expect(label, got_checks, want):
    global fails
    got = set(got_checks)
    ok = got == set(want)
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:44} {sorted(got) if got else '(silent)'}")
    if not ok:
        print(f"          expected {sorted(want) if want else '(silent)'}")


def checks(fn, *a, **kw):
    return [f["check"] for f in fn(*a, **kw)]


print("— placeholders —")
for label, body, want in [
    ("lorem ipsum",            "Hi,\n\nLorem ipsum dolor sit amet.", ["PLACEHOLDER_TEXT"]),
    ("bracketed [insert x]",   "Hi,\n\nWe helped [insert client] grow.", ["PLACEHOLDER_TEXT"]),
    ("angle <your company>",   "Hi,\n\nAbout <your company> here.", ["PLACEHOLDER_TEXT"]),
    ("caps TODO",              "Hi,\n\nTODO: add the case study.", ["PLACEHOLDER_TEXT"]),
    ("bare caps TEST",         "Hi,\n\nTEST\n\nHernan", ["PLACEHOLDER_TEXT"]),
    ("your company here",      "Hi,\n\nyour company here needs this.", ["PLACEHOLDER_TEXT"]),
    # near-misses: ordinary English that must NOT fire
    ("'test' as a verb",       "Hi,\n\nWe test your pipeline weekly.", []),
    ("'testing' in prose",     "Hi,\n\nA/B testing is how we work.", []),
    ("'todo' lowercase prose", "Hi,\n\nMy todo list is long.", []),
    ("legit bracket citation", "Hi,\n\nRevenue grew [2024 figures].", []),
    ("'your company' in prose","Hi,\n\nHow does your company handle this?", []),
]:
    expect(label, checks(check.chk_placeholders, campaign([V("1A", body=body)])), want)

print("\n— forbidden terms —")
expect("competitor name present",
       checks(check.chk_forbidden_terms, campaign([V("1A", body="We beat Acme every time.")]), ["acme"]),
       ["FORBIDDEN_TERM"])
expect("term absent",
       checks(check.chk_forbidden_terms, campaign([V("1A", body="Nothing to see.")]), ["acme"]), [])
expect("substring must not match",
       checks(check.chk_forbidden_terms, campaign([V("1A", body="Acmetric is different.")]), ["acme"]), [])
expect("no terms supplied",
       checks(check.chk_forbidden_terms, campaign([V("1A", body="We beat Acme.")]), []), [])
expect("term only as a merge tag name",
       checks(check.chk_forbidden_terms,
              campaign([V("1A", body="We helped {{competitor}} grow.")]), ["competitor"]), [])
expect("term in prose beside that tag",
       checks(check.chk_forbidden_terms,
              campaign([V("1A", body="Your competitor {{competitor}} grew.")]), ["competitor"]),
       ["FORBIDDEN_TERM"])

print("\n— variant integrity —")
same = "Hi {{firstName}},\n\nNoticed you are scaling ads at {{company}}.\n\nWorth a chat?\n\nHernan"
near = "Hi {{firstName}},\n\nNoticed you are scaling ads at {{company}}.\n\nWorth a chat!\n\nHernan"
diff = "Hey {{firstName}},\n\nTotally different angle: your hiring page.\n\nOpen to it?\n\nHernan"
sev1 = lambda c: [f"{f['check']}:{f['severity']}" for f in check.chk_variant_integrity(c)]
expect("identical variants BLOCK", sev1(campaign([V("1A", body=same), V("1B", body=same)])),
       ["VARIANT_NOT_DISTINCT:BLOCKER", "SHARED_OPENER:WARNING"])
expect("one-char difference is a real edit, not a duplicate",
       sev1(campaign([V("1A", body=same), V("1B", body=near)])), ["SHARED_OPENER:WARNING"])
expect("whitespace-only difference still counts as duplicate",
       sev1(campaign([V("1A", body=same), V("1B", body=same.replace("\n\n", "\n\n  "))])),
       ["VARIANT_NOT_DISTINCT:BLOCKER", "SHARED_OPENER:WARNING"])
# The live Initech pair: one swapped sentence is a deliberate single-variable test.
onesent_a = ("Hi {{firstName}},\n\nLooking to automate rep busywork?\n\nInitech automates rep "
             "busywork and builds the assets needed to win deals.\n\nI've already built a pitch "
             "deck for {{companyName}} showing how Initech would work for your team. Want to see it?")
onesent_b = ("Hi {{firstName}},\n\nLooking to automate rep busywork?\n\nInitech automates rep "
             "busywork and builds the assets needed to win deals.\n\nI can build a pitch deck for "
             "{{companyName}} in minutes to show you how Initech would work for your team. Want to see it?")
expect("deliberate one-sentence swap is not flagged",
       [f["check"] for f in check.chk_variant_integrity(
           campaign([V("1A", body=onesent_a), V("1B", body=onesent_b)]))
        if f["check"] == "VARIANT_NOT_DISTINCT"], [])
expect("genuinely different variants", checks(check.chk_variant_integrity,
       campaign([V("1A", body=same), V("1B", body=diff)])), [])
expect("single variant cannot collide", checks(check.chk_variant_integrity,
       campaign([V("1A", body=same)])), [])
expect("shared opener, different bodies", checks(check.chk_variant_integrity,
       campaign([V("1A", body="Hi {{firstName}},\n\nAngle one entirely about ads spend here."),
                 V("1B", body="Hi {{firstName}},\n\nSomething wholly unrelated re hiring plans.")])),
       ["SHARED_OPENER"])

print("\n— subject integrity —")
expect("empty subject on step 1",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="")])), ["EMPTY_SUBJECT"])
expect("emoji subject",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="quick one 🚀")])), ["SUBJECT_STYLE"])
expect("double bang subject",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="open this!!")])), ["SUBJECT_STYLE"])
expect("shouting subject",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="URGENT OFFER INSIDE NOW")])),
       ["SUBJECT_STYLE"])
# near-misses
expect("normal subject",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="quick question on ads")])), [])
expect("single acronym is not shouting",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="your CRM and RevOps setup")])), [])
expect("one exclamation is fine",
       checks(check.chk_subject_integrity, campaign([V("1A", subject="nice work!")])), [])
expect("LinkedIn step needs no subject",
       checks(check.chk_subject_integrity,
              campaign([V("1A", subject="")], channel="connection_request")), [])
expect("empty subject on follow-up threads correctly",
       checks(check.chk_subject_integrity, two_steps([V("1A")], [V("2A", subject="")])), [])
expect("follow-up with own subject breaks thread",
       checks(check.chk_subject_integrity, two_steps([V("1A")], [V("2A", subject="new topic")])),
       ["THREAD_BREAK"])
expect("follow-up with Re: is fine",
       checks(check.chk_subject_integrity, two_steps([V("1A")], [V("2A", subject="Re: a subject")])), [])

print("\n— step pacing —")


def paced(*delays):
    return {"steps": [{"step": i + 1, "channel": "email", "delay_days": d,
                       "variants": [V(f"{i+1}A")]} for i, d in enumerate(delays)],
            "leads": []}


def sev(c):
    return [(f["check"], f["severity"]) for f in check.chk_step_pacing(c)]


for label, c, want in [
    ("zero-day gap",        paced(0, 3),    [("STEPS_NOT_PACED", "BLOCKER")]),
    ("one-day gap",         paced(1, 3),    [("STEPS_NOT_PACED", "WARNING")]),
    ("healthy 3-day gaps",  paced(3, 3, 3), []),
    ("two-day gap is fine", paced(2, 2),    []),
    ("last step delay ignored", paced(3, 0), []),   # nothing follows the last step
    ("no delay stated",     paced(None, None), []), # silence is not evidence
    ("single step",         paced(3),       []),
    ("hours normalize to <1 day", paced(0.25, 3), [("STEPS_NOT_PACED", "WARNING")]),
]:
    expect(label, [f"{a}:{b}" for a, b in sev(c)],
           [f"{a}:{b}" for a, b in want])

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
