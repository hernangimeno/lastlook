"""Render/verdict/CLI edge cases from the v1 launch review.

Each fix keeps a case that fires and a near-miss that stays quiet:
  - nested {{#if}} renders correctly on every branch
  - a RANDOM option may contain an inline {{var | fallback}} without being split
  - the verdict counts DISTINCT broken leads, never (lead, variant) pairs
  - a spam/forbidden term ending in punctuation ("100%") still matches
  - campaign-not-found raises LookupError (exit 3 at the CLI), never exit 1
  - an unknown platform in a campaign JSON dies with a sentence, not a KeyError
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check, cli
from lastlook.render import render_message, BLANK

fails = 0


def case(label, got, expect):
    global fails
    ok = got == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:44} got={got!r} expected={expect!r}")


LEAD = {"id": "l1", "email": "a@b.com", "first_name": "Anne", "company_name": "Acme"}

# --- nested conditionals ---
t = "{{#if first_name}}A{{#if company_name}}B{{/if}}C{{/if}}done"
case("nested if, both truthy", render_message(t, LEAD, {})[0], "ABCdone")
t = "{{#if missing}}A{{#if first_name}}B{{/if}}C{{/if}}done"
case("nested if, falsy outer", render_message(t, LEAD, {})[0], "done")
t = "{{#if missing}}A{{else}}X{{#if first_name}}Y{{/if}}Z{{/if}}"
case("nested if inside else", render_message(t, LEAD, {})[0], "XYZ")
t = "{{#if first_name}}a{{/if}} and {{#if company_name}}b{{/if}}"
case("stacked (non-nested) ifs", render_message(t, LEAD, {})[0], "a and b")

# --- RANDOM options containing an inline fallback ---
t = "{{RANDOM | Hey {{first_name}} | Hi {{company_name | your team}} }}"
outs = set()
for i in range(40):
    lead = dict(LEAD, id=f"lead{i}", email=f"x{i}@y.com")
    outs.add(render_message(t, lead, {})[0].strip())
case("RANDOM options stay whole", outs <= {"Hey Anne", "Hi Acme"}, True)
t = "{{RANDOM | plain A | plain B }}"
outs = {render_message(t, dict(LEAD, id=f"p{i}"), {})[0].strip() for i in range(10)}
case("RANDOM without inner tags still splits", outs <= {"plain A", "plain B"}, True)

# --- RANDOM: an unbalanced opening must not hang (ReDoS) ---
# The old single-regex form was exponential on this input: 140 bytes took 3.2s
# and 200 bytes never returned. The scan is linear, so a pathological input that
# used to wedge the process now finishes in milliseconds and the broken tag is
# left in place for the leftover-tag checks to flag.
import time
evil = "{{RANDOM | " + "{{a}}" * 60 + "x"          # 300+ bytes, never balances
t0 = time.monotonic()
out = render_message(evil, LEAD, {})[0]
elapsed = time.monotonic() - t0
case("unbalanced RANDOM finishes fast", elapsed < 1.0, True)
# The broken block falls through to the var pass, which cannot resolve it and
# leaves BLANK sentinels: the blank-merge check fires. A malformed template must
# never render clean.
case("unbalanced RANDOM still raises a finding", BLANK in out, True)
# Near-miss: a balanced block sitting AFTER a broken one still renders.
t = "{{RANDOM | broken {{a}} " + "\n" + "{{RANDOM | good A | good B }}"
outs = {render_message(t, dict(LEAD, id=f"q{i}"), {})[0] for i in range(10)}
case("balanced RANDOM after a broken one renders",
     all(o.rstrip().endswith(("good A", "good B")) for o in outs), True)

# --- verdict counts distinct leads ---
rows = [{"lead_id": "l1", "variant": "A", "step": 1},
        {"lead_id": "l1", "variant": "B", "step": 1}]
findings = [
    {"lead_id": "l1", "lead_email": "", "step": 1, "variant": v, "channel": "email",
     "check": "EM_DASH", "severity": check.BLOCKER, "evidence": "x", "signature": f"s{v}"}
    for v in ("A", "B")]
line = [ln for ln in check.verdict_block(rows, findings).splitlines() if "would send" in ln][0]
case("verdict: 1 lead broken in 2 variants", "1 of 1 leads" in line, True)

# --- terms ending in punctuation ---
spam = check.make_spam_check({"100%", "free"})
case("'100%' fires", bool(spam({"subject": "", "body": "Get 100% results"})), True)
case("'free' inside 'freedom' stays quiet",
     bool(spam({"subject": "", "body": "freedom of choice"})), False)
case("'earn $' still fires before a number",
     bool(check.make_spam_check({"earn $"})({"subject": "", "body": "earn $5000 now"})), True)

# --- campaign not found is a LookupError (exit 3), never SystemExit(str) = exit 1 ---
import httpx
from lastlook.adapters import instantly


def handler(request):
    if request.url.path == "/api/v2/campaigns":
        return httpx.Response(200, json={"items": [], "next_starting_after": None})
    return httpx.Response(404, json={})


real_client = instantly.client
instantly.client = lambda key: httpx.Client(
    base_url="https://api.instantly.ai/api/v2", transport=httpx.MockTransport(handler))
try:
    instantly.pull("fake-key", "No Such Campaign")
    case("campaign-not-found raises", "returned", "LookupError")
except LookupError as e:
    case("campaign-not-found raises LookupError", True, True)
    case("_explain keeps it a plain sentence", cli._explain(e), str(e))
except SystemExit:
    case("campaign-not-found raises LookupError", "SystemExit(=exit 1)", "LookupError")
finally:
    instantly.client = real_client

# --- adapter pagination: list responses, missing totals, repeated cursors ---
def lead_list_handler(request):
    return httpx.Response(200, json=[{"id": "l1", "email": "a@example.com"}])


with httpx.Client(base_url="https://api.instantly.ai/api/v2",
                  transport=httpx.MockTransport(lead_list_handler)) as cx:
    case("Instantly accepts a top-level list response",
         len(instantly.fetch_leads(cx, "c1")), 1)

cursor_calls = {"n": 0}


def repeated_cursor_handler(request):
    cursor_calls["n"] += 1
    return httpx.Response(200, json={
        "items": [{"id": f"l{cursor_calls['n']}", "email": "a@example.com"}],
        "next_starting_after": "same-cursor",
    })


with httpx.Client(base_url="https://api.instantly.ai/api/v2",
                  transport=httpx.MockTransport(repeated_cursor_handler)) as cx:
    try:
        instantly.fetch_leads(cx, "c1")
        case("Instantly rejects a repeated pagination cursor", "returned", "RuntimeError")
    except RuntimeError:
        case("Instantly rejects a repeated pagination cursor", "RuntimeError", "RuntimeError")

from lastlook.adapters import heyreach
page_calls = {"n": 0}


def no_total_handler(request):
    page_calls["n"] += 1
    count = 100 if page_calls["n"] == 1 else 1
    return httpx.Response(200, json={
        "items": [{"profileUrl": f"https://linkedin.example/{page_calls['n']}/{i}"}
                  for i in range(count)]
    })


with httpx.Client(base_url="https://api.heyreach.io",
                  transport=httpx.MockTransport(no_total_handler)) as cx:
    case("HeyReach paginates when totalCount is absent",
         len(heyreach.fetch_list_leads(cx, 1)), 101)

from lastlook import fleet
real_pull = fleet.pull_instantly.pull
fleet.pull_instantly.pull = lambda *args: {
    "platform": "instantly", "campaign": {"id": "c", "name": "empty"},
    "steps": [], "leads": [], "defined_vars": [],
}
try:
    fleet.scan_one({"platform": "instantly", "campaign": "c", "key": "k"}, 200)
    case("fleet refuses a zero-message false clear", "returned", "ValueError")
except ValueError:
    case("fleet refuses a zero-message false clear", "ValueError", "ValueError")
finally:
    fleet.pull_instantly.pull = real_pull

# --- unknown platform dies with a sentence and exit 3 ---
try:
    cli._key_for("fixture", None)
    case("unknown platform dies", "returned", "SystemExit(3)")
except SystemExit as e:
    case("unknown platform dies with exit 3", e.code, 3)
except KeyError:
    case("unknown platform dies", "KeyError", "SystemExit(3)")

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
