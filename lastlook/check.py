"""check.py — campaign-preflight stage 3.

Reads rendered messages (preflight_<slug>.rendered.jsonl) and runs the check
catalog over every one. Deterministic checks run on 100% of messages. Emits:
  - preflight_<slug>.findings.csv  (one row per lead x variant x issue)
  - a verdict block printed to stdout (BLOCKERS / WARNINGS / CLEAR)

Each check is independent: a pure function (row) -> list[(check, severity, evidence)].
Add a check by writing a function and listing it in CHECKS. Nothing else couples.

Usage:
    python3 check.py --in preflight_acme.rendered.jsonl
    python3 check.py --in preflight_acme.rendered.jsonl --out findings.csv \
        --spam-words spam.txt
"""

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict

import difflib

from .render import BLANK, VAR_RE, SYSTEM_VARS, lead_vars, _norm_key  # shared render logic

BLOCKER, WARNING = "BLOCKER", "WARNING"

# --- rule catalog -------------------------------------------------------------
# Every check this tool can emit, in one place. Two jobs: `--list-rules` prints
# it, and `--disable` / `--only` validate against it, so a typo'd rule name is an
# error instead of a filter that silently matches nothing and reports CLEAR.
RULES = {
    # rendered-message defects
    "DATA_GAP":            "A defined variable is empty for this lead — sends a gap.",
    "BLANK_MERGE":         "A variable resolved blank with no fallback of any kind.",
    "UNDEFINED_TAG":       "Template uses a variable the campaign does not define.",
    "UNRESOLVED_VAR":      "A literal {{tag}} survived into the output and would ship.",
    "UNKNOWN_SYNTAX":      "Merge syntax the renderer does not understand ([[x]], %X%, {X}).",
    "DANGLING_TEXT":       "Text left broken where a value vanished (\"at .\", \"Hi ,\").",
    "DOUBLE_PUNCT":        "Doubled period or comma, usually at a merge seam.",
    "CLAYGENT_APOLOGY":    "AI/enrichment boilerplate (\"I couldn't find information\") in the copy.",
    "EM_DASH":             "Em or en dash used as prose punctuation.",
    "CASING":              "Shouting name or company from raw CRM casing.",
    "LEGAL_SUFFIX":        "Legal suffix (Inc, Ltd, GmbH) left on a company name mid-sentence.",
    "FULL_NAME_GREETING":  "Greeting uses a full name — the firstName field holds more than one.",
    "INVISIBLE_CHARS":     "Zero-width or non-breaking characters pasted into the copy.",
    "LENGTH":              "Message or subject over the channel's limit.",
    "SPAM_VOCAB":          "Spam-trigger vocabulary in subject or body.",
    "LINK_IN_FIRST_TOUCH": "Link in a first LinkedIn touch, which tanks acceptance.",
    "LINK_HEALTH":         "A URL in the copy is dead (needs --check-links).",
    # data quality
    "NAME_QUALITY":        "firstName value is a placeholder, role word, or malformed.",
    "COMPANY_QUALITY":     "companyName value is a placeholder or malformed.",
    # lead list
    "LEAD_NO_EMAIL":       "Lead on an email campaign has no address.",
    "LEAD_INVALID_EMAIL":  "Address is malformed and will bounce.",
    "LEAD_DUPLICATE":      "Address appears more than once — the list would send twice.",
    "LEAD_ROLE_ADDRESS":   "Role inbox (info@, noreply@) rather than a person.",
    "LEAD_FREEMAIL":       "Personal mailbox (gmail, yahoo) in a B2B list.",
    "LEAD_OVER_CONTACT":   "Many contacts at one domain — reads internally as a blast.",
    # template integrity
    "PLACEHOLDER_TEXT":    "Editing scaffolding left in (lorem ipsum, TODO, [insert]).",
    "FORBIDDEN_TERM":      "A caller-supplied banned term appears in the copy.",
    "VARIANT_NOT_DISTINCT":"Two A/B variants are effectively the same message.",
    "SHARED_OPENER":       "Variants in a step open with an identical first line.",
    "EMPTY_SUBJECT":       "First-touch email has no subject line.",
    "SUBJECT_STYLE":       "Emoji, repeated exclamation, or shouting in the subject.",
    "THREAD_BREAK":        "Follow-up sets its own subject, starting a new thread.",
    "STEPS_NOT_PACED":     "Consecutive steps send with no day gap between them.",
    # structural
    "AB_SIGNAL_COLLISION": "Two variants in a step target the same signal.",
    "BROKEN_HANDOFF":      "A HeyReach handoff points at an Instantly campaign that is gone.",
}

# --- patterns -----------------------------------------------------------------

# A blank merge leaves a strong tell: a preposition/greeting butting into
# punctuation, or empty parens. Those mean a value vanished -> BLOCKER.
# The tell of a collapsed merge is the SPACE left where the value used to be:
# "at {{x}}." renders to "at ." and "Hi {{firstName}}," to "Hi ,". Requiring that
# space (\s+, not \s*) is what separates a real gap from ordinary English.
# With \s* this matched "someone I could speak with?", "get in touch with,",
# "who to reach out to." and the deliberate no-name opener "Hey, not sure if…" —
# 720 false BLOCKERS across a 34-campaign sweep, on grammatical copy.
DANGLING_BLOCKER_RE = re.compile(
    r"(\b(?:at|with|for|to|from|of)\s+[.,!?]"   # "...spend at ."
    r"|\b(?:hi|hey|hello|dear)\s+[,!.]"          # "Hi ,"
    r"|\(\s*\))",                                  # "( )"
    re.IGNORECASE,
)
# Cosmetic spacing tells (space before comma/period, collapsed double space) are
# usually a trailing space in CRM data -> sloppy but sendable -> WARNING.
# Horizontal whitespace only. This was `\s{2,}`, which matches the "\n\n" between
# paragraphs — i.e. every correctly formatted email. It fired on 54,004 of 55,000
# benchmark messages, so the one signal that mattered was buried in noise.
DANGLING_WARN_RE = re.compile(r"([ \t]+[,.](?:\s|$)|[ \t]{2,})")
CLAYGENT_RE = re.compile(
    r"\b(i\s+(?:couldn'?t|could not|was unable to|cannot|can'?t)\s+find"
    r"|i\s+do\s*n'?t\s+have"
    r"|i\s+do\s+not\s+have"
    r"|as\s+an\s+ai"
    r"|unable\s+to\s+(?:find|determine|locate)"
    r"|no\s+(?:information|data)\s+(?:available|found)"
    r"|not\s+enough\s+information"
    r"|i\s+don'?t\s+have\s+enough"
    r"|based\s+on\s+the\s+(?:provided|available)"
    r"|n/?a\b"
    r"|null\b"
    r"|undefined\b)",
    re.IGNORECASE,
)
# All-caps tokens only matter where a NAME lands — after a greeting or "at <co>".
# Scanning whole-body prose just flags legitimate acronyms (EDC, GCP, ICH...).
# Require 2+ leading caps so "Jane" is fine but "BOB"/"JANE" are flagged.
# Greeting word is case-flexible; the captured name must be shouting.
GREETING_NAME_RE = re.compile(r"(?:Hi|Hey|Hello|Dear|HI|HEY)[\s,]+([A-Z]{2,}[A-Z'’.-]*)\b", re.MULTILINE)
START_NAME_RE = re.compile(r"^\s*([A-Z]{2,}[A-Z'’.-]*)\s*,", re.MULTILINE)
AT_COMPANY_RE = re.compile(r"\bat\s+([A-Z]{2,}[A-Z'’.-]*)\b")
LEGAL_SUFFIX_RE = re.compile(r"\b(inc|llc|ltd|corp|gmbh|co|sa|srl|bv|plc)\.?\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://|www\.|\b\w+\.(?:com|io|ai|co|net|org)\b", re.IGNORECASE)
LEFTOVER_VAR_RE = re.compile(r"\{\{.*?\}\}|\{[^{}]*\|[^{}]*\}")
EMDASH_RE = re.compile(r"[—–]")  # em dash and en dash both banned in copy

# Constructs that LOOK like a merge tag from some ESP but were NOT substituted by
# our renderer — i.e. a syntax we don't understand. Surviving any of these into a
# rendered message means it would ship literally to a prospect. This is the guard
# against the false-green class: every live platform quirk (HeyReach {SINGLE},
# Instantly {{RANDOM}}) first showed up as one of these. New quirk -> loud flag,
# never a silent pass. Patterns deliberately avoid common prose ("30%", "$5,000").
UNKNOWN_SYNTAX_RES = [
    ("[[...]]",  re.compile(r"\[\[[^\]\n]{1,40}\]\]")),
    ("%VAR%",    re.compile(r"%[A-Za-z][A-Za-z0-9_ ]{0,38}%")),
    ("*|MERGE|*", re.compile(r"\*\|[^|\n]{1,40}\|\*")),
    ("<<...>>",  re.compile(r"<<[^<>\n]{1,40}>>")),
    # true single-brace {Var} (not part of {{...}}, no pipe -> not spintax)
    ("{VAR}",    re.compile(r"(?<!\{)\{(?!\{)[A-Za-z_][\w .'\-]{0,38}\}(?!\})")),
]

DEFAULT_SPAM = {
    "free", "guarantee", "guaranteed", "risk-free", "act now", "limited time",
    "click here", "buy now", "100%", "cash", "cheap", "discount", "earn $",
    "no obligation", "no cost", "winner", "congratulations", "urgent",
}

# --- individual checks --------------------------------------------------------
# Each returns a list of (check_name, severity, evidence_snippet).


def _snip(text, m, pad=22):
    s, e = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if s > 0 else "") + text[s:e].replace("\n", " ").strip() + ("…" if e < len(text) else "")


def _sig(field, m):
    """Dedup signature: what the defect IS, stripped of who it happened to.

    `_snip` pads a match with surrounding rendered text so a human can read it,
    which means the lead's name and company land inside the string. Keying dedup
    on that made every lead look like a distinct issue — one em dash in one
    template subject reported as 111 issues over 35,000 leads. The signature is
    the matched text alone, so a template defect collapses to one issue per
    variant while genuinely lead-specific values stay separate."""
    return f"{field}:{m.group()}"


# A signature carrying this prefix marks a DATA defect rather than a template
# one, and dedup drops step/variant from its key. The distinction is "how many
# things do I have to go fix": a bad template is one fix per variant that
# contains it, but a shouting company name is one CRM record no matter how many
# variants render it. Keying data defects per variant inflated CASING from 19
# issues to 109 for the same 19 bad values.
DATA_SIG = "data:"


def chk_blank_merge(row):
    """Report the gaps a message will actually send, classified by certainty/kind:
      - DATA_GAP: a known/defined variable that's empty for THIS lead -> sends a
        gap for this specific lead (fix: enrich the lead or add a fallback).
      - BLANK_MERGE: same, on a platform with no variable registry (HeyReach /
        fixtures) where we can't classify further.
    Undefined tags are NOT reported here — they're the campaign-level UNDEFINED_TAG
    (a template bug hitting everyone), so reporting them per-lead too would double up."""
    out = []
    for t in row.get("blanked_data_gap", []):
        out.append(("DATA_GAP", BLOCKER,
                    f"{{{{{t}}}}} is empty for this lead — no value, no fallback; sends a gap"))
    for t in row.get("blanked_unknown", []):
        out.append(("BLANK_MERGE", BLOCKER,
                    f"{{{{{t}}}}} resolved blank with no fallback — sends a gap"))
    return out


def chk_dangling(row):
    # surface tells of a blank merge even when the sentinel was overwritten by spintax
    out = []
    for field in ("subject", "body"):
        text = row.get(field, "")
        if BLANK in row.get(field + "_raw", ""):
            continue  # already caught by chk_blank_merge, don't double-count
        mb = DANGLING_BLOCKER_RE.search(text)
        if mb:
            out.append(("DANGLING_TEXT", BLOCKER, f"{field}: “{_snip(text, mb)}”", _sig(field, mb)))
            continue
        mw = DANGLING_WARN_RE.search(text)
        if mw:
            out.append(("DANGLING_TEXT", WARNING, f"{field}: stray spacing “{_snip(text, mw)}”",
                        f"{field}:stray-spacing"))
    return out


def chk_unresolved_var(row):
    # A literal {{tag}} that survived into the output (renderer couldn't even
    # parse it) ships visibly to the prospect — always broken.
    out = []
    for field in ("subject", "body"):
        m = LEFTOVER_VAR_RE.search(row.get(field, ""))
        if m:
            out.append(("UNRESOLVED_VAR", BLOCKER,
                        f"{field}: literal tag survived “{_snip(row[field], m)}”", _sig(field, m)))
    return out


def chk_unknown_syntax(row):
    out = []
    for field in ("subject", "body"):
        text = row.get(field, "")
        for label, rx in UNKNOWN_SYNTAX_RES:
            m = rx.search(text)
            if m:
                out.append(("UNKNOWN_SYNTAX", BLOCKER,
                            f"{field}: unhandled {label} merge tag “{_snip(text, m)}” "
                            f"— renderer did not substitute it", f"{field}:{label}"))
                break  # one report per field is enough
    return out


def chk_claygent(row):
    out = []
    for field in ("subject", "body"):
        m = CLAYGENT_RE.search(row.get(field, ""))
        if m:
            # The apology text comes from an enrichment column, not the template —
            # one bad Claygent output, however many variants render it.
            out.append(("CLAYGENT_APOLOGY", BLOCKER,
                        f"{field}: AI/enrichment artifact “{_snip(row[field], m)}”",
                        f"{DATA_SIG}ai:{m.group().lower()}"))
    return out


DOUBLE_PUNCT_RE = re.compile(r"(?<!\.)\.\.(?!\.)|,,")  # ".." (not ellipsis) or ",,"
FULL_NAME_GREET_RE = re.compile(
    r"\b(?:Hi|Hey|Hello|Dear)\s+([A-Z][a-z]+\s+[A-Z][a-z'’.\-]+)\s*[,!]")


def chk_invisible_chars(row):
    """Invisible characters in the copy — zero-width spaces, non-breaking spaces,
    soft hyphens. Pasted in from Docs/Notion/Word and impossible to see in any
    campaign UI. A zero-width space sitting inside a word is what obfuscated spam
    looks like to a filter, and it silently breaks any text matching over the copy.
    The renderer strips them; this reports that they were there."""
    inv = row.get("invisibles") or []
    if not inv:
        return []
    return [("INVISIBLE_CHARS", WARNING,
             f"invisible character(s) in copy: {', '.join(inv)} — strip them in the template",
             f"invisible:{','.join(inv)}")]


def chk_double_punct(row):
    # doubled period/comma at a merge seam or as a typo — e.g. "together.. " or
    # "Inc.." (value ends in a period, template adds another). Sloppy -> WARNING.
    out = []
    for field in ("subject", "body"):
        m = DOUBLE_PUNCT_RE.search(row.get(field, ""))
        if m:
            out.append(("DOUBLE_PUNCT", WARNING,
                        f"{field}: doubled punctuation “{_snip(row[field], m)}”", _sig(field, m)))
    return out


def chk_full_name_greeting(row):
    # "Hi John Smith," — the firstName field holds a full name; reads scraped.
    out = []
    for field in ("subject", "body"):
        m = FULL_NAME_GREET_RE.search(row.get(field, ""))
        if m:
            out.append(("FULL_NAME_GREETING", WARNING,
                        f"{field}: greeting uses full name “{m.group(1)}” (firstName field likely not just the first name)",
                        f"{DATA_SIG}fullname:{m.group(1)}"))
    return out


def chk_emdash(row):
    # The ban is on the em/en dash used as PROSE punctuation (the AI tell).
    # An en dash inside a numeric range ("11–15 hours") is correct typography —
    # don't flag it.
    out = []
    for field in ("subject", "body"):
        text = row.get(field, "")
        for m in EMDASH_RE.finditer(text):
            i = m.start()
            if m.group() == "–":
                left = text[i - 1] if i > 0 else ""
                right = text[i + 1] if i + 1 < len(text) else ""
                if left.isdigit() and right.isdigit():
                    continue  # numeric range, legitimate
            out.append(("EM_DASH", BLOCKER, f"{field}: banned dash “{_snip(text, m)}”", _sig(field, m)))
            break
    return out


def chk_casing(row):
    out = []
    subj, body = row.get("subject", ""), row.get("body", "")
    seen = set()
    for field, text in (("subject", subj), ("body", body)):
        for rx in (GREETING_NAME_RE, START_NAME_RE, AT_COMPANY_RE):
            m = rx.search(text)
            if m:
                tok = m.group(1)
                if tok not in seen:
                    seen.add(tok)
                    out.append(("CASING", WARNING, f"{field}: shouting name “{tok}” (raw CRM casing)",
                                f"{DATA_SIG}shouting:{tok}"))
    for field, text in (("subject", subj), ("body", body)):
        m = LEGAL_SUFFIX_RE.search(text)
        if m:
            # The suffix rides in on the company value, so it is one CRM fix.
            out.append(("LEGAL_SUFFIX", WARNING, f"{field}: legal suffix in name “{_snip(text, m, 14)}”",
                        f"{DATA_SIG}suffix:{m.group().lower()}"))
            break
    return out


def chk_length(row):
    out = []
    limits = row.get("limits") or {}
    for field, key in (("subject", "subject"), ("body", "body")):
        cap = limits.get(key)
        if cap and len(row.get(field, "")) > cap:
            # Signature omits the measured length: the same over-limit template
            # renders to a different char count per lead, which would otherwise
            # report one issue per lead instead of one per variant.
            out.append(("LENGTH", BLOCKER,
                        f"{field} is {len(row[field])} chars, max {cap} ({row.get('channel')})",
                        f"{field}:over-{cap}"))
    # email subject sanity (no hard cap, but long subjects hurt)
    if row.get("channel") == "email" and len(row.get("subject", "")) > 60:
        out.append(("LENGTH", WARNING, f"subject {len(row['subject'])} chars (>60 hurts open rates)",
                    "subject:over-60"))
    return out


def chk_link_first_touch(row):
    # Links tank acceptance on a connection request and reply rate on a first DM.
    # InMail is a paid cold message where links are normal practice — exclude it.
    out = []
    if row.get("channel") in ("connection_request", "message") and row.get("step") in (1, "1"):
        m = URL_RE.search(row.get("body", ""))
        if m:
            out.append(("LINK_IN_FIRST_TOUCH", WARNING,
                        f"link in first LinkedIn touch “{_snip(row['body'], m)}”", _sig("body", m)))
    return out


def _term_pat(w):
    """One term, with a word boundary only where the term's edge IS a word char.
    \\b between "%" and a following space never matches, so a blanket \\b(...)\\b
    made "100%" and any other term ending in punctuation unmatchable in prose."""
    p = re.escape(w)
    if w[:1].isalnum() or w[:1] == "_":
        p = r"\b" + p
    if w[-1:].isalnum() or w[-1:] == "_":
        p += r"\b"
    return p


def make_spam_check(words):
    pat = re.compile("(" + "|".join(_term_pat(w) for w in sorted(words, key=len, reverse=True)) + ")",
                     re.IGNORECASE) if words else None

    def chk_spam(row):
        if not pat:
            return []
        m = pat.search(row.get("body", "")) or pat.search(row.get("subject", ""))
        return [("SPAM_VOCAB", WARNING, f"spam-trigger word “{m.group()}”")] if m else []

    return chk_spam


# --- campaign-level checks (run once over all rows, not per-row) ---------------


def chk_handoffs(campaign_json, instantly_key):
    """Cross-platform: verify each HeyReach SEND_LEAD_TO_INSTANTLY target still
    exists in Instantly. Skipped (and logged) when the campaign JSON or an
    Instantly key isn't supplied — the broken-pipe check no UI offers."""
    out = []
    if not campaign_json:
        return out
    handoffs = (campaign_json.get("handoffs") or [])
    if not handoffs:
        return out
    if not instantly_key:
        print(f"NOTE: {len(handoffs)} Instantly handoff(s) NOT verified "
              f"(pass --instantly-key to check them).")
        return out
    import httpx
    cx = httpx.Client(base_url="https://api.instantly.ai/api/v2",
                      headers={"Authorization": f"Bearer {instantly_key}",
                               "User-Agent": "campaign-preflight/1.0"}, timeout=20.0)
    for h in handoffs:
        tid = h.get("targetId")
        ok = False
        try:
            ok = cx.get(f"/campaigns/{tid}").status_code == 200
        except Exception:
            ok = False
        if not ok:
            out.append({
                "lead_id": "(campaign-level)", "lead_email": "", "step": "",
                "variant": "", "channel": "handoff",
                "check": "BROKEN_HANDOFF", "severity": BLOCKER,
                "evidence": f"SEND_LEAD_TO_INSTANTLY target {tid} not found in Instantly",
            })
    cx.close()
    return out


HTTP_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+")


def chk_link_health(rows, enabled):
    """Resolve every distinct URL in the rendered copy and flag dead ones — a
    404/dead link in a live campaign means recipients click into nothing.
    Campaign-level: dedup URLs first (template links repeat across leads), so
    it's a handful of requests, not one per lead. Off unless --check-links."""
    out = []
    if not enabled:
        return out
    urls = {}  # url -> (step, variant) first seen
    for r in rows:
        for field in ("subject", "body"):
            for m in HTTP_URL_RE.finditer(r.get(field, "")):
                u = m.group().rstrip(".,;:!?")  # drop trailing sentence punctuation
                urls.setdefault(u, (r.get("step"), r.get("variant")))
    if not urls:
        return out
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    # Network-bound and independent per URL, so fetch concurrently. Serially this
    # was (distinct URLs x up to 12s) and was the only part of a check run that
    # took real time; a campaign with a dozen links and one slow host could sit
    # for a minute. Capped at 8 so we never look like a scraper to one host.
    cx = httpx.Client(timeout=12.0, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 campaign-preflight"})

    def probe(item):
        u, (step, variant) = item
        try:
            resp = cx.get(u)
            return (u, step, variant, f"HTTP {resp.status_code}") if resp.status_code >= 400 else None
        except Exception as e:
            return (u, step, variant, type(e).__name__)

    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        results = list(pool.map(probe, urls.items()))
    cx.close()

    for res in results:
        if res is None:
            continue
        u, step, variant, why = res
        out.append({
            "lead_id": "(campaign-level)", "lead_email": "", "step": step,
            "variant": variant, "channel": "link",
            # low-priority: surfaces only when a link is actually dead, and as a
            # warning so it never blocks an otherwise-clean launch
            "check": "LINK_HEALTH", "severity": WARNING,
            "evidence": f"dead link ({why}): {u[:80]}",
        })
    return out


# --- name-quality classification (run on the actual firstName value) ----------

NAME_TAG_KEYS = {"firstname", "first", "fname", "name", "fullname"}
# exact placeholder / role tokens that are not a person's first name
NAME_PLACEHOLDERS = {
    "there", "friend", "test", "none", "null", "na", "unknown", "customer",
    "user", "guest", "info", "sales", "admin", "support", "team", "hello",
    "hi", "sir", "madam", "owner", "founder", "ceo", "manager", "contact",
    "prospect", "lead", "recipient", "valued", "member", "subscriber",
    "firstname", "first name", "your name", "name", "n/a", "tbd", "xxx",
    "decision maker", "to whom it may concern",
}
HONORIFIC_RE = re.compile(r"^(mr|mrs|ms|miss|mx|dr|prof|sir|rev)\.?\s", re.I)
CREDENTIAL_RE = re.compile(r"[,\s](phd|md|mba|esq|cpa|jr|sr|dds|do|rn|cfa|pmp|iii|iv)\b\.?", re.I)
ODD_CHAR_RE = re.compile(r"[()\[\]{}<>/\\|*=+~^@#$%\"`_]")
MOJIBAKE_RE = re.compile(r"Ã.|Â.|â€|ð\x9f")


def classify_name(value):
    """Return a one-line reason if a firstName value looks broken/weird, else None.
    Passes legit names: accented (José), hyphenated (Jean-Pierre), apostrophe
    (O'Brien), initials (J.T.). Casing and plain full-names are handled by
    CASING / FULL_NAME_GREETING, so this skips them to avoid double-reporting."""
    v = value.strip()
    if not v:
        return None
    low = v.lower().strip(".")
    if low in NAME_PLACEHOLDERS:
        return "placeholder / role word, not a name"
    if any(ch.isdigit() for ch in v):
        return "contains digits"
    if "@" in v or re.search(r"https?://|www\.|\.(com|io|net|org|ai)\b", v, re.I):
        return "looks like an email or URL"
    if MOJIBAKE_RE.search(v):
        return "encoding garbage (mojibake)"
    # emoji / non-latin script (allow latin + latin-1/extended accents, common typography)
    for ch in v:
        o = ord(ch)
        if o >= 0x2100 and not (0x2010 <= o <= 0x2027):
            return "non-name character (emoji / non-latin script)"
    if ODD_CHAR_RE.search(v):
        return "odd characters"
    if HONORIFIC_RE.match(v):
        return "honorific prefix (Mr/Dr/...)"
    if CREDENTIAL_RE.search(v):
        return "credential suffix (PhD/MD/Jr/...)"
    if "," in v:
        return "comma in name (last,first format?)"
    if re.search(r"\s&\s|\sand\s|\s\+\s", v, re.I):
        return "multiple names (& / and)"
    if len(v) > 35:
        return "too long to be a first name (text leaked into the field?)"
    return None


def chk_name_quality(campaign_json):
    """Examine the actual firstName value for every lead and flag broken/weird
    ones. Only runs if a template actually uses a name tag (otherwise a bad name
    value is harmless). Per-lead findings so the CSV pins which leads to fix;
    dedups by (reason, value) in the verdict. WARNING — reads bad, sendable."""
    out = []
    if not campaign_json:
        return out
    # does any template use a first-name merge tag?
    uses_name = False
    for step in campaign_json.get("steps", []):
        for v in step.get("variants", []):
            for m in VAR_RE.finditer((v.get("subject") or "") + "\n" + (v.get("body") or "")):
                if _norm_key(m.group(1)) in NAME_TAG_KEYS:
                    uses_name = True
    if not uses_name:
        return out
    for lead in campaign_json.get("leads", []):
        vmap = lead_vars(lead)
        name = next((vmap[k] for k in ("firstname", "first", "fname", "name") if vmap.get(k)), None)
        if name is None:
            continue
        reason = classify_name(name)
        if reason:
            out.append({
                "lead_id": lead.get("id") or lead.get("email"),
                "lead_email": lead.get("email", ""), "step": "", "variant": "",
                "channel": "name", "check": "NAME_QUALITY", "severity": WARNING,
                "evidence": f"firstName “{name[:40]}” — {reason}",
            })
    return out


# --- template integrity (runs on the TEMPLATES, once, not per rendered row) ---
# These defects live in the copy itself, so a lead adds nothing: checking them
# once per variant is both cheaper and the right granularity for the verdict.

# Editing scaffolding that was never taken back out. Anchored hard, because the
# words themselves are ordinary: "we test your pipeline" must stay silent while a
# bare "TEST" or "[insert value]" must not.
PLACEHOLDER_RES = [
    ("lorem ipsum",   re.compile(r"\blorem ipsum\b", re.I)),
    ("bracketed slot", re.compile(r"[\[<]\s*(insert|your|company|name|value|x+|tbd|todo)[^\]\n>]{0,30}[\]>]", re.I)),
    ("TODO/FIXME",    re.compile(r"\b(TODO|FIXME|XXX)\b")),          # caps only: "todo" appears in prose
    ("bare TEST",     re.compile(r"(?<![\w-])TEST(?![\w-])")),        # caps only: "test" is a real word
    ("filler phrase", re.compile(r"\b(your company here|company name here|sample text|placeholder)\b", re.I)),
]

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
BANG_RE = re.compile(r"!{2,}")
# 4+ consecutive caps words: "URGENT OFFER INSIDE NOW". Short acronyms are fine.
SHOUT_RE = re.compile(r"\b[A-Z]{2,}\b(?:\s+\b[A-Z]{2,}\b){3,}")


def _template_texts(campaign_json):
    """Yield (step_no, channel, variant_id, field, text) for every live variant."""
    for step in campaign_json.get("steps", []) or []:
        for v in step.get("variants", []) or []:
            if v.get("disabled"):
                continue
            for field in ("subject", "body"):
                yield step.get("step"), step.get("channel", "email"), v.get("id"), field, v.get(field) or ""


def _tpl_finding(step, variant, checkname, severity, evidence, signature):
    return {
        "lead_id": "(template)", "lead_email": "", "step": step, "variant": variant,
        "channel": "template", "check": checkname, "severity": severity,
        "evidence": evidence, "signature": signature,
    }


def chk_placeholders(campaign_json):
    """Editing scaffolding left in the copy — the kind that ships to a prospect
    and cannot be explained away."""
    out = []
    if not campaign_json:
        return out
    for step, _chan, vid, field, text in _template_texts(campaign_json):
        for label, rx in PLACEHOLDER_RES:
            m = rx.search(text)
            if m:
                out.append(_tpl_finding(step, vid, "PLACEHOLDER_TEXT", BLOCKER,
                                        f"{field}: {label} left in the template “{_snip(text, m)}”",
                                        f"{field}:{label}"))
                break
    return out


def chk_forbidden_terms(campaign_json, terms):
    """Caller-supplied banned words. The real use is a previous client's or a
    competitor's name surviving into a recycled template."""
    out = []
    if not campaign_json or not terms:
        return out
    pat = re.compile("(" + "|".join(_term_pat(t) for t in sorted(terms, key=len, reverse=True)) + ")",
                     re.IGNORECASE)
    for step, _chan, vid, field, raw in _template_texts(campaign_json):
        # Blank out merge tags first. A variable NAMED {{competitor}} is
        # scaffolding, not words a prospect ever reads — matching on it flags a
        # perfectly clean template.
        text = re.sub(r"\{\{.*?\}\}", " ", raw)
        m = pat.search(text)
        if m:
            out.append(_tpl_finding(step, vid, "FORBIDDEN_TERM", BLOCKER,
                                    f"{field}: banned term “{m.group()}” in the copy",
                                    f"{field}:{m.group().lower()}"))
    return out


def _normalize_copy(text):
    """Collapse a template to its prose shape so two variants can be compared:
    merge tags out (they are identical scaffolding), whitespace and case flattened."""
    t = re.sub(r"\{\{.*?\}\}", " ", text)
    t = re.sub(r"\{[^{}]*\}", " ", t)          # spintax groups
    return re.sub(r"\s+", " ", t).strip().lower()


def chk_variant_integrity(campaign_json):
    """A duplicated variant makes the A/B test measure nothing, and an identical
    opening line across variants wastes the only part a prospect reliably reads.

    Duplication means IDENTICAL after merge tags and whitespace are normalized
    away — no similarity threshold. A first pass flagged anything ≥95% alike,
    which caught a live Initech pair differing only in "I've already built a pitch
    deck" vs "I can build a pitch deck in minutes": a textbook single-variable
    test, i.e. exactly the behaviour you want. Raising the bar to 99% just moved
    the arbitrariness, since the ratio depends on body length rather than on
    whether anything is wrong. If two variants differ at all, the author changed
    something on purpose and can see what it was."""
    out = []
    if not campaign_json:
        return out
    for step in campaign_json.get("steps", []) or []:
        live = [v for v in (step.get("variants") or []) if not v.get("disabled")]
        if len(live) < 2:
            continue
        sn = step.get("step")
        bodies = {v.get("id"): _normalize_copy(v.get("body") or "") for v in live}
        ids = list(bodies)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = bodies[ids[i]], bodies[ids[j]]
                if not a or not b or a != b:
                    continue
                pair = " / ".join(sorted((str(ids[i]), str(ids[j]))))
                out.append(_tpl_finding(
                    sn, pair, "VARIANT_NOT_DISTINCT", BLOCKER,
                    f"variants {pair} are the same message — the A/B test measures nothing",
                    f"step{sn}:{pair}"))
        # opening line: the first non-empty line of each body
        openers = {}
        for v in live:
            first = next((ln.strip() for ln in (v.get("body") or "").splitlines() if ln.strip()), "")
            openers.setdefault(_normalize_copy(first), []).append(str(v.get("id")))
        for opener, vids in openers.items():
            if opener and len(vids) > 1:
                pair = " / ".join(sorted(vids))
                out.append(_tpl_finding(
                    sn, pair, "SHARED_OPENER", WARNING,
                    f"variants {pair} open with the same line “{opener[:50]}”",
                    f"step{sn}:opener:{opener[:40]}"))
    return out


def chk_step_pacing(campaign_json):
    """Consecutive steps with no real pause between them.

    Two emails landing the same day reads as a malfunction, not a follow-up, and
    it is the pattern most likely to earn a manual spam complaint — which is
    scored against the sending domain, not the campaign. One day is aggressive
    but defensible, so it warns rather than blocks.

    The gap between step N and N+1 is step N's `delay_days` (see the note in
    pull_instantly.extract_steps). A step with no stated delay is skipped rather
    than assumed to be zero: silence about pacing is not evidence of bad pacing."""
    out = []
    if not campaign_json:
        return out
    steps = [s for s in (campaign_json.get("steps") or [])
             if s.get("channel", "email") == "email"]
    for cur, nxt in zip(steps, steps[1:]):
        gap = cur.get("delay_days")
        if gap is None:
            continue
        a, b = cur.get("step"), nxt.get("step")
        if gap <= 0:
            out.append(_tpl_finding(
                b, "", "STEPS_NOT_PACED", BLOCKER,
                f"steps {a} and {b} send with no gap — two emails the same day",
                f"pacing:{a}-{b}:same-day"))
        elif gap < 2:
            out.append(_tpl_finding(
                b, "", "STEPS_NOT_PACED", WARNING,
                f"only {gap:g} day between steps {a} and {b} — aggressive for a cold follow-up",
                f"pacing:{a}-{b}:{gap:g}d"))
    return out


def chk_subject_integrity(campaign_json):
    """Subject-line defects, judged on the template rather than per lead."""
    out = []
    if not campaign_json:
        return out
    email_steps = [s for s in (campaign_json.get("steps") or [])
                   if s.get("channel", "email") == "email"]
    for step in email_steps:
        sn = step.get("step")
        first_step = str(sn) in ("1", "None") or sn == 1
        for v in (step.get("variants") or []):
            if v.get("disabled"):
                continue
            vid, subj = v.get("id"), (v.get("subject") or "").strip()
            if not subj:
                # Empty on a follow-up is how an ESP threads the reply — correct
                # and deliberate. Empty on the opener is a campaign with no subject.
                if first_step:
                    out.append(_tpl_finding(sn, vid, "EMPTY_SUBJECT", BLOCKER,
                                            "first-touch email has no subject line",
                                            "subject:empty-step1"))
                continue
            if EMOJI_RE.search(subj):
                out.append(_tpl_finding(sn, vid, "SUBJECT_STYLE", WARNING,
                                        f"emoji in subject “{subj[:50]}”", "subject:emoji"))
            if BANG_RE.search(subj):
                out.append(_tpl_finding(sn, vid, "SUBJECT_STYLE", WARNING,
                                        f"repeated exclamation in subject “{subj[:50]}”", "subject:bangs"))
            if SHOUT_RE.search(subj):
                out.append(_tpl_finding(sn, vid, "SUBJECT_STYLE", WARNING,
                                        f"shouting subject “{subj[:50]}”", "subject:allcaps"))

    # A follow-up that carries its OWN subject starts a new email thread instead
    # of replying under the first one. Sometimes intended, usually not, so warn.
    if len(email_steps) > 1:
        for step in email_steps[1:]:
            sn = step.get("step")
            for v in (step.get("variants") or []):
                if v.get("disabled"):
                    continue
                subj = (v.get("subject") or "").strip()
                if subj and not subj.lower().startswith("re:"):
                    out.append(_tpl_finding(
                        sn, v.get("id"), "THREAD_BREAK", WARNING,
                        f"follow-up sets its own subject “{subj[:40]}” — starts a new thread "
                        f"rather than replying under the first email",
                        f"step{sn}:thread-break"))
    return out


# --- lead-list hygiene (runs on the LIST, before anything is rendered) --------
# Nothing else in this file looks at the list itself. These are the failures that
# cost sending-domain reputation rather than one embarrassing message: a bounce
# and a duplicate send are scored against the domain, and a burned domain takes
# weeks to recover. Cheap to check, expensive to miss.

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
ROLE_LOCALPARTS = {
    "info", "support", "sales", "contact", "admin", "help", "office", "hello",
    "team", "billing", "accounts", "accounting", "hr", "jobs", "careers",
    "noreply", "no-reply", "donotreply", "postmaster", "webmaster", "abuse",
    "marketing", "press", "media", "legal", "privacy", "security", "enquiries",
    "inquiries", "general", "mail", "email", "service", "customerservice",
}
FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "me.com", "live.com", "msn.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.de", "mail.com", "yandex.com", "zoho.com", "yahoo.co.uk",
    "hotmail.co.uk", "googlemail.com", "web.de", "free.fr", "orange.fr",
}
# Above this many contacts at one domain, a campaign reads internally as a blast:
# colleagues compare notes and the whole account sours at once.
OVER_CONTACT_THRESHOLD = 5


def _lead_finding(lead, checkname, severity, evidence, signature):
    return {
        "lead_id": lead.get("id") or lead.get("email"),
        "lead_email": lead.get("email", ""), "step": "", "variant": "",
        "channel": "list", "check": checkname, "severity": severity,
        "evidence": evidence, "signature": signature,
    }


def chk_lead_list(campaign_json, over_contact=OVER_CONTACT_THRESHOLD):
    """Hygiene of the lead list itself: duplicates, undeliverable addresses,
    role inboxes, free-mail in a B2B list, and over-contacting one company.

    Email-address rules apply only to campaigns that actually send email. A
    LinkedIn lead has no mailbox by design, so running them over a HeyReach list
    flags every single lead — the exact cry-wolf failure that gets a checker
    switched off."""
    out = []
    if not campaign_json:
        return out
    leads = campaign_json.get("leads") or []
    if not leads:
        return out
    steps = campaign_json.get("steps") or []
    # No steps to judge by -> treat as email, matching this codebase's default
    # channel everywhere else. Only an explicitly all-LinkedIn campaign opts out.
    if steps and not any(s.get("channel", "email") == "email" for s in steps):
        return out

    seen, by_domain = {}, {}
    for lead in leads:
        email = (lead.get("email") or "").strip()
        if not email:
            out.append(_lead_finding(lead, "LEAD_NO_EMAIL", BLOCKER,
                                     "lead has no email address", "data:lead:no-email"))
            continue
        low = email.lower()

        if not EMAIL_RE.match(low):
            out.append(_lead_finding(lead, "LEAD_INVALID_EMAIL", BLOCKER,
                                     f"“{email[:50]}” is not a valid address — it will bounce",
                                     f"data:lead:invalid:{low[:50]}"))
            continue

        local, _, domain = low.partition("@")
        if low in seen:
            # Report the SECOND and later occurrences: the first is the keeper.
            out.append(_lead_finding(lead, "LEAD_DUPLICATE", BLOCKER,
                                     f"“{email}” appears more than once — the list would send twice",
                                     f"data:lead:dupe:{low}"))
        else:
            seen[low] = True

        if local.replace(".", "").replace("_", "") in ROLE_LOCALPARTS:
            out.append(_lead_finding(lead, "LEAD_ROLE_ADDRESS", BLOCKER,
                                     f"“{email}” is a role inbox, not a person — high bounce and complaint risk",
                                     f"data:lead:role:{local}"))
        if domain in FREEMAIL_DOMAINS:
            out.append(_lead_finding(lead, "LEAD_FREEMAIL", WARNING,
                                     f"“{email}” is a personal mailbox, not a company domain",
                                     f"data:lead:freemail:{domain}"))
        by_domain.setdefault(domain, []).append(email)

    for domain, addrs in sorted(by_domain.items()):
        if len(addrs) >= over_contact:
            out.append({
                "lead_id": "(campaign-level)", "lead_email": "", "step": "", "variant": "",
                "channel": "list", "check": "LEAD_OVER_CONTACT", "severity": WARNING,
                "evidence": (f"{len(addrs)} contacts at {domain} — colleagues comparing "
                             f"the same email reads as a blast"),
                "signature": f"data:lead:overcontact:{domain}",
            })
    return out


# --- company-quality classification (run on the actual companyName value) -----

COMPANY_TAG_KEYS = {"companyname", "company", "account", "accountname",
                    "organization", "organisation", "org"}
COMPANY_PLACEHOLDERS = {
    "company", "company name", "companyname", "your company", "the company",
    "test", "null", "na", "n/a", "unknown", "none", "tbd", "xxx", "-",
    "your team", "example", "sample", "no company", "self", "n.a.",
}
# legit brands break naive rules: 3M / 7-Eleven (digits), AT&T / J&J (&),
# Coca-Cola (hyphen), O'Reilly (apostrophe), eBay (lowercase start), IBM (caps).
# So: digits allowed unless ALL-numeric; &, -, ', ! allowed; flag only clear junk.
COMPANY_ODD_RE = re.compile(r"[|/\\<>{}\[\]()=~^@#]")


def classify_company(value):
    v = value.strip()
    if not v:
        return None
    low = v.lower().strip(". ")
    if low in COMPANY_PLACEHOLDERS:
        return "placeholder, not a real company name"
    # Only explicit URLs — NOT bare branded names like Customer.io / Vue.ai / Abacus.AI,
    # which legitimately carry a .io/.ai/.com suffix and are the real company name.
    if re.search(r"https?://|www\.|@|/", v):
        return "looks like a URL / email"
    if v.replace(" ", "").isdigit():
        return "numeric, not a company name"
    if MOJIBAKE_RE.search(v):
        return "encoding garbage (mojibake)"
    for ch in v:
        o = ord(ch)
        if o >= 0x2100 and not (0x2010 <= o <= 0x2027):
            return "non-latin / emoji characters"
    if COMPANY_ODD_RE.search(v):
        return "odd characters (tagline / pipe / brackets?)"
    # shouting: 2+ consecutive all-caps words, or one long all-caps token (>7)
    if re.search(r"\b[A-Z]{2,}\b\s+\b[A-Z]{2,}\b", v) or (v.isupper() and len(v) > 7):
        return "all-caps (raw CRM casing)"
    if v.islower() and len(v) >= 4:
        return "all-lowercase (raw CRM casing)"
    if len(v) > 50:
        return "too long (tagline / description leaked into the field?)"
    return None


def chk_company_quality(campaign_json):
    """Value-level company-name check, mirror of NAME_QUALITY. Only runs if a
    template uses a company tag. Legal suffixes are left to LEGAL_SUFFIX (copy
    level) to avoid double-reporting."""
    out = []
    if not campaign_json:
        return out
    uses_company = False
    for step in campaign_json.get("steps", []):
        for v in step.get("variants", []):
            for m in VAR_RE.finditer((v.get("subject") or "") + "\n" + (v.get("body") or "")):
                if _norm_key(m.group(1)) in COMPANY_TAG_KEYS:
                    uses_company = True
    if not uses_company:
        return out
    for lead in campaign_json.get("leads", []):
        vmap = lead_vars(lead)
        co = next((vmap[k] for k in ("companyname", "company", "account", "organization")
                   if vmap.get(k)), None)
        if co is None:
            continue
        reason = classify_company(co)
        if reason:
            out.append({
                "lead_id": lead.get("id") or lead.get("email"),
                "lead_email": lead.get("email", ""), "step": "", "variant": "",
                "channel": "company", "check": "COMPANY_QUALITY", "severity": WARNING,
                "evidence": f"companyName “{co[:45]}” — {reason}",
            })
    return out


def chk_undefined_tags(campaign_json):
    """The CERTAIN breakage. A merge tag used in the copy that is neither a
    defined campaign variable nor present in any lead has no value source at all
    — and you can't configure a fallback on a variable that doesn't exist. So it
    cannot personalize, period. This is the only merge finding we can assert
    without a test-send. Needs the campaign's variable registry (Instantly via
    pull_instantly.defined_vars); skipped when that registry is unknown."""
    out = []
    if not campaign_json:
        return out
    defined = campaign_json.get("defined_vars")
    if defined is None:
        return out
    defined_norm = {_norm_key(v) for v in defined}
    lead_keys = set()
    for lead in campaign_json.get("leads", []):
        lead_keys |= set(lead_vars(lead).keys())
    seen = set()
    for step in campaign_json.get("steps", []):
        for v in step.get("variants", []):
            text = (v.get("subject") or "") + "\n" + (v.get("body") or "")
            for m in VAR_RE.finditer(text):
                raw = m.group(1).strip()
                key = _norm_key(raw)
                if not key or key in SYSTEM_VARS or key == "random" or raw in seen:
                    continue
                if key in defined_norm or key in lead_keys:
                    continue
                seen.add(raw)
                hint = difflib.get_close_matches(raw, list(defined), n=1, cutoff=0.6)
                did = f" (did you mean “{hint[0]}”?)" if hint else ""
                out.append({
                    "lead_id": "(campaign-level)", "lead_email": "", "step": step.get("step"),
                    "variant": v.get("id"), "channel": "", "check": "UNDEFINED_TAG",
                    "severity": BLOCKER,
                    "evidence": f"copy uses {{{{{raw}}}}} — not a defined campaign variable "
                                f"and absent from every lead; cannot personalize{did}",
                })
    return out


def chk_signal_collision(rows):
    """Two live variants in the same step sharing a signal wedge tag."""
    out = []
    by_step = defaultdict(dict)  # step -> {signal -> set(variant_ids)}
    for r in rows:
        sig = r.get("signal")
        if not sig:
            continue
        by_step[r.get("step")].setdefault(sig, set()).add(r.get("variant"))
    for step, sigs in by_step.items():
        for sig, variants in sigs.items():
            if len(variants) > 1:
                out.append({
                    "lead_id": "(campaign-level)", "lead_email": "", "step": step,
                    "variant": "+".join(sorted(map(str, variants))), "channel": "",
                    "check": "AB_SIGNAL_COLLISION", "severity": WARNING,
                    "evidence": f"variants {sorted(variants)} share signal “{sig}” in step {step}",
                })
    return out


PER_ROW_CHECKS = [
    chk_blank_merge, chk_dangling, chk_unresolved_var, chk_unknown_syntax,
    chk_claygent, chk_emdash, chk_casing, chk_length, chk_link_first_touch,
    chk_double_punct, chk_full_name_greeting, chk_invisible_chars,
]


def load_spam_words(path):
    words = set(DEFAULT_SPAM)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            words |= {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}
    return words


def run(rows, spam_words, campaign_json=None, instantly_key=None, check_links=False,
        forbidden_terms=None, enabled=None):
    checks = PER_ROW_CHECKS + [make_spam_check(spam_words)]
    findings = []
    for r in rows:
        for chk in checks:
            for finding in chk(r):
                # Checks yield (name, severity, evidence) or, when the readable
                # evidence would embed lead-specific text, a 4th dedup signature.
                name, sev, ev = finding[0], finding[1], finding[2]
                sig = finding[3] if len(finding) > 3 else ev
                findings.append({
                    "lead_id": r.get("lead_id"), "lead_email": r.get("lead_email"),
                    "step": r.get("step"), "variant": r.get("variant"),
                    "channel": r.get("channel"), "check": name,
                    "severity": sev, "evidence": ev, "signature": sig,
                })
    findings.extend(chk_signal_collision(rows))
    findings.extend(chk_name_quality(campaign_json))
    findings.extend(chk_company_quality(campaign_json))
    findings.extend(chk_undefined_tags(campaign_json))
    findings.extend(chk_lead_list(campaign_json))
    findings.extend(chk_placeholders(campaign_json))
    findings.extend(chk_forbidden_terms(campaign_json, forbidden_terms))
    findings.extend(chk_variant_integrity(campaign_json))
    findings.extend(chk_subject_integrity(campaign_json))
    findings.extend(chk_step_pacing(campaign_json))
    findings.extend(chk_handoffs(campaign_json, instantly_key))
    findings.extend(chk_link_health(rows, check_links))
    if enabled is not None:
        findings = [f for f in findings if f["check"] in enabled]
    return findings


def resolve_enabled(disable=None, only=None):
    """Turn --disable/--only into the set of rule names to keep.

    Unknown names raise rather than silently matching nothing: a typo'd
    `--disable EM_DSAH` that quietly did nothing would let a campaign look clean
    for the wrong reason, and a checker that can lie about being clean is worse
    than no checker."""
    known = set(RULES)
    for name in list(disable or []) + list(only or []):
        if name not in known:
            # ValueError, not SystemExit: this is library code and the caller
            # decides how to exit. The CLI turns it into exit 3 (tool error).
            raise ValueError(f"unknown rule \u201c{name}\u201d. Run `lastlook rules` for the catalog.")
    if only:
        return set(only)
    if disable:
        return known - set(disable)
    return None


def dedup_issues(findings):
    """Collapse findings to DISTINCT issues. A spam word or em dash baked into a
    template is ONE issue affecting N leads, not N issues. Data-driven findings
    (a specific bad value) carry a distinct signature and stay separate. Each
    issue records how many leads it touches.

    Keyed on the signature, not the evidence. Evidence is padded with the text
    around the match so a human can read it, which drags the lead's name and
    company into the string — keying on that made one em dash in one template
    subject report as 111 distinct issues across 35,000 leads. Also keyed on
    step and variant, so the same defect in two variants stays two issues:
    they are two places to go and fix."""
    groups = {}
    for f in findings:
        sig = f.get("signature", f["evidence"])
        # Data defects are one fix wherever they appear; template defects are one
        # fix per variant that carries them.
        if isinstance(sig, str) and sig.startswith(DATA_SIG):
            key = (f["check"], f["severity"], sig)
        else:
            key = (f["check"], f["severity"], f.get("step"), f.get("variant"), sig)
        g = groups.setdefault(key, {"check": f["check"], "severity": f["severity"],
                                     "evidence": f["evidence"], "leads": 0,
                                     "step": f.get("step"), "variant": f.get("variant")})
        g["leads"] += 1
    return list(groups.values())


def verdict_block(rows, findings):
    n_msgs = len(rows)
    n_leads = len({r.get("lead_id") for r in rows})
    issues = dedup_issues(findings)
    by_sev = Counter(i["severity"] for i in issues)
    blockers, warnings = by_sev[BLOCKER], by_sev[WARNING]
    # UNDEFINED_TAG is in the template -> hits every lead on that step (not the 1
    # campaign-level pseudo-lead). Count it as the full audience.
    template_blocker = any(f["check"] == "UNDEFINED_TAG" for f in findings if f["severity"] == BLOCKER)
    # DISTINCT leads, not (lead, variant) pairs: every variant is rendered against
    # every lead, so a blocker in two variants counted the same lead twice and the
    # line read "880 of 440 leads". A lead gets ONE variant at send time; distinct
    # leads is the honest worst case.
    per_lead = {f["lead_id"] for f in findings
                if f["severity"] == BLOCKER and f["lead_id"] != "(campaign-level)"}
    broken = n_leads if template_blocker else len(per_lead)

    lines = ["=" * 64]
    if blockers:
        lines.append(f"🔴 NOT CLEAR — {blockers} distinct BLOCKER(S), {warnings} WARNING(S)")
    elif warnings:
        lines.append(f"🟡 LAUNCH WITH CAUTION — {warnings} distinct WARNING(S), 0 blockers")
    else:
        lines.append("🟢 CLEAR TO LAUNCH — 0 blockers, 0 warnings")
    lines.append("=" * 64)
    note = " (template-level — hits every lead on the affected step)" if template_blocker else ""
    lines.append(f"Rendered {n_msgs} messages. {broken} of {n_leads} leads would send with a blocker{note}.")

    # group distinct issues under each check; show issue count + leads affected
    by_check = {}
    order = {BLOCKER: 0, WARNING: 1}
    for i in issues:
        c = by_check.setdefault(i["check"], {"sev": 9, "issues": 0, "leads": 0, "ex": i["evidence"]})
        c["sev"] = min(c["sev"], order[i["severity"]])
        c["issues"] += 1
        c["leads"] += i["leads"]
    if by_check:
        lines.append("")
        lines.append(f"{'check':<20} {'issues':>7} {'leads':>7}   example")
        for check, c in sorted(by_check.items(), key=lambda kv: (kv[1]["sev"], -kv[1]["leads"])):
            tag = "🔴" if c["sev"] == 0 else "🟡"
            lines.append(f"{tag} {check:<18} {c['issues']:>7} {c['leads']:>7}   e.g. {c['ex'][:60]}")
    lines.append("=" * 64)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Run preflight checks over rendered messages.")
    # Not argparse-required: --list-rules is a valid invocation with no input.
    ap.add_argument("--in", dest="infile", default=None)
    ap.add_argument("--out", dest="outfile", default=None, help="findings CSV (default: beside input)")
    ap.add_argument("--spam-words", dest="spam", default=None, help="newline-delimited extra spam words")
    ap.add_argument("--campaign-json", default=None,
                    help="normalized campaign JSON (enables the BROKEN_HANDOFF check)")
    ap.add_argument("--instantly-key", default=os.environ.get("INSTANTLY_API_KEY"),
                    help="Instantly key for verifying HeyReach->Instantly handoffs")
    ap.add_argument("--check-links", action="store_true",
                    help="resolve every distinct URL in the copy and flag dead ones")
    ap.add_argument("--forbidden-terms", default=None,
                    help="comma-separated terms, or a path to a newline-delimited file. "
                         "Blocks on a previous client's or competitor's name left in the copy.")
    ap.add_argument("--list-rules", action="store_true", help="print the rule catalog and exit")
    ap.add_argument("--disable", default="", help="comma-separated rule names to skip")
    ap.add_argument("--only", default="", help="comma-separated rule names to run exclusively")
    args = ap.parse_args()

    if args.list_rules:
        width = max(len(r) for r in RULES)
        print(f"{len(RULES)} rules\n")
        for name, desc in RULES.items():
            print(f"  {name:<{width}}  {desc}")
        return
    if not args.infile:
        ap.error("--in is required (or use --list-rules)")

    enabled = resolve_enabled(
        disable=[s.strip() for s in args.disable.split(",") if s.strip()],
        only=[s.strip() for s in args.only.split(",") if s.strip()])

    forbidden = []
    if args.forbidden_terms:
        if os.path.exists(args.forbidden_terms):
            with open(args.forbidden_terms, encoding="utf-8") as f:
                forbidden = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        else:
            forbidden = [t.strip() for t in args.forbidden_terms.split(",") if t.strip()]

    rows = []
    with open(args.infile, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))

    campaign_json = None
    if args.campaign_json:
        with open(args.campaign_json, encoding="utf-8") as f:
            campaign_json = json.load(f)

    findings = run(rows, load_spam_words(args.spam), campaign_json, args.instantly_key,
                   check_links=args.check_links, forbidden_terms=forbidden, enabled=enabled)

    out = args.outfile or re.sub(r"\.rendered\.jsonl$|\.jsonl$", "", args.infile) + ".findings.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["severity", "check", "step", "variant",
                                          "channel", "lead_id", "lead_email", "evidence"])
        w.writeheader()
        order = {BLOCKER: 0, WARNING: 1}
        for row in sorted(findings, key=lambda r: (order[r["severity"]], r["check"])):
            w.writerow({k: row.get(k, "") for k in w.fieldnames})

    print(verdict_block(rows, findings))
    print(f"\nFull findings -> {out}")


if __name__ == "__main__":
    main()
