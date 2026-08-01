"""Fixer tests. The near-misses matter more than the hits.

A checker that cries wolf wastes your time. A FIXER that gets it wrong silently
rewrites live copy going to real prospects. Every transform here needs a case
proving it leaves correct text alone.
"""
import copy
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
t("doubled comma", fix._double_punct("Hey Sam,, there"), "Hey Sam, there")
t("doubled period", fix._double_punct("together.. Next"), "together. Next")
t("prose em dash", fix._prose_dash("a — b"), "a - b")
t("collapses double space", fix._collapse_spaces("Hey  there"), "Hey there")

print("\n— emoji must survive the invisible-character strip —")
# A zero-width joiner between emoji is load-bearing: 🤦 + ZWJ + ♀ is ONE glyph.
# Stripping it split live client copy into two emoji, and --apply would have
# written that corruption back to the campaign.
t("emoji ZWJ sequence survives", fix._strip_invisibles("Great work 👨\u200d💻 team"),
  "Great work 👨\u200d💻 team")
t("gendered emoji survives", fix._strip_invisibles("oops 🤦\u200d♀️ yes"), "oops 🤦\u200d♀️ yes")
t("variation selector survives", fix._strip_invisibles("red ❤️ heart"), "red ❤️ heart")
t("skin tone modifier survives", fix._strip_invisibles("wave 👋🏽 hi"), "wave 👋🏽 hi")
t("stray ZWJ inside a word is still stripped",
  fix._strip_invisibles("Hi\u200dJane"), "HiJane")
t("fixer and renderer agree", fix._strip_invisibles("a 👨\u200d💻 b\u200bc"),
  __import__("lastlook.render", fromlist=["x"]).normalize_invisibles("a 👨\u200d💻 b\u200bc")[0])

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

print("\n— a merge tag's NAME is an identifier, never prose —")
# {{Q1–Q2 Goal}} -> {{Q1-Q2 Goal}} stops resolving: the fixer would have broken
# personalization on live copy while the prose around it was the actual defect.
t("en dash inside a tag survives, prose em dash is fixed",
  fix.fix_text("Your {{Q1–Q2 Goal}} is live — see it", "instantly")[0],
  "Your {{Q1–Q2 Goal}} is live - see it")
t("double space inside a tag survives, prose double space is fixed",
  fix.fix_text("See {{Company  Name}} and this  gap", "instantly")[0],
  "See {{Company  Name}} and this gap")
t("doubled period inside a tag survives",
  fix.fix_text("Ref {{Acct..ID}} today", "instantly")[0], "Ref {{Acct..ID}} today")
t("raw single-brace HeyReach tag survives, seam space is fixed",
  fix.fix_text("Hey {FIRST  NAME} , welcome", "heyreach")[0],
  "Hey {FIRST  NAME}, welcome")
t("spintax stays editable (it is prose)",
  fix.fix_text("Pick {fast|slow}  option", "instantly")[0], "Pick {fast|slow} option")
t("prose right after a tag is still fixed",
  fix.fix_text("Hi {{firstName}} ,", "instantly")[0], "Hi {{firstName}},")

print("\n— foreign merge tags —")
t("instantly: single-brace converted",
  fix._foreign_tags("Hey {FIRST_NAME},", "instantly"), "Hey {{firstName}},")
t("heyreach: double-brace left alone (adapter already normalized)",
  fix._foreign_tags("Hey {{first_name}},", "heyreach"), "Hey {{first_name}},")
t("unknown platform changes nothing",
  fix._foreign_tags("Hey {FIRST_NAME},", "lemlist"), "Hey {FIRST_NAME},")

print("\n— data cleaners —")
t("emoji prefix", fix._clean_first_name("🔷Marco"), "Marco")
t("full name to first", fix._clean_first_name("Norman Gregory"), "Norman")
t("honorific dropped, not kept", fix._clean_first_name("Dr. Sam"), "Sam")
t("nickname in quotes", fix._clean_first_name('Theodore "Theo"'), "Theodore")
t("parenthetical nickname", fix._clean_first_name("Sam (Sammy)"), "Sam")
t("shouting name", fix._clean_first_name("DANA"), "Dana")
t("registered mark", fix._clean_company("the Globex Workout®"), "the Globex Workout")
t("legal suffix", fix._clean_company("Initech Ltd"), "Initech")
t("slash tail", fix._clean_company("Renovation Training Company/Training Floor"),
  "Renovation Training Company")
t("all-caps company", fix._clean_company("DRCB CONSULTING"), "Drcb Consulting")

print("\n— data cleaners: leave good values alone —")
for good in ("Jane", "José", "Ana-Maria", "O'Brien"):
    t(f"first name {good!r} untouched", fix._clean_first_name(good), good)
for good in ("Acme", "Initech.io", "AT&T", "3M", "Coca-Cola"):
    t(f"company {good!r} untouched", fix._clean_company(good), good)

print("\n— planning —")
camp = {
    "platform": "instantly",
    "campaign": {"id": "x", "name": "n"},
    "steps": [{"step": 1, "channel": "email", "variants": [
        {"id": "A", "subject": "hi", "body": "Hey {FIRST_NAME},"},
        {"id": "B", "subject": "hi", "body": "Hey {{firstName}},", "disabled": True},
    ]}],
    "leads": [{"id": "1", "first_name": "🔷Marco", "company_name": "Acme Ltd"}],
}
edits = fix.plan_template_fixes(camp)
t("plans only the broken enabled variant", [(e["step"], e["variant"], e["field"]) for e in edits],
  [(1, "A", "body")])
t("disabled variants are skipped", all(e["variant"] != "B" for e in edits), True)
data = fix.plan_data_fixes(camp)
t("plans both data fields", sorted(d["field"] for d in data), ["company_name", "first_name"])
t("nothing is mutated in place", camp["steps"][0]["variants"][0]["body"], "Hey {FIRST_NAME},")

print("\n— stale-write guards —")
instant_sequences = [{"steps": [{"variants": [
    {"subject": "hi", "body": "Hey {FIRST_NAME},"}
]}]}]
instant_edit = [{"step": 1, "variant": "A", "field": "body",
                 "before": "Hey {FIRST_NAME},", "after": "Hey {{firstName}},"}]
patched = copy.deepcopy(instant_sequences)
t("Instantly patches an exact live match",
  fix._patch_instantly_sequences(patched, instant_edit), 1)
t("Instantly applies the planned replacement",
  patched[0]["steps"][0]["variants"][0]["body"], "Hey {{firstName}},")
# The schema allows string steps; the writer's counter is an int. Raw
# comparison made every schema-legal string-step edit abort as "missing".
patched = copy.deepcopy(instant_sequences)
t("Instantly patches a schema-legal STRING step key",
  fix._patch_instantly_sequences(patched, [dict(instant_edit[0], step="1")]), 1)
for label, live in (
        ("Instantly refuses copy changed since pull", "Someone edited this"),
        ("Instantly refuses a missing planned field", None)):
    tree = copy.deepcopy(instant_sequences)
    edits = instant_edit
    if live is None:
        edits = [dict(instant_edit[0], variant="B")]
    else:
        tree[0]["steps"][0]["variants"][0]["body"] = live
    try:
        fix._patch_instantly_sequences(tree, edits)
        t(label, "wrote", "StaleCampaign")
    except fix.StaleCampaign:
        t(label, "StaleCampaign", "StaleCampaign")

hr_tree = {"nodeType": "MESSAGE", "payload": {"messages": ["Hey {FIRST_NAME}  ,"]}}
hr_edit = {("1", "A", "body"): {
    "step": 1, "variant": "A", "field": "body",
    "before": "Hey {{first_name}}  ,", "after": "Hey {{first_name}},",
}}
patched = copy.deepcopy(hr_tree)
t("HeyReach compares canonical tags but patches raw syntax",
  len(fix._hr_patch_tree(patched, hr_edit, None)), 1)
t("HeyReach preserves its single-brace merge syntax",
  patched["payload"]["messages"][0], "Hey {FIRST_NAME},")
stale = copy.deepcopy(hr_tree)
stale["payload"]["messages"][0] = "Edited live"
try:
    fix._hr_patch_tree(stale, hr_edit, None)
    t("HeyReach refuses copy changed since pull", "wrote", "StaleCampaign")
except fix.StaleCampaign:
    t("HeyReach refuses copy changed since pull", "StaleCampaign", "StaleCampaign")

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
           "Initech.io", "Acme", "the Globex Workout®"):
    t(f"{co!r} is a data fix, not a removal", reasons(company_name=co), [])
for fn in ("🔷Marco", "Dr. Sam", "Sam Taylor", "DANA", "José"):
    t(f"{fn!r} is a data fix, not a removal", reasons(first_name=fn), [])

t("clean lead suggests nothing", reasons(first_name="Jane", company_name="Acme"), [])
t("custom community list overrides the default",
  sorted(r["reason"] for r in fix.plan_removals(camp_with(company_name="MyClub"), {"myclub"})),
  ["company_is_a_community"])
t("default communities not applied when a custom list is given",
  fix.plan_removals(camp_with(company_name="Pavilion"), {"myclub"}), [])

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
