"""recap.py — the short "what do I actually fix" summary.

The verdict table answers "what is wrong". This answers "what do I do", which is
a different question with a different shape: findings grouped by the ACTION that
clears them, ranked by whether they block, then by blast radius.

Ten rules firing can be three fixes. LEAD_DUPLICATE, LEAD_ROLE_ADDRESS and
LEAD_INVALID_EMAIL are all "clean the list before import". Reporting them as
three separate lines makes the work look bigger than it is, and a checklist that
looks bigger than it is does not get started.
"""

from collections import OrderedDict

BLOCKER, WARNING = "BLOCKER", "WARNING"

# rule -> (fix-group key, imperative action, where the fix happens)
# The group key is what collapses several rules into one line of work.
FIXES = {
    # --- the copy itself
    "EM_DASH":             ("copy_edit", "Remove em/en dashes from the copy", "template"),
    "PLACEHOLDER_TEXT":    ("copy_edit", "Delete placeholder text left in the copy", "template"),
    "FORBIDDEN_TERM":      ("copy_edit", "Remove banned terms from the copy", "template"),
    "SPAM_VOCAB":          ("copy_edit", "Reword spam-trigger vocabulary", "template"),
    "INVISIBLE_CHARS":     ("copy_edit", "Strip invisible characters (retype the line)", "template"),
    "SUBJECT_STYLE":       ("copy_edit", "Tone down the subject line", "template"),
    "LINK_IN_FIRST_TOUCH": ("copy_edit", "Remove the link from the first LinkedIn touch", "template"),

    # --- merge fields and fallbacks
    "UNDEFINED_TAG":       ("merge_fix", "Fix merge tags that have no value source", "template"),
    "UNRESOLVED_VAR":      ("merge_fix", "Fix merge tags the renderer cannot parse", "template"),
    "UNKNOWN_SYNTAX":      ("merge_fix", "Replace unsupported merge syntax", "template"),
    "BLANK_MERGE":         ("fallbacks", "Add fallbacks or enrich the blank fields", "data"),
    "DATA_GAP":            ("fallbacks", "Add fallbacks or enrich the blank fields", "data"),
    "DANGLING_TEXT":       ("fallbacks", "Add fallbacks or enrich the blank fields", "data"),
    "DOUBLE_PUNCT":        ("fallbacks", "Fix punctuation at the merge seam", "template"),

    # --- the values coming out of the CRM
    "CASING":              ("clean_values", "Title-case the raw CRM values", "data"),
    "LEGAL_SUFFIX":        ("clean_values", "Strip legal suffixes from company names", "data"),
    "FULL_NAME_GREETING":  ("clean_values", "Split full names into a first-name field", "data"),
    "NAME_QUALITY":        ("clean_values", "Fix or drop malformed first-name values", "data"),
    "COMPANY_QUALITY":     ("clean_values", "Fix or drop malformed company values", "data"),
    "CLAYGENT_APOLOGY":    ("clean_values", "Blank the failed AI-enrichment values", "data"),

    # --- the lead list
    "LEAD_NO_EMAIL":       ("clean_list", "Clean the lead list before import", "list"),
    "LEAD_INVALID_EMAIL":  ("clean_list", "Clean the lead list before import", "list"),
    "LEAD_DUPLICATE":      ("clean_list", "Clean the lead list before import", "list"),
    "LEAD_ROLE_ADDRESS":   ("clean_list", "Clean the lead list before import", "list"),
    "LEAD_FREEMAIL":       ("clean_list", "Clean the lead list before import", "list"),
    "LEAD_OVER_CONTACT":   ("cap_domain", "Cap contacts per company domain", "list"),

    # --- campaign structure
    "VARIANT_NOT_DISTINCT": ("structure", "Make the A/B variants actually different", "settings"),
    "SHARED_OPENER":       ("structure", "Give each variant its own opening line", "settings"),
    "EMPTY_SUBJECT":       ("structure", "Add a subject to the first email", "settings"),
    "THREAD_BREAK":        ("structure", "Clear the follow-up subject so it threads", "settings"),
    "STEPS_NOT_PACED":     ("structure", "Space the steps further apart", "settings"),
    "AB_SIGNAL_COLLISION": ("structure", "Give colliding variants distinct signals", "settings"),
    "BROKEN_HANDOFF":      ("structure", "Repoint the broken Instantly handoff", "settings"),
    "LENGTH":              ("structure", "Cut the message under the channel limit", "template"),
    "LINK_HEALTH":         ("structure", "Replace the dead links", "template"),
}

WHERE_LABEL = {
    "template": "in the campaign copy",
    "data":     "in Clay/CRM",
    "list":     "in the lead list",
    "settings": "in the campaign settings",
}

# Minutes per fix group, for a person who knows where the campaign lives.
# Vague estimates ("some work") read the same as "a few hours" and get deferred,
# so every line carries a number even when the number is rough. Groups that mean
# re-running enrichment cost more than groups that mean editing a string.
BASE_MINUTES = {
    "copy_edit":    2,    # find and retype a phrase
    "merge_fix":    5,    # find the tag, point it at a real variable
    "fallbacks":   10,    # decide a fallback, set it on each tag
    "clean_values": 20,   # a Clay column or a CRM export pass
    "clean_list":   5,    # filter and re-import
    "cap_domain":  10,    # dedupe per account, re-import
    "structure":    5,    # a settings toggle or a copy swap
    "other":        5,
}


# A group can hold several rules with different one-line actions. The heading
# has to describe the GROUP, otherwise it reads as whichever rule happened to be
# seen first — "Reword spam vocabulary" on a group that is mostly invisible
# characters. Specific per-rule actions go underneath.
# Self-contained: each already says WHERE, so the heading does not also append
# WHERE_LABEL and read "Edit the campaign copy in the campaign copy".
GROUP_LABEL = {
    "copy_edit":    "Edit the campaign copy",
    "merge_fix":    "Fix the merge tags in the copy",
    "fallbacks":    "Add fallbacks, or enrich the blank fields",
    "clean_values": "Clean the raw values in Clay/CRM",
    "clean_list":   "Clean the lead list before import",
    "cap_domain":   "Cap contacts per company domain",
    "structure":    "Fix the campaign settings",
    "other":        "Resolve the remaining findings",
}

# Rules whose count is a number of LEADS. Everything else is campaign-level, so
# reporting "1 lead" for it is wrong — 7 contacts at one domain is one finding.
# Rules lastlook can resolve itself, and how. A finding the tool could have
# fixed but never mentioned is a worse failure than the finding: the user does
# by hand what the CLI would have done in two seconds. Kept in sync with
# fix.TEMPLATE_FIXES and fix.DATA_CLEANERS by a test.
AUTOFIX_TEMPLATE = {
    "INVISIBLE_CHARS", "UNKNOWN_SYNTAX", "DANGLING_TEXT", "DOUBLE_PUNCT", "EM_DASH",
}
AUTOFIX_DATA = {
    "CASING", "LEGAL_SUFFIX", "FULL_NAME_GREETING", "NAME_QUALITY", "COMPANY_QUALITY",
}

CAMPAIGN_LEVEL = {
    "LEAD_OVER_CONTACT", "VARIANT_NOT_DISTINCT", "SHARED_OPENER", "EMPTY_SUBJECT",
    "THREAD_BREAK", "STEPS_NOT_PACED", "AB_SIGNAL_COLLISION", "BROKEN_HANDOFF",
    "PLACEHOLDER_TEXT", "FORBIDDEN_TERM", "LINK_HEALTH",
}


def _scope(group):
    """How big is this, in the unit that actually means something."""
    if group["rules"] <= CAMPAIGN_LEVEL:
        n = group["issues"]
        return f"{n} place{'s' if n != 1 else ''}"
    if group["leads"] > 1:
        return f"{group['leads']:,} leads"
    return ""


def _estimate(group):
    """Minutes for one fix group, scaled by how many places it has to be applied."""
    base = BASE_MINUTES.get(group["key"], 5)
    places = max(1, len(group["places"]))
    mins = base + (places - 1) * max(1, base // 2)
    if mins < 60:
        return f"~{mins} min"
    hours = mins / 60.0
    return f"~{hours:.0f}h" if hours >= 2 else "~1h"


def build(issues, max_items=5):
    """Group deduped issues into fix-actions, worst first.

    `issues` is the output of check.dedup_issues — one entry per distinct issue,
    carrying `leads` (how many leads it touches), `check`, `severity`, and the
    step/variant it was found at.
    """
    groups = OrderedDict()
    for iss in issues:
        rule = iss["check"]
        key, action, where = FIXES.get(rule, ("other", f"Resolve {rule}", "template"))
        g = groups.setdefault(key, {
            "key": key, "action": action, "where": where, "severity": WARNING,
            "leads": 0, "issues": 0, "rules": set(), "places": [],
        })
        if iss["severity"] == BLOCKER:
            g["severity"] = BLOCKER
        g["leads"] = max(g["leads"], iss.get("leads", 0))
        g["issues"] += 1
        g["rules"].add(rule)
        place = _place(iss)
        if place and place not in g["places"]:
            g["places"].append(place)

    ranked = sorted(groups.values(),
                    key=lambda g: (g["severity"] != BLOCKER, -g["leads"], -g["issues"]))
    return ranked[:max_items], max(0, len(ranked) - max_items)


def _place(iss):
    step, variant = iss.get("step"), iss.get("variant")
    if step in (None, "", "(campaign-level)"):
        return ""
    if variant in (None, "", "(template)"):
        return f"step {step}"
    return f"step {step} variant {variant}"


def render(issues, max_items=5, rules_run=None, campaign_path=None, skipped=None,
           fixable_rules=None):
    """The recap block, as a string.

    Shaped for someone who has to ACT on it, not study it:
      - the first line is a thing to do, not a heading
      - work is numbered, one bounded action per line, capped at five
      - every line carries a concrete minute estimate
      - what is already clean is stated, not left implied
      - and what did NOT run is stated too: `rules_run` is the set that actually
        ran, never the catalog, so the count can never overstate the coverage
      - it ends with one action that takes under two minutes

    Returns the all-clear block when there is nothing to fix.
    """
    ranked, overflow = build(issues, max_items)
    total_rules = len(rules_run) if rules_run else None

    if not ranked:
        out = ["", "=" * 64, "Nothing to fix. Launch it.", "=" * 64]
        if total_rules:
            out.append(f"{total_rules} checks ran, none fired.")
        out.extend(_skipped_lines(skipped))
        return "\n".join(out)

    blockers = [g for g in ranked if g["severity"] == BLOCKER]
    first = ranked[0]

    lines = ["", "=" * 64]
    # Rule 1: the first line is the action, not a banner.
    lines.append(f"START HERE → {GROUP_LABEL[first['key']]}  ({_estimate(first)})")
    lines.append("=" * 64)

    total = 0
    for n, g in enumerate(ranked, 1):
        tag = "MUST" if g["severity"] == BLOCKER else "then"
        if n == 1 and g["severity"] != BLOCKER:
            tag = "now"
        scope = _scope(g)
        total += _minutes(g)
        lines.append(f"{n}. [{tag}] {GROUP_LABEL[g['key']]}"
                     + (f" — {scope}" if scope else ""))
        # The specific edits inside this group, so the line is actionable and not
        # just a category name. Skipped when there is one rule and the sub-bullet
        # would only restate the heading.
        if len(g["rules"]) > 1:
            for rule in sorted(g["rules"]):
                lines.append(f"      · {FIXES.get(rule, ('', rule, ''))[1]}")
        if g["places"]:
            shown = ", ".join(g["places"][:3])
            more = f" +{len(g['places']) - 3} more" if len(g["places"]) > 3 else ""
            lines.append(f"      {_estimate(g)} — {shown}{more}")
        else:
            lines.append(f"      {_estimate(g)}")
    if overflow:
        lines.append(f"   ... {overflow} more fix group(s), all lower priority")

    lines.append("")
    # Rule 7: make the clean part visible instead of only listing damage.
    if total_rules:
        fired = len({r for g in ranked for r in g["rules"]})
        lines.append(f"Clean: {total_rules - fired} of {total_rules} checks found nothing.")
    lines.extend(_skipped_lines(skipped))
    plural = "fix" if len(ranked) == 1 else "fixes"
    lines.append(f"{len(ranked)} {plural}, roughly {_fmt_minutes(total)} total."
                 + ("" if blockers else " None of it blocks launch."))

    # Tell the user what they do NOT have to do by hand.
    fired = {r for g in ranked for r in g["rules"]}
    # fixable_rules is what `lastlook fix` would ACTUALLY change, planned against
    # the campaign. Promising a fix from the finding code alone over-promised: a
    # doubled period created at the merge seam is a defect in the rendered output
    # with nothing in the template to edit, so `fix` found one edit where the
    # recap had advertised two.
    tpl = (fired & AUTOFIX_TEMPLATE) if fixable_rules is None else (fired & set(fixable_rules))
    dat = fired & AUTOFIX_DATA
    if tpl or dat:
        lines.append("")
        if tpl:
            lines.append(f"lastlook can fix {len(tpl)} of these for you "
                         f"({', '.join(sorted(tpl))}).")
        if dat:
            lines.append(f"It can also suggest corrected values for "
                         f"{len(dat)} ({', '.join(sorted(dat))}).")
        if campaign_path:
            lines.append(f"    lastlook fix {campaign_path}            # show the diff, write nothing")
            lines.append(f"    lastlook fix {campaign_path} --apply    # push it to the platform")
        else:
            # `audit` streams and never writes the campaign to disk, so pointing
            # at a file that does not exist would just fail for the user.
            lines.append("    lastlook pull <platform> --campaign <x> -o c.json")
            lines.append("    lastlook fix c.json [--apply]")
    lines.append("=" * 64)
    return "\n".join(lines)


def _minutes(group):
    base = BASE_MINUTES.get(group["key"], 5)
    places = max(1, len(group["places"]))
    return base + (places - 1) * max(1, base // 2)


def _fmt_minutes(mins):
    if mins < 60:
        return f"{mins} min"
    return f"{mins / 60.0:.1f}h".replace(".0h", "h")


def _skipped_lines(skipped):
    """State every check that did not run, grouped by why.

    Silence here reads as coverage. A user who narrows the run with --only, or
    forgets --campaign-json, should see the hole rather than a green verdict over
    it."""
    if not skipped:
        return []
    by_reason = {}
    for rule, reason in sorted(skipped.items()):
        by_reason.setdefault(reason, []).append(rule)
    out = [f"NOT CHECKED: {len(skipped)} rule(s) did not run."]
    for reason, rules in sorted(by_reason.items()):
        shown = ", ".join(rules[:6])
        more = f" +{len(rules) - 6} more" if len(rules) > 6 else ""
        out.append(f"    {reason}: {shown}{more}")
    return out
