"""render.py — campaign-preflight stage 2.

Reads a normalized campaign JSON (preflight_<slug>.json, emitted by any adapter)
and renders every message exactly as it would send: one rendered row per
(step, variant, lead). Output is JSONL — one rendered message per line — so
check.py can stream it.

The renderer is platform-agnostic. It knows nothing about Instantly or HeyReach;
it only knows the normalized shape and the merge syntax Workflows uses:
  - variables:    {{firstName}} / {{first_name}} / {{company_name}} ...
  - inline fallback (Instantly): {{company | your team}}
  - per-variant fallback map (HeyReach fallbackMessage -> fallbacks dict)
  - spintax:      {a|b|c}  (one option chosen per lead; nesting works, e.g.
                  {Quick {question|note}|Fast {ask|ping}})
  - conditional:  {{#if var}}...{{else}}...{{/if}}  (nesting works)

Spintax choice is deterministic (hash of lead id + raw text) so reruns are stable.

Usage:
    python3 render.py --in preflight_acme.json --out preflight_acme.rendered.jsonl
"""

import argparse
import hashlib
import html
import json
import re
import sys

# {{#if var}}...{{else}}...{{/if}}. The tempered dot ((?!\{\{#if).) means a match
# can never contain another opening {{#if}}, so this always matches an INNERMOST
# conditional; render_conditionals loops until stable, peeling nesting outward.
# A plain non-greedy (.*?) paired an outer {{#if}} with the first {{/if}} it saw,
# which on a falsy outer var leaked the tail of the block plus an orphan {{/if}}.
COND_RE = re.compile(
    r"\{\{#if\s+([\w.]+)\s*\}\}"
    r"((?:(?!\{\{#if).)*?)"
    r"(?:\{\{else\}\}((?:(?!\{\{#if).)*?))?"
    r"\{\{/if\}\}",
    re.DOTALL,
)
# {{RANDOM | option A | option B | ...}} — Instantly picks one option at send time.
# Must be resolved BEFORE VAR_RE, which would otherwise read "RANDOM" as a variable
# and leak the remaining options as literal text. Inner {{vars}} in an option are
# preserved and resolved by the later var pass.
#
# Only the OPENING is a regex; the closing }} is found by _find_random's depth
# scan below. A single pattern spanning both was
#   \{\{\s*RANDOM\s*\|((?:[^{}]|\{\{.*?\}\})*?)\}\}
# whose alternation is ambiguous: "{{a}}{{b}}" decomposes many ways, so an
# unbalanced opening backtracked exponentially. 140 bytes took 3.2s, 200 bytes
# never returned — and an unbalanced {{RANDOM is exactly the malformed template
# this tool exists to flag, so the input that should raise a finding hung the run
# instead. In cron that wedges the job forever.
RANDOM_OPEN_RE = re.compile(r"\{\{\s*RANDOM\s*\|", re.IGNORECASE)
# {{ var }} or {{ var | fallback }}  — variable, optional inline fallback.
# Key may contain internal spaces (Instantly custom vars like {{Industry For Emails}});
# starts with a word char so it never matches {{#if}} / {{/if}}.
VAR_RE = re.compile(r"\{\{\s*([\w.][\w.\s]*?)\s*(?:\|\s*([^}]*?))?\s*\}\}")
# {a|b|c} — spintax. A single-brace group containing at least one pipe.
SPINTAX_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
# HTML -> text for checks. Block-level tags become newlines so adjacent words
# don't fuse ("scale with</div><div>Out of..." -> "scale with\nOut of..."), which
# would otherwise read as a missing-space defect that isn't real.
BLOCK_RE = re.compile(r"(?i)<\s*(br|/p|/div|/li|/tr|/h[1-6])\s*/?>")
TAG_RE = re.compile(r"<[^>]+>")

# Characters that are invisible on screen but real in the bytes that send.
# They arrive by paste from Docs/Notion/Word. Two problems: they sit inside words
# where a spam filter sees an obfuscation attempt, and they break merge-key and
# text matching for anything scanning the copy (including these checks).
# The nbsp family renders as a space, so normalize those to one; the zero-width
# family renders as nothing, so drop it.
SPACEY_INVISIBLES = "     "          # look like a space
ZERO_WIDTH_INVISIBLES = "​‌‍⁠﻿­"  # look like nothing
INVISIBLE_RE = re.compile(f"[{SPACEY_INVISIBLES}{ZERO_WIDTH_INVISIBLES}]")

# Sentinel inserted where a variable resolved to empty AND had no fallback.
# check.py keys off this to distinguish "blank merge" from legitimately empty text.
# Deliberately letter-free so it never trips the CASING / legal-suffix / claygent
# text checks that scan the rendered fields.
BLANK = "\x00\x00"

# Platform "system" merge tags that are NOT filled from lead data — the ESP
# resolves them at send time from the sending mailbox / unsubscribe service.
# They must never be flagged as blank or unresolved. Matched after _norm_key.
SYSTEM_VARS = {
    # Instantly sender/account + housekeeping tags
    "accountsignature", "sendingaccountfirstname", "sendingaccountlastname",
    "sendingaccountemail", "sendingaccountname", "sendername", "senderfirstname",
    "senderlastname", "senderemail", "unsubscribelink", "unsubscribe", "sentdate",
    "currentdate", "dayofweek",
}


def strip_html(text):
    text = BLOCK_RE.sub("\n", text)
    text = TAG_RE.sub("", text)
    # html.unescape covers every named and numeric entity. The previous
    # hand-written table held six, so &#8217; &rsquo; &mdash; and friends shipped
    # to prospects literally — and Instantly stores bodies as HTML, so curly
    # quotes and dashes pasted from a doc arrive in exactly those forms.
    # Unescape AFTER tag stripping: doing it first can synthesize a "<tag>" out
    # of &lt;...&gt; and TAG_RE would then eat real copy.
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text)


ZWJ = "‍"


def _is_pictographic(ch):
    """Roughly: is this an emoji or an emoji modifier?

    Used only to decide whether a zero-width joiner is doing real work. A loose
    test is the safe direction here — treating something as emoji means we LEAVE
    the joiner alone, and leaving a stray joiner is harmless next to breaking a
    real one."""
    if not ch:
        return False
    o = ord(ch)
    return (0x1F000 <= o <= 0x1FAFF          # emoji blocks
            or 0x2600 <= o <= 0x27BF          # misc symbols / dingbats
            or 0x2190 <= o <= 0x21FF          # arrows
            or o in (0xFE0F, 0xFE0E)          # variation selectors
            or 0x1F3FB <= o <= 0x1F3FF)       # skin-tone modifiers


def normalize_invisibles(text):
    """Strip invisible characters, reporting which were found.

    Returns (clean_text, sorted_codepoints). The renderer must show what actually
    sends, and these characters are indistinguishable from nothing on screen — so
    they are removed from the rendered text and surfaced as a finding instead of
    silently riding along.

    A zero-width joiner BETWEEN two emoji is the exception: it is load-bearing.
    U+1F926 U+200D U+2640 is one glyph, "woman facepalming"; drop the joiner and
    it becomes two. An earlier version stripped it unconditionally, which showed
    up on 132 rows of live client copy — and `fix --apply` would have written
    that corruption back to the campaign."""
    out, found = [], set()
    n = len(text)
    for i, ch in enumerate(text):
        if ch in SPACEY_INVISIBLES:
            found.add(f"U+{ord(ch):04X}")
            out.append(" ")
        elif ch in ZERO_WIDTH_INVISIBLES:
            if ch == ZWJ and _is_pictographic(text[i - 1] if i else "") \
                    and _is_pictographic(text[i + 1] if i + 1 < n else ""):
                out.append(ch)          # joining an emoji sequence: leave it
                continue
            found.add(f"U+{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out), sorted(found)


def _stable_choice(options, seed):
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def lead_vars(lead):
    """Flat lookup dict for a lead: standard fields + payload custom vars.

    Keys are matched case-insensitively and across snake/camel by normalizing
    to lowercase-alphanumeric, so {{firstName}} and {{first_name}} both resolve.
    """
    flat = {}
    for k, v in lead.items():
        if k in ("payload", "vars"):
            continue
        flat[k] = v
    # custom variables live under "vars" (normalized) or "payload" (raw instantly)
    for src in (lead.get("vars"), lead.get("payload")):
        if isinstance(src, dict):
            flat.update(src)
    return {_norm_key(k): ("" if v is None else str(v)) for k, v in flat.items()}


def _norm_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def render_conditionals(text, vars_map):
    def repl(m):
        key = _norm_key(m.group(1))
        truthy = bool(vars_map.get(key, "").strip())
        return m.group(2) if truthy else (m.group(3) or "")

    # resolve repeatedly in case of stacked (non-nested) conditionals
    prev = None
    while prev != text:
        prev = text
        text = COND_RE.sub(repl, text)
    return text


def render_vars(text, vars_map, fallbacks):
    """Substitute vars. Returns (text, blanked) where `blanked` lists every tag
    that resolved to BLANK — i.e. no value AND no fallback of any kind (inline,
    per-variant). A blank in the output is a real send-gap; there is no other
    fallback mechanism the renderer can't see (verified against Instantly)."""
    blanked = []

    def repl(m):
        raw_key, inline_fb = m.group(1), m.group(2)
        key = _norm_key(raw_key)
        val = vars_map.get(key, None)
        if val is not None and val.strip() != "":
            return val
        # system tags are filled by the ESP at send time — render empty, never flag
        if key in SYSTEM_VARS:
            return ""
        # empty or missing — try inline fallback, then per-variant fallback map
        if inline_fb is not None and inline_fb.strip() != "":
            return inline_fb
        fb = fallbacks.get(raw_key) or fallbacks.get(key)
        if fb:
            return str(fb)
        blanked.append(raw_key)   # no value, no fallback -> WILL send a gap
        return BLANK

    rendered = VAR_RE.sub(repl, text)
    return rendered, blanked


def _split_top_level(s):
    """Split on the pipes that separate RANDOM options — not the pipe inside an
    inner {{company | fallback}} tag. A naive split("|") broke such an option
    into fragments ("Hi {{company", "your team}}") that shipped literally AND
    matched no leftover-tag pattern, so the defect passed the checker silently."""
    parts, cur, depth, i, n = [], [], 0, 0, len(s)
    while i < n:
        two = s[i:i + 2]
        if two == "{{":
            depth += 1; cur.append(two); i += 2; continue
        if two == "}}" and depth:
            depth -= 1; cur.append(two); i += 2; continue
        if s[i] == "|" and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(s[i])
        i += 1
    parts.append("".join(cur))
    return parts


def _find_random(text, from_pos):
    """Locate the next {{RANDOM | ...}} block. Returns (start, end, inner) or None.

    One left-to-right pass, so cost is linear in the length of the text — see the
    ReDoS note on RANDOM_OPEN_RE. An opening whose {{ }} never balances is left
    alone rather than guessed at: the leftover-tag checks then flag it, which is
    the correct outcome for a broken template."""
    n = len(text)
    while True:
        m = RANDOM_OPEN_RE.search(text, from_pos)
        if not m:
            return None
        i, depth = m.end(), 0
        while i < n:
            two = text[i:i + 2]
            if two == "{{":
                depth += 1; i += 2; continue
            if two == "}}":
                if depth == 0:
                    return m.start(), i + 2, text[m.end():i]
                depth -= 1; i += 2; continue
            i += 1
        # Unbalanced. Resume after this opening so a well-formed block that
        # follows a broken one still renders.
        from_pos = m.end()


def render_random(text, seed):
    out, pos = [], 0
    while True:
        found = _find_random(text, pos)
        if not found:
            out.append(text[pos:])
            return "".join(out)
        start, end, inner = found
        options = [o.strip() for o in _split_top_level(inner)]
        out.append(text[pos:start])
        out.append(_stable_choice(options, seed + "R" + inner))
        pos = end


def render_spintax(text, seed):
    def repl(m):
        options = m.group(1).split("|")
        return _stable_choice(options, seed + "|" + m.group(1))

    prev = None
    while prev != text:
        prev = text
        text = SPINTAX_RE.sub(repl, text)
    return text


def render_message(template, lead, fallbacks):
    """Full render pipeline for one template string against one lead."""
    vars_map = lead_vars(lead)
    seed = str(lead.get("id") or lead.get("email") or "")
    text = render_conditionals(template, vars_map)
    text = render_random(text, seed)
    text, blanked = render_vars(text, vars_map, fallbacks)
    text = render_spintax(text, seed)
    return text, blanked


def classify_blanks(blanked, lead, defined):
    """Split blanked tags into:
      - undefined: not a defined campaign variable AND not a key on the lead
        (a template bug — hits everyone, the certain structural breakage)
      - data_gap:  a known/defined variable that's just empty for THIS lead
    When `defined` is None (platform without a variable registry, e.g. HeyReach,
    or fixtures), everything goes to `unknown` and keeps the old blocker behavior."""
    if defined is None:
        return [], [], sorted(set(blanked))
    vmap = lead_vars(lead)
    undefined, data_gap = [], []
    for raw in blanked:
        key = _norm_key(raw)
        if key in defined or key in vmap:
            data_gap.append(raw)
        else:
            undefined.append(raw)
    return sorted(set(undefined)), sorted(set(data_gap)), []


def iter_rendered(campaign):
    platform = campaign.get("platform", "unknown")
    cname = campaign.get("campaign", {}).get("name", "")
    cid = campaign.get("campaign", {}).get("id", "")
    leads = campaign.get("leads", [])
    dv = campaign.get("defined_vars")
    defined = {_norm_key(v) for v in dv} if dv is not None else None
    for step in campaign.get("steps", []):
        for variant in step.get("variants", []):
            if variant.get("disabled"):
                continue
            fallbacks = variant.get("fallbacks") or {}
            subj_t = variant.get("subject") or ""
            body_t = variant.get("body") or ""
            for lead in leads:
                subj, u1 = render_message(subj_t, lead, fallbacks)
                body, u2 = render_message(body_t, lead, fallbacks)
                undef, gap, unknown = classify_blanks(u1 + u2, lead, defined)
                subj_clean, inv_s = normalize_invisibles(strip_html(subj).replace(BLANK, ""))
                body_clean, inv_b = normalize_invisibles(strip_html(body).replace(BLANK, ""))
                yield {
                    "platform": platform,
                    "campaign_id": cid,
                    "campaign_name": cname,
                    "step": step.get("step"),
                    "channel": step.get("channel", "email"),
                    "limits": step.get("limits") or {},
                    "variant": variant.get("id"),
                    "signal": variant.get("signal"),
                    "lead_id": lead.get("id") or lead.get("email"),
                    "lead_email": lead.get("email"),
                    # `subject`/`body` are what the prospect actually receives, so the
                    # BLANK sentinel must not survive into them — an unfilled merge
                    # sends as nothing, not as a control character. Leaving it in also
                    # wrote raw NULs into the findings CSV, which made the file
                    # unreadable by csv readers (Clay included) on any campaign with a
                    # blank merge. The `_raw` twins keep the sentinel; that is where
                    # chk_dangling looks to avoid double-reporting a gap.
                    "subject": subj_clean,
                    "body": body_clean,
                    "subject_raw": subj,
                    "body_raw": body,
                    "invisibles": sorted(set(inv_s + inv_b)),
                    "blanked_undefined": undef,   # template bug: var doesn't exist
                    "blanked_data_gap": gap,       # known var, empty for this lead
                    "blanked_unknown": unknown,    # no registry (HeyReach/fixtures)
                }


def main():
    ap = argparse.ArgumentParser(description="Render every variant x lead message.")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outfile", required=True)
    args = ap.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        campaign = json.load(f)

    n = 0
    with open(args.outfile, "w", encoding="utf-8") as out:
        for row in iter_rendered(campaign):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1

    leads = len(campaign.get("leads", []))
    variants = sum(
        len([v for v in s.get("variants", []) if not v.get("disabled")])
        for s in campaign.get("steps", [])
    )
    print(f"Rendered {n} messages ({variants} live variants x {leads} leads) -> {args.outfile}")
    if n == 0:
        print("WARNING: 0 messages rendered. Check that the campaign has steps, "
              "live variants, and leads.", file=sys.stderr)


if __name__ == "__main__":
    main()
