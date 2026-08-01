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

from .render import normalize_invisibles

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
    # perfectly good copy on all 7 campaigns of a live LinkedIn account.
}


def _strip_invisibles(text, platform=None):
    # Delegates to the renderer so the fixer can never diverge from what the
    # checker reports. That matters most for the zero-width joiner inside an
    # emoji sequence, which must survive both.
    return normalize_invisibles(text)[0]


# A merge tag's NAME is an identifier, not prose: {{Q1–Q2 Goal}} -> {{Q1-Q2 Goal}}
# stops resolving and the lead renders blank. The prose transforms below must
# therefore never edit inside {{...}} or a single-brace tag ({FIRST  NAME} is
# HeyReach's raw syntax). A single-brace group WITH a pipe is spintax — prose —
# and stays editable. _strip_invisibles stays unmasked on purpose (an invisible
# char inside a tag name is itself what breaks resolution) and _foreign_tags
# works ON the tags.
TAG_SPAN_RE = re.compile(r"\{\{.*?\}\}|\{[^{}|]*\}")


def _masked(fn):
    def wrapper(text, platform=None):
        out, last = [], 0
        for m in TAG_SPAN_RE.finditer(text):
            out.append(fn(text[last:m.start()], platform))
            out.append(m.group())
            last = m.end()
        out.append(fn(text[last:], platform))
        return "".join(out)
    return wrapper


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
    # "Hey Sam,," and "together.." — a value that already ended in punctuation
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
    ("space_before_punct", "remove space before punctuation", _masked(_space_before_punct)),
    ("collapse_spaces", "collapse repeated spaces", _masked(_collapse_spaces)),
    ("double_punct",    "collapse doubled commas and periods", _masked(_double_punct)),
    ("prose_dash",      "replace em/en dash used as prose punctuation", _masked(_prose_dash)),
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


# Which rule code each template transform actually clears. Used by the recap so
# "lastlook can fix N of these" is derived from the plan, never from the findings.
FIX_ID_TO_RULE = {
    "invisible_chars": "INVISIBLE_CHARS",
    "foreign_tags": "UNKNOWN_SYNTAX",
    "space_before_punct": "DANGLING_TEXT",
    "collapse_spaces": "DANGLING_TEXT",
    "double_punct": "DOUBLE_PUNCT",
    "prose_dash": "EM_DASH",
}


def fixable_rules(campaign, enabled=None):
    """The rule codes `fix` would genuinely clear on THIS campaign's templates."""
    out = set()
    for edit in plan_template_fixes(campaign, enabled):
        for fid in edit["fixes"]:
            if fid in FIX_ID_TO_RULE:
                out.add(FIX_ID_TO_RULE[fid])
    return out


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
    # Taglines and hiring notices bolted onto the company field by enrichment:
    # "Initech.co | We are hiring!" -> "Initech.co"
    v = v.split("|")[0].strip()
    # A trailing domain restating the name: "Acme Inbound - AcmeInbound.com"
    v = re.sub(r"\s*[-–—]\s*[\w.-]+\.(com|io|ai|co|net|org|eu)\s*$", "", v, flags=re.I)
    v = LEGAL_SUFFIX_TAIL.sub("", v).strip(" ,-")
    # "Globex (CRM)" -> "Globex";  "Foo/Bar Baz" -> "Foo"
    v = PAREN_TAIL.sub("", v)
    if "/" in v:
        v = v.split("/")[0].strip()
    if v.isupper() and len(v) > 4:
        v = v.title()
    v = re.sub(r"\s{2,}", " ", v).strip()
    return v


def _clean_first_name(value):
    v = EMOJI_AND_MARKS.sub("", value).strip()
    # Mojibake: "JosÃ©" style double-encoding round-trips back to "José".
    try:
        maybe = v.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
        if maybe != v and not re.search(r"[ÃÂâ€]", maybe):
            v = maybe
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    v = PAREN_TAIL.sub("", v)          # "Sam (Sammy)" -> "Sam"
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


class WrongCampaign(RuntimeError):
    """The live campaign at this id is not the one you confirmed by name.

    You type a campaign NAME to confirm --apply, but the write targets the ID in
    the local JSON. A stale or hand-edited campaign.json whose name and id
    disagree would have you approve one campaign and overwrite another. Both
    writers already fetch the live campaign, so the name is right there: check it
    before touching anything."""


def _assert_same_campaign(local_name, live_name, cid):
    if (local_name or "").strip() == (live_name or "").strip():
        return
    raise WrongCampaign(
        f"refusing to write: campaign id {cid} is called '{live_name}' on the "
        f"platform, but your campaign JSON calls it '{local_name}'. Nothing was "
        f"written. Re-pull the campaign and try again.")


class StaleCampaign(RuntimeError):
    """The live copy changed after the local campaign JSON was pulled."""


def _stale(key, detail="changed on the platform"):
    step, variant, field = key
    raise StaleCampaign(
        f"refusing to write: step {step} variant {variant} {field} {detail}. "
        f"Nothing was written. Re-pull the campaign, review the new diff, and try again.")


def _patch_instantly_sequences(sequences, edits):
    """Patch a live Instantly tree only if every planned source still matches."""
    # str() on both sides: the schema allows string steps and json round-trips
    # keep whatever type the campaign JSON holds, while the writer's own counter
    # is an int. Comparing them raw made every schema-legal string-step edit
    # read as "missing" and abort.
    by_key = {(str(e["step"]), str(e["variant"]), e["field"]): e for e in edits}
    touched, step_no = set(), 0
    for sequence in sequences:
        for step in sequence.get("steps", []):
            step_no += 1
            for i, variant in enumerate(step.get("variants", [])):
                vid = chr(ord("A") + i)
                for field in ("subject", "body"):
                    key = (str(step_no), vid, field)
                    edit = by_key.get(key)
                    if not edit:
                        continue
                    current = variant.get(field) or ""
                    if current != edit["before"]:
                        _stale(key)
                    variant[field] = edit["after"]
                    touched.add(key)
    missing = set(by_key) - touched
    if missing:
        _stale(sorted(missing)[0],
               "was not found in the live sequence — the copy changed, or the "
               "local JSON's step/variant ids do not match the platform's "
               "positional A/B numbering")
    return len(touched)


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

    cid = campaign["campaign"]["id"]
    with httpx.Client(base_url="https://api.instantly.ai/api/v2",
                      headers={"Authorization": f"Bearer {api_key}",
                               "User-Agent": "lastlook/0.1"}, timeout=30.0) as cx:
        # Read the CURRENT sequence and patch only the fields we changed. Rebuilding
        # it from the normalized shape would drop everything normalization discards.
        live = cx.get(f"/campaigns/{cid}")
        live.raise_for_status()
        live = live.json()
        _assert_same_campaign(campaign["campaign"].get("name"), live.get("name"), cid)
        seqs = live.get("sequences") or []
        touched = _patch_instantly_sequences(seqs, edits)
        if not touched:
            return 0

        response = cx.patch(f"/campaigns/{cid}", json={"sequences": seqs})
        if response.status_code >= 400:
            raise RuntimeError(
                f"Instantly rejected the update: HTTP {response.status_code} "
                f"{response.text[:300]}")
        return touched


# --- HeyReach writer ----------------------------------------------------------
# HeyReach refuses a sequence write while a campaign is running, so the flow is
# pause -> UpdateSequence -> resume. Verified live 2026-07-30 against a dormant
# campaign: POST /campaign/UpdateSequence with {"campaignId", "Sequence"}
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

    from .adapters.heyreach import normalize_merge_tags

    ntype = node.get("nodeType")
    if ntype in HR_MESSAGE_NODES:
        counter["n"] += 1
        step = str(counter["n"])   # keys are strings — see _patch_instantly_sequences
        pl = node.get("payload") or {}
        msgs = pl.get("messages") or []
        for i, m in enumerate(msgs):
            vid = chr(ord("A") + i)
            if ntype == "INMAIL" and isinstance(m, dict):
                for field, mkey in (("subject", "subject"), ("body", "message")):
                    key = (step, vid, field)
                    edit = edits_by_key.get(key)
                    if not edit:
                        continue
                    current = m.get(mkey) or ""
                    if normalize_merge_tags(current) != edit["before"]:
                        _stale(key)
                    new, applied = fix_text(current, "heyreach", enabled)
                    if applied and new != current:
                        m[mkey] = new
                        touched.append((step, vid, field, applied))
            elif isinstance(m, str):
                key = (step, vid, "body")
                edit = edits_by_key.get(key)
                if edit:
                    if normalize_merge_tags(m) != edit["before"]:
                        _stale(key)
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
    # HeyReach ids are numeric and its API rejects string ids in JSON bodies
    # (GetLeadsFromList needed int(list_id) for the same reason). Query params
    # stringify anyway, so one numeric form serves both.
    if str(cid).isdigit():
        cid = int(cid)
    cx = httpx.Client(base_url="https://api.heyreach.io/api/public",
                      headers={"X-API-KEY": api_key}, timeout=30.0)
    try:
        meta = cx.get("/campaign/GetById", params={"campaignId": cid})
        meta.raise_for_status()
        meta = meta.json()
        _assert_same_campaign(campaign["campaign"].get("name"), meta.get("name"), cid)
        was_running = meta.get("status") == "IN_PROGRESS"

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

            edits_by_key = {(str(e["step"]), str(e["variant"]), e["field"]): e
                            for e in edits}
            touched = _hr_patch_tree(tree, edits_by_key, enabled)
            touched_keys = {tuple(item[:3]) for item in touched}
            missing = set(edits_by_key) - touched_keys
            if missing:
                _stale(sorted(missing)[0],
                       "no longer exists or no longer needs that fix")
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


# --- suggested removals -------------------------------------------------------
# Some leads should not be messaged at all. Cleaning the value is the wrong fix
# when the value is not a name, or when the "company" is a community the person
# belongs to rather than an employer — personalizing on it produces "relevant for
# Pavilion", which a real prospect called out as the tell that a message was
# automated.
#
# Suggestions only. Nothing is deleted: which leads to drop is a targeting call.

NOT_A_NAME = {
    "there", "friend", "team", "test", "null", "none", "na", "n/a", "unknown",
    "customer", "user", "admin", "hi", "hello", "guest", "member", "sir", "madam",
}
# Well-known B2B communities that enrichment routinely stores as an employer.
# Extend per client with --communities.
DEFAULT_COMMUNITIES = {
    "pavilion", "exit five", "exitfive", "rev genius", "revgenius", "wizard of ops",
    "peak community", "sales hacker", "demand curve", "superpath", "product marketing alliance",
    "the marketing meetup", "b2b marketing exchange", "gtm partners", "hey operations",
    "modern sales pros", "operations nation", "topline", "saastr", "dgmg", "marketingops",
}
URLISH = re.compile(r"(https?://|www\.|@|\.(com|io|ai|co|net|org)\b)", re.I)


def plan_removals(campaign, communities=None):
    """Leads worth excluding rather than correcting, each with a stated reason."""
    comms = {c.lower().strip() for c in (communities or DEFAULT_COMMUNITIES)}
    out = []

    def add(lead, reason, evidence):
        out.append({
            "lead_id": lead.get("id") or lead.get("email") or "",
            "lead_email": lead.get("email") or "",
            "name": lead.get("first_name") or "",
            "company": lead.get("company_name") or "",
            "reason": reason,
            "evidence": evidence,
        })

    for lead in campaign.get("leads", []) or []:
        fn = (lead.get("first_name") or "").strip()
        co = (lead.get("company_name") or "").strip()

        if fn:
            cleaned = _clean_first_name(fn)
            if not cleaned:
                add(lead, "name_unusable",
                    f"first name {fn!r} is nothing but symbols once cleaned")
            elif cleaned.lower() in NOT_A_NAME:
                add(lead, "name_is_placeholder",
                    f"first name {fn!r} is a placeholder, not a person")
            elif URLISH.search(fn):
                add(lead, "name_is_not_a_name", f"first name {fn!r} looks like a URL or address")

        if co:
            if co.lower().strip() in comms or _clean_company(co).lower() in comms:
                add(lead, "company_is_a_community",
                    f"{co!r} is a community, not an employer — the real employer is unknown, "
                    f"and personalizing on it reads as automated")
            else:
                # Judge the CLEANED value, not the raw one. "Globex Law
                # Office/www.globex.eu" is a real company with a URL bolted on
                # — that is a data fix, not a reason to drop the lead. Only
                # suggest removal when cleaning cannot recover anything usable.
                cc = _clean_company(co)
                if not cc:
                    add(lead, "company_unusable", f"company {co!r} is empty once cleaned")
                elif re.match(r"^(https?://|www\.)", cc, re.I) or "@" in cc:
                    add(lead, "company_unusable",
                        f"company {co!r} is still a URL or address after cleaning")

    return out


def write_removals_csv(rows, path):
    cols = ["lead_id", "lead_email", "name", "company", "reason", "evidence"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path
