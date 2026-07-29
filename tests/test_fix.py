"""Fixer tests. The near-misses matter more than the hits.

A checker that cries wolf wastes your time. A FIXER that gets it wrong silently
rewrites live copy going to real prospects. Every transform here needs a case
proving it leaves correct text alone.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import fix

fails = 0


def t(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")


print("— template transforms —")
t("strips a zero-width space", fix._strip_invisibles("Hi​Jane"), "HiJane")
t("nbsp becomes a real space", fix._strip_invisibles("Hi Jane"), "Hi Jane")
t("space before comma", fix._space_before_punct("Hey Anne , how are you"), "Hey Anne, how are you")
t("doubled comma", fix._double_punct("Hey Yune,, there"), "Hey Yune, there")
t("doubled period", fix._double_punct("together.. Next"), "together. Next")
t("prose em dash", fix._prose_dash("a — b"), "a - b")
t("collapses double space", fix._collapse_spaces("Hey  there"), "Hey there")

print("\n— near-misses: correct text must survive untouched —")
t("ellipsis survives", fix._double_punct("wait... really"), "wait... really")
t("numeric en dash range survives", fix._prose_dash("11–15 hours"), "11–15 hours")
t("emoticon keeps its space", fix._space_before_punct("send the link :)"), "send the link :)")
t("space before colon survives", fix._space_before_punct("the plan : step one"), "the plan : step one")
t("paragraph break survives", fix._collapse_spaces("Hi,\n\nAll good"), "Hi,\n\nAll good")
t("decimal survives", fix._double_punct("grew 2.5x"), "grew 2.5x")
t("clean copy is untouched",
  fix.fix_text("Hi {{firstName}},\n\nAll good at {{company}}.\n\nHernan")[0],
  "Hi {{firstName}},\n\nAll good at {{company}}.\n\nHernan")

print("\n— foreign merge tags —")
t("instantly: single-brace converted",
  fix._foreign_tags("Hey {FIRST_NAME},", "instantly"), "Hey {{firstName}},")
t("heyreach: double-brace left alone (adapter already normalized)",
  fix._foreign_tags("Hey {{first_name}},", "heyreach"), "Hey {{first_name}},")
t("unknown platform changes nothing",
  fix._foreign_tags("Hey {FIRST_NAME},", "lemlist"), "Hey {FIRST_NAME},")

print("\n— data cleaners —")
t("emoji prefix", fix._clean_first_name("🔷Anthony"), "Anthony")
t("full name to first", fix._clean_first_name("Norman Gregory"), "Norman")
t("honorific dropped, not kept", fix._clean_first_name("Dr. Sam"), "Sam")
t("nickname in quotes", fix._clean_first_name('Theodore "Theo"'), "Theodore")
t("parenthetical nickname", fix._clean_first_name("Julian (Jules)"), "Julian")
t("shouting name", fix._clean_first_name("DANA"), "Dana")
t("registered mark", fix._clean_company("the Globex Workout®"), "the Globex Workout")
t("legal suffix", fix._clean_company("Initech Ltd"), "Initech")
t("slash tail", fix._clean_company("Renovation Training Company/Training Floor"),
  "Renovation Training Company")
t("all-caps company", fix._clean_company("DRCB CONSULTING"), "Drcb Consulting")

print("\n— data cleaners: leave good values alone —")
for good in ("Jane", "José", "Ana-Maria", "O'Brien"):
    t(f"first name {good!r} untouched", fix._clean_first_name(good), good)
for good in ("Acme", "Customer.io", "AT&T", "3M", "Coca-Cola"):
    t(f"company {good!r} untouched", fix._clean_company(good), good)

print("\n— planning —")
camp = {
    "platform": "instantly",
    "campaign": {"id": "x", "name": "n"},
    "steps": [{"step": 1, "channel": "email", "variants": [
        {"id": "A", "subject": "hi", "body": "Hey {FIRST_NAME},"},
        {"id": "B", "subject": "hi", "body": "Hey {{firstName}},", "disabled": True},
    ]}],
    "leads": [{"id": "1", "first_name": "🔷Anthony", "company_name": "Acme Ltd"}],
}
edits = fix.plan_template_fixes(camp)
t("plans only the broken enabled variant", [(e["step"], e["variant"], e["field"]) for e in edits],
  [(1, "A", "body")])
t("disabled variants are skipped", all(e["variant"] != "B" for e in edits), True)
data = fix.plan_data_fixes(camp)
t("plans both data fields", sorted(d["field"] for d in data), ["company_name", "first_name"])
t("nothing is mutated in place", camp["steps"][0]["variants"][0]["body"], "Hey {FIRST_NAME},")

print("\n— apply refuses what it cannot do —")
try:
    fix.apply_template_fixes({"platform": "lemlist", "campaign": {"id": "1", "name": "n"}},
                             [{"step": 1, "variant": "A", "field": "body"}], "k")
    t("unsupported platform raises", False, True)
except fix.ApplyUnsupported:
    t("unsupported platform raises ApplyUnsupported", True, True)

print("\n— removals: only when cleaning cannot save it —")

def camp_with(**lead):
    return {"platform": "heyreach", "campaign": {"id": "1", "name": "n"},
            "steps": [], "leads": [dict(id="L1", **lead)]}


def reasons(**lead):
    return sorted(r["reason"] for r in fix.plan_removals(camp_with(**lead)))


t("community as employer", reasons(company_name="Pavilion"), ["company_is_a_community"])
t("community, other casing", reasons(company_name="exit five"), ["company_is_a_community"])
t("placeholder name", reasons(first_name="there"), ["name_is_placeholder"])
t("pure emoji name", reasons(first_name="🌀"), ["name_unusable"])
t("email in the name field", reasons(first_name="jane@acme.com"), ["name_is_not_a_name"])

print("\n— removals: recoverable values are NOT removals —")
for co in ("Globex Law Office/www.globex.eu", "Initech.co | We are hiring!",
           "Acme Inbound - AcmeInbound.com", "Contoso.com, LLC",
           "Customer.io", "Acme", "the Globex Workout®"):
    t(f"{co!r} is a data fix, not a removal", reasons(company_name=co), [])
for fn in ("🔷Anthony", "Dr. Sam", "Norman Gregory", "DANA", "José"):
    t(f"{fn!r} is a data fix, not a removal", reasons(first_name=fn), [])

t("clean lead suggests nothing", reasons(first_name="Jane", company_name="Acme"), [])
t("custom community list overrides the default",
  sorted(r["reason"] for r in fix.plan_removals(camp_with(company_name="MyClub"), {"myclub"})),
  ["company_is_a_community"])
t("default communities not applied when a custom list is given",
  fix.plan_removals(camp_with(company_name="Pavilion"), {"myclub"}), [])

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
