"""The rules whose whole job is to stop a broken campaign reading as CLEAR.

A 100-campaign sweep never fired either of these, which is exactly why they need
tests: a safety net nobody exercises is a safety net nobody notices has rotted.

UNKNOWN_SYNTAX is the false-green guard. Every platform quirk that ever shipped
unpersonalized copy (HeyReach single-brace {FIRST_NAME} in an Instantly campaign,
Instantly {{RANDOM}}) surfaced here first. If it stops firing, a campaign sending
literal merge tags to 4,000 people reports CLEAR.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check

fails = 0


def t(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r} want {want!r}")


def fires(body, subject=""):
    row = {"subject": subject, "body": body, "channel": "email", "step": 1}
    return [f[0] for f in check.chk_unknown_syntax(row)]


print("— UNKNOWN_SYNTAX: every foreign merge syntax must be caught —")
for body, label in [
    ("Hey {FIRST_NAME}, quick one",        "HeyReach single-brace in an email campaign"),
    ("Hi [[first_name]] there",            "double square brackets"),
    ("Hi %FIRSTNAME% there",               "percent-delimited"),
    ("Hi *|FNAME|* there",                 "Mailchimp merge"),
    ("Hi <<first_name>> there",            "angle-bracket merge"),
    ("Hello {Company Name} team",          "single-brace with a space"),
]:
    t(f"{label}: {body[:34]!r}", fires(body), ["UNKNOWN_SYNTAX"])

t("also caught in the subject", fires("clean body", subject="re: {COMPANY}"),
  ["UNKNOWN_SYNTAX"])

print("\n— UNKNOWN_SYNTAX: ordinary copy must NOT trip it —")
for body, label in [
    ("We cut CAC by 30% last quarter",      "a percentage"),
    ("They spend $5,000 a month",           "a dollar amount with a comma"),
    ("Margins went from 20% to 45%",        "two percentages"),
    ("Hi Jane, all good at Acme.",          "plain prose"),
    ("Hi {{firstName}}, all good",          "correctly resolved double-brace"),
    ("{a|b} spun text already resolved",    "spintax leftovers are a different rule"),
    ("Use the <b>bold</b> tag",             "an html tag"),
    ("Email me at jane@acme.com",           "an email address"),
    ("Rated 4.5/5 by 200 teams",            "a fraction"),
]:
    t(f"{label}: {body[:34]!r}", fires(body), [])

print("\n— BROKEN_HANDOFF: never silently skipped —")
# No key supplied: the check must SKIP and say so, never return a clean pass that
# looks like verification happened.
camp = {"handoffs": [{"type": "SEND_LEAD_TO_INSTANTLY", "targetId": "abc-123"}]}
t("no key -> reports nothing rather than a false all-clear",
  check.chk_handoffs(camp, None), [])
t("no handoffs -> nothing to check", check.chk_handoffs({"handoffs": []}, "key"), [])
t("no campaign json -> nothing to check", check.chk_handoffs(None, "key"), [])
t("handoff without a key is reported as NOT CHECKED",
  check.rules_actually_run(campaign_json=camp)[1].get("BROKEN_HANDOFF"),
  "needs --instantly-key")

print("\n— disabled network rules make no network calls —")
calls = []
real_handoffs, real_links = check.chk_handoffs, check.chk_link_health
check.chk_handoffs = lambda *args, **kwargs: calls.append("handoff") or []
check.chk_link_health = lambda *args, **kwargs: calls.append("link") or []
try:
    check.run([{"subject": "", "body": "clean", "step": 1, "variant": "A"}],
              set(), campaign_json=camp, instantly_key="key", check_links=True,
              enabled={"EM_DASH"})
finally:
    check.chk_handoffs, check.chk_link_health = real_handoffs, real_links
t("--only skips handoff and link I/O", calls, [])

print("\n— link checker SSRF guard —")
for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8000/", "http://10.0.0.1/"):
    try:
        check._assert_public_http_url(url)
        t(f"blocks {url}", "allowed", "UnsafeURL")
    except check.UnsafeURL:
        t(f"blocks {url}", "UnsafeURL", "UnsafeURL")
try:
    check._assert_public_http_url("https://93.184.216.34/")
    t("allows a public HTTPS address", True, True)
except check.UnsafeURL as exc:
    t("allows a public HTTPS address", str(exc), "allowed")

print("\n— the guard is wired into the run —")
t("UNKNOWN_SYNTAX is in the per-row checks",
  check.chk_unknown_syntax in check.PER_ROW_CHECKS, True)
t("both rules are in the catalog",
  {"UNKNOWN_SYNTAX", "BROKEN_HANDOFF"} <= set(check.RULES), True)
t("UNKNOWN_SYNTAX is a BLOCKER, not a warning",
  check.chk_unknown_syntax({"subject": "", "body": "Hi {FIRST_NAME}"})[0][1], check.BLOCKER)

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
