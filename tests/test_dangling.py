"""The dangling rule must still catch real defects and stay quiet on clean copy."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lastlook import check

cases = [
    # (label, body, should_fire)
    ("paragraph break (clean)",   "Hi Jane,\n\nAll good here.\n\nHernan",        False),
    ("trailing newline (clean)",  "Hi Jane,\n\nAll good.\n",                     False),
    ("real double space",         "Hi Jane,  two spaces there",                  True),
    ("space before comma",        "Hi Jane , odd",                               True),
    ("blank merge tell 'at .'",   "We work with teams at .",                     True),
    ("empty parens",              "Your team ( ) is scaling",                    True),
    ("greeting with no name",     "Hi ,\n\nGood stuff",                          True),
    ("normal sentence",           "Hi Jane,\n\nWe work with teams at Acme.",     False),
    # Grammatical English that a \s* pattern mistook for a collapsed merge —
    # 720 false BLOCKERS in the 34-campaign sweep.
    ("sentence ends in a preposition", "Anyone I could speak with?",              False),
    ("comma after a preposition",  "get in touch with, I'd appreciate it",        False),
    ("deliberate no-name greeting","Hey, not sure if you saw this",               False),
    ("period after a preposition", "who should I reach out to.",                  False),
    ("'of.' at a sentence end",    "I can send a demo of.",                       False),
    ("greeting with a real space gap", "Hey ,\n\nGood stuff",                     True),
]

fails = 0
for label, body, expect in cases:
    row = {"subject": "", "body": body, "subject_raw": "", "body_raw": body}
    got = bool(check.chk_dangling(row))
    ok = got == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:26} fired={got} expected={expect}")
print("\nall pass" if not fails else f"\n{fails} FAILURES")
