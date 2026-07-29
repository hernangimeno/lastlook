"""The recap has to stay actionable. These lock the shape, not the prose.

The failure mode for a summary is not being wrong, it is being ignorable:
un-numbered, un-estimated, or leading with a heading instead of an action.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import recap

fails = 0


def check(label, cond):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")


def issue(rule, sev="WARNING", leads=10, step=1, variant="A"):
    return {"check": rule, "severity": sev, "leads": leads, "step": step, "variant": variant}


# --- grouping ----------------------------------------------------------------
print("— grouping —")
list_rules = [issue("LEAD_DUPLICATE"), issue("LEAD_ROLE_ADDRESS"), issue("LEAD_INVALID_EMAIL")]
groups, _ = recap.build(list_rules)
check("three list rules collapse into one fix", len(groups) == 1)
check("that fix is 'clean the list'", groups[0]["key"] == "clean_list")

mixed = [issue("EM_DASH"), issue("LEAD_DUPLICATE"), issue("CASING")]
groups, _ = recap.build(mixed)
check("unrelated rules stay separate", len(groups) == 3)

# --- ranking -----------------------------------------------------------------
print("\n— ranking —")
ranked, _ = recap.build([issue("CASING", leads=500), issue("EM_DASH", "BLOCKER", leads=1)])
check("blockers rank above bigger warnings", ranked[0]["severity"] == "BLOCKER")

ranked, _ = recap.build([issue("CASING", leads=5), issue("EM_DASH", leads=900)])
check("among warnings, blast radius wins", ranked[0]["leads"] == 900)

# --- the five-item cap -------------------------------------------------------
print("\n— cap —")
many = [issue(r) for r in ("EM_DASH", "LEAD_DUPLICATE", "CASING", "UNDEFINED_TAG",
                           "BLANK_MERGE", "EMPTY_SUBJECT", "LINK_HEALTH")]
ranked, overflow = recap.build(many, max_items=5)
check("never more than five fixes shown", len(ranked) <= 5)
check("the remainder is counted, not dropped", overflow > 0)
check("overflow is stated in the output", f"{overflow} more fix group" in recap.render(many))

# --- output shape ------------------------------------------------------------
print("\n— shape —")
text = recap.render([issue("EM_DASH", "BLOCKER"), issue("LEAD_DUPLICATE")],
                    rules_run=recap.FIXES)
first_real = [ln for ln in text.splitlines() if ln.strip() and set(ln.strip()) != {"="}][0]
check("first line is an action, not a heading", first_real.startswith("START HERE →"))
check("work is numbered", "\n1. " in text)
check("every fix carries a time estimate", text.count("~") >= 2)
check("total effort is stated once", "total." in text)
check("what is clean is stated", "Clean:" in text)
check("no closing pleasantry", not any(
    p in text.lower() for p in ("hope this", "let me know", "feel free", "anything else")))

# --- the all-clear -----------------------------------------------------------
print("\n— all clear —")
clear = recap.render([], rules_run=recap.FIXES)
check("clean run says so plainly", "Nothing to fix" in clear)
check("clean run still reports the checks that ran", "checks ran" in clear)

# --- estimates ---------------------------------------------------------------
print("\n— estimates —")
one = recap.build([issue("EM_DASH", step=1, variant="A")])[0][0]
five = recap.build([issue("EM_DASH", step=i, variant="A") for i in range(1, 6)])[0][0]
check("more places costs more time", recap._minutes(five) > recap._minutes(one))
check("a Clay pass costs more than a string edit",
      recap.BASE_MINUTES["clean_values"] > recap.BASE_MINUTES["copy_edit"])

# --- every rule is mapped ----------------------------------------------------
print("\n— coverage of the catalog —")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check as chk
unmapped = set(chk.RULES) - set(recap.FIXES)
check(f"every rule has a fix action (unmapped: {sorted(unmapped) or 'none'})", not unmapped)
bad_group = {r for r, (k, _, _) in recap.FIXES.items() if k not in recap.GROUP_LABEL}
check(f"every fix group has a label (missing: {sorted(bad_group) or 'none'})", not bad_group)

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
