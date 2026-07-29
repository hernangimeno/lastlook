"""fix.py — propose (and optionally apply) the fixes lastlook can make safely.

Two halves, because they are fixed in two different places:

  TEMPLATE fixes   deterministic text edits to the campaign copy. Shown as a
                   unified diff; `--apply` writes them back via the platform API.
  DATA fixes       bad values on the leads. lastlook cannot fix a CRM, so these
                   are exported as a CSV of current -> suggested for whatever
                   owns the data.

What is deliberately NOT auto-fixable, and why:

  spam vocabulary      rewording is a judgement about the offer, not a typo
  blank merge fields   the fix is a fallback or enrichment, both editorial
  placeholder text     deleting "[insert case study]" leaves a hole, not a fix
  variant duplication  the fix is writing a different message
  pacing / structure   a campaign-settings decision with revenue consequences

A fixer that guesses at any of those does more damage than the defect. Every
transform here is reversible by inspection and changes no meaning.
"""

import csv
import difflib
import re
import unicodedata

from .render import INVISIBLE_RE, SPACEY_INVISIBLES, ZERO_WIDTH_INVISIBLES

# --- template transforms ------------------------------------------------------
# Each is (id, description, callable). Order matters: invisible characters are
# normalized first so the whitespace rules can see a plain space.

# Other platforms' merge syntax appearing in a campaign that cannot resolve it.
# The value is the tag as THIS platform writes it. Keyed by platform because
# {FIRST_NAME} is correct in HeyReach and broken in Instantly.
FOREIGN_TAGS = {
    "instantly": {
        r"\{FIRST_NAME\}":   "{{firstName}}",
        r"\{LAST_NAME\}":    "{{lastName}}",
        r"\{COMPANY_NAME\}": "{{companyName}}",
        r"\{COMPANY\}":      "{{companyName}}",
        r"\{POSITION\}":     "{{title}}",
    },
    # No heyreach entry ON PURPOSE. Its adapter canonicalizes {FIRST_NAME} to
    # {{first_name}} on the way in, so by the time a fix is planned the tags are
    # already double-brace. Mapping them "back" produced a diff that rewrote
    # perfectly good copy on all 7 live AcmeAds campaigns.
}


def _strip_invisibles(text, platform=None):
    out = []
    for ch in text:
        if ch in SPACEY_INVISIBLES:
            out.append(" ")
        elif ch in ZERO_WIDTH_INVISIBLES:
            continue
        else:
            out.append(ch)
    return "".join(out)


def _space_before_punct(text, platform=None):
    # "Hey Anne ," -> "Hey Anne,"  — the tell of a trailing space in a CRM value.
    # Only , . ! ? — NOT : or ; . A space before a colon is almost never a merge
    # artifact, and " :)" is an emoticon: an earlier version turned
    # "send you the link :)" into "the link:)" in live copy.
    return re.sub(r"[ \t]+([,.!?])(?=\s|$)", r"\1", text)


def _collapse_spaces(text, platform=None):
    # Horizontal runs only. Never touches "\n\n", which is a paragraph break.
    return re.sub(r"[ \t]{2,}", " ", text)


def _double_punct(text, platform=None):
    # "Hey Yune,," and "together.." — a value that already ended in punctuation
    # meeting a template that adds its own. Ellipses are left alone.
    text = re.sub(r",{2,}", ",", text)
    return re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)


def _prose_dash(text, platform=None):
    # Em/en dash used as prose punctuation -> a plain hyphen. A numeric range
    # ("11–15 hours") is correct typography and is left as it is.
    def repl(m):
        i = m.start()
        left = text[i - 1] if i else ""
        right = text[i + 1] if i + 1 < len(text) else ""
        if m.group() == "–" and left.isdigit() and right.isdigit():
            return m.group()
        return "-"
    return re.sub(r"[—–]", repl, text)


def _foreign_tags(text, platform=None):
    for pat, repl in FOREIGN_TAGS.get(platform or "", {}).items():
        text = re.sub(pat, repl, text)
    return text


TEMPLATE_FIXES = [
    ("invisible_chars", "strip zero-width and non-breaking characters", _strip_invisibles),
    ("foreign_tags",    "convert another platform's merge tags to this one's", _foreign_tags),
    ("space_before_punct", "remove space before punctuation", _space_before_punct),
    ("collapse_spaces", "collapse repeated spaces", _collapse_spaces),
    ("double_punct",    "collapse doubled commas and periods", _double_punct),
    ("prose_dash",      "replace em/en dash used as prose punctuation", _prose_dash),
]


def fix_text(text, platform=None, enabled=None):
    """Apply every enabled transform. Returns (new_text, [applied fix ids])."""
    applied = []
    for fid, _desc, fn in TEMPLATE_FIXES:
        if enabled is not None and fid not in enabled:
            continue
        after = fn(text, platform)
        if after != text:
            applied.append(fid)
            text = after
    return text, applied


def plan_template_fixes(campaign, enabled=None):
    """Every template edit this campaign needs. Nothing is written."""
    platform = campaign.get("platform")
    out = []
    for step in campaign.get("steps", []) or []:
        for v in step.get("variants", []) or []:
            if v.get("disabled"):
                continue
            for field in ("subject", "body"):
                before = v.get(field) or ""
                if not before:
                    continue
                after, applied = fix_text(before, platform, enabled)
                if applied:
                    out.append({
                        "step": step.get("step"), "variant": v.get("id"),
                        "field": field, "before": before, "after": after,
                        "fixes": applied,
                    })
    return out


# --- data-side suggestions ----------------------------------------------------

LEGAL_SUFFIX_TAIL = re.compile(
    r"[,\s]+(inc|llc|l\.l\.c|ltd|limited|corp|corporation|gmbh|s\.a|sa|srl|bv|b\.v|plc|ag|oy|ab|as|nv)\.?$",
    re.IGNORECASE)
EMOJI_AND_MARKS = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿←-⇿️®™©]")
PAREN_TAIL = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*$")


def _clean_company(value):
    v = EMOJI_AND_MARKS.sub("", value).strip()
    v = LEGAL_SUFFIX_TAIL.sub("", v).strip(" ,-")
    # "Affinity (CRM)" -> "Affinity";  "Foo/Bar Baz" -> "Foo"
    v = PAREN_TAIL.sub("", v)
    if "/" in v:
        v = v.split("/")[0].strip()
    if v.isupper() and len(v) > 4:
        v = v.title()
    v = re.sub(r"\s{2,}", " ", v).strip()
    return v


def _clean_first_name(value):
    v = EMOJI_AND_MARKS.sub("", value).strip()
    # Mojibake: "Ã‚ngela" style double-encoding round-trips back to "Ângela".
    try:
        maybe = v.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
        if maybe != v and not re.search(r"[ÃÂâ€]", maybe):
            v = maybe
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    v = PAREN_TAIL.sub("", v)          # "Julian (Jules)" -> "Julian"
    v = re.sub(r"[,\s]+(phd|md|mba|cpa|jr|sr|iii|iv)\b\.?$", "", v, flags=re.I)
    # Drop a leading honorific BEFORE taking the first token, or "Dr. Sam"
    # collapses to "Dr" — which is what shipped on the first run.
    v = re.sub(r"^(dr|mr|mrs|ms|miss|mx|prof|sir|rev)\.?\s+", "", v, flags=re.I)
    v = v.split()[0] if v.split() else v          # "Norman Gregory" -> "Norman"
    v = v.strip(" .,-")
    if v.isupper() and len(v) > 2:
        v = v.title()
    return v


DATA_CLEANERS = {
    "company_name": _clean_company,
    "first_name": _clean_first_name,
}


def plan_data_fixes(campaign):
    """Suggested value corrections, one row per lead+field that would change."""
    rows = []
    for lead in campaign.get("leads", []) or []:
        for field, clean in DATA_CLEANERS.items():
            cur = lead.get(field)
            if not isinstance(cur, str) or not cur.strip():
                continue
            new = clean(cur)
            if new and new != cur:
                rows.append({
                    "lead_id": lead.get("id") or lead.get("email") or "",
                    "lead_email": lead.get("email") or "",
                    "field": field,
                    "current": cur,
                    "suggested": new,
                })
    return rows


# --- presentation -------------------------------------------------------------

def render_diff(edits, max_shown=None):
    if not edits:
        return "No template edits needed."
    lines = []
    for e in edits[:max_shown] if max_shown else edits:
        lines.append(f"\nstep {e['step']} / variant {e['variant']} / {e['field']}"
                     f"   [{', '.join(e['fixes'])}]")
        diff = difflib.unified_diff(
            e["before"].splitlines(), e["after"].splitlines(),
            lineterm="", n=0)
        for d in list(diff)[2:]:
            lines.append("  " + d)
    if max_shown and len(edits) > max_shown:
        lines.append(f"\n... {len(edits) - max_shown} more edits")
    return "\n".join(lines)


def write_data_csv(rows, path):
    cols = ["lead_id", "lead_email", "field", "current", "suggested"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


# --- applying -----------------------------------------------------------------

class ApplyUnsupported(RuntimeError):
    """The platform offers no way to write the sequence back."""


def apply_template_fixes(campaign, edits, api_key, enabled=None):
    """Write the edited templates back to the platform."""
    platform = campaign.get("platform")
    if platform == "heyreach":
        return _apply_heyreach(campaign, edits, api_key, enabled)
    if platform != "instantly":
        raise ApplyUnsupported(
            f"{platform} campaigns cannot be written back — lastlook only has "
            f"writers for instantly and heyreach. The diff above is still "
            f"correct; paste it in by hand.")

    import httpx

    edited = {(e["step"], e["variant"], e["field"]): e["after"] for e in edits}
    cid = campaign["campaign"]["id"]
    cx = httpx.Client(base_url="https://api.instantly.ai/api/v2",
                      headers={"Authorization": f"Bearer {api_key}",
                               "User-Agent": "lastlook/0.1"}, timeout=30.0)

    # Read the CURRENT sequence and patch only the fields we changed. Rebuilding
    # it from the normalized shape would drop everything normalization discards.
    live = cx.get(f"/campaigns/{cid}")
    live.raise_for_status()
    live = live.json()
    seqs = live.get("sequences") or []

    touched, step_no = 0, 0
    for seq in seqs:
        for st in seq.get("steps", []):
            step_no += 1
            for i, v in enumerate(st.get("variants", [])):
                vid = chr(ord("A") + i)
                for field in ("subject", "body"):
                    key = (step_no, vid, field)
                    if key in edited:
                        v[field] = edited[key]
                        touched += 1

    if not touched:
        cx.close()
        return 0

    r = cx.patch(f"/campaigns/{cid}", json={"sequences": seqs})
    cx.close()
    if r.status_code >= 400:
        raise RuntimeError(f"Instantly rejected the update: HTTP {r.status_code} {r.text[:300]}")
    return touched


# --- HeyReach writer ----------------------------------------------------------
# HeyReach refuses a sequence write while a campaign is running, so the flow is
# pause -> UpdateSequence -> resume. Verified live 2026-07-30 against the dormant
# campaign 100001: POST /campaign/UpdateSequence with {"campaignId", "Sequence"}
# returns 200 and a no-op round-trip leaves the tree byte-identical.

HR_MESSAGE_NODES = {"CONNECTION_REQUEST", "MESSAGE", "INMAIL"}


def _hr_patch_tree(node, edits_by_key, enabled, step_no=0, counter=None, touched=None):
    """Walk the live sequence tree and re-run the fixes on the RAW payload text.

    Deliberately does NOT paste the `after` text from the plan. That text came
    out of the adapter, which canonicalizes {FIRST_NAME} to {{first_name}} on
    read — writing it back would convert every HeyReach tag to a syntax HeyReach
    does not understand and break the whole campaign. Re-applying the transform
    to the live string keeps the platform's own tags intact.

    Step numbering mirrors adapters.heyreach.walk_sequence exactly, or the edits
    land on the wrong node.
    """
    if counter is None:
        counter, touched = {"n": 0}, []
    if not node:
        return touched

    ntype = node.get("nodeType")
    if ntype in HR_MESSAGE_NODES:
        counter["n"] += 1
        step = counter["n"]
        pl = node.get("payload") or {}
        msgs = pl.get("messages") or []
        for i, m in enumerate(msgs):
            vid = chr(ord("A") + i)
            if ntype == "INMAIL" and isinstance(m, dict):
                for field, mkey in (("subject", "subject"), ("body", "message")):
                    if (step, vid, field) not in edits_by_key:
                        continue
                    new, applied = fix_text(m.get(mkey) or "", "heyreach", enabled)
                    if applied and new != m.get(mkey):
                        m[mkey] = new
                        touched.append((step, vid, field, applied))
            elif isinstance(m, str):
                if (step, vid, "body") in edits_by_key:
                    new, applied = fix_text(m, "heyreach", enabled)
                    if applied and new != m:
                        msgs[i] = new
                        touched.append((step, vid, "body", applied))

    for key in ("unconditionalNode", "conditionalNode"):
        if node.get(key):
            _hr_patch_tree(node[key], edits_by_key, enabled, step_no, counter, touched)
    return touched


def _apply_heyreach(campaign, edits, api_key, enabled=None):
    import time

    import httpx

    cid = campaign["campaign"]["id"]
    cx = httpx.Client(base_url="https://api.heyreach.io/api/public",
                      headers={"X-API-KEY": api_key}, timeout=30.0)
    try:
        meta = cx.get("/campaign/GetById", params={"campaignId": cid})
        meta.raise_for_status()
        was_running = meta.json().get("status") == "IN_PROGRESS"

        if was_running:
            # Pause/Resume take campaignId as a QUERY PARAM, not a JSON body —
            # a body returns 400 "campaignId field is required". UpdateSequence
            # is the opposite and takes a body. Verified live 2026-07-30.
            r = cx.post("/campaign/Pause", params={"campaignId": cid})
            if r.status_code >= 400:
                raise RuntimeError(f"could not pause the campaign, nothing written: "
                                   f"HTTP {r.status_code} {r.text[:200]}")
            time.sleep(0.5)

        # Everything from here runs inside try/finally: a campaign left paused
        # because a write failed is a silently stopped campaign, which is worse
        # than the defect being fixed.
        try:
            seq = cx.get("/campaign/GetCampaignSequence", params={"campaignId": cid})
            seq.raise_for_status()
            tree = seq.json()

            keys = {(e["step"], e["variant"], e["field"]) for e in edits}
            touched = _hr_patch_tree(tree, keys, enabled)
            if not touched:
                return 0

            r = cx.post("/campaign/UpdateSequence",
                        json={"campaignId": cid, "Sequence": tree})
            if r.status_code >= 400:
                raise RuntimeError(f"HeyReach rejected the sequence update: "
                                   f"HTTP {r.status_code} {r.text[:300]}")
            return len(touched)
        finally:
            if was_running:
                time.sleep(0.5)
                rr = cx.post("/campaign/Resume", params={"campaignId": cid})
                if rr.status_code >= 400:
                    raise RuntimeError(
                        f"THE CAMPAIGN IS STILL PAUSED. The sequence write may have "
                        f"succeeded, but resume failed: HTTP {rr.status_code} "
                        f"{rr.text[:200]}. Resume it by hand in HeyReach.")
    finally:
        cx.close()
