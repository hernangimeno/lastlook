"""Guard: no private data in a public repo.

This exists because client names and real prospect values reached this repo once
already, through the least suspicious door available: illustrative examples in
comments and tests. A live client's campaign copy is the most convenient fixture
in the world, and that is exactly why it must be blocked mechanically.

Two layers:

1. A DENYLIST of terms that must never appear, read from `.private-terms` in the
   repo root (one term per line, `#` comments allowed). That file is gitignored,
   because a list of client names is itself the confidential thing. Copy
   `.private-terms.example` and fill in your own. No file, no denylist pass — the
   test says so out loud rather than pretending it checked.

2. ALWAYS-ON heuristics that need no local config, so a fork gets them for free:
   API-key-shaped strings, and email addresses outside the synthetic domains the
   fixtures use. A real prospect's email in a test file is the same leak as a
   client name, and it arrives the same way.

Scans git-tracked text files only. Untracked scratch files are yours.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = 0


def fail(msg):
    global fails
    fails += 1
    print(f"FAIL  {msg}")


def tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    skip_ext = {".woff2", ".woff", ".ttf", ".otf", ".png", ".jpg", ".jpeg",
                ".gif", ".ico", ".pdf", ".zip"}
    for rel in filter(None, out.split("\0")):
        if os.path.splitext(rel)[1].lower() in skip_ext:
            continue
        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                yield rel, fh.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


FILES = list(tracked_text_files())
print(f"— scanning {len(FILES)} tracked text files —")

# --- layer 1: the local denylist ---------------------------------------------
denylist_path = os.environ.get("LASTLOOK_PRIVATE_TERMS") or os.path.join(ROOT, ".private-terms")
terms = []
if os.path.exists(denylist_path):
    with open(denylist_path, encoding="utf-8") as fh:
        terms = [ln.strip() for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")]

if not terms:
    print(f"SKIP  no denylist at {os.path.relpath(denylist_path, ROOT)} — "
          f"client-name check DID NOT RUN (copy .private-terms.example to enable)")
else:
    hits = 0
    for rel, text in FILES:
        if rel == ".private-terms.example":
            continue
        low = text.lower()
        for term in terms:
            t = term.lower()
            if t not in low:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if t in line.lower():
                    fail(f"private term {term!r} at {rel}:{n}")
                    hits += 1
    if not hits:
        print(f"PASS  none of {len(terms)} private term(s) appear in tracked files")

# --- layer 2: always-on heuristics -------------------------------------------
# Synthetic domains the fixtures and tests are allowed to use. Anything else that
# looks like a real mailbox is treated as a leak until it is added here.
SAFE_DOMAINS = {
    "acme.com", "acme.io", "globex.com", "initech.com", "initech.io", "contoso.com",
    "example.com", "example.org", "example.net", "b.com", "y.com", "test.com",
    "x.com", "acme.co.uk", "mail.acme.co.uk",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",   # free-mail RULE data
    "instantly.ai", "heyreach.io", "github.com",               # platform/vendor refs
}
EMAIL_RE = re.compile(r"\b[\w.+-]+@([\w-]+\.[\w.-]+)\b")
bad_emails = []
for rel, text in FILES:
    if rel.startswith(".private-terms"):
        continue
    for n, line in enumerate(text.splitlines(), 1):
        for m in EMAIL_RE.finditer(line):
            dom = m.group(1).lower().rstrip(".")
            if dom in SAFE_DOMAINS:
                continue
            # Role addresses are rule DATA (info@, noreply@), not real people.
            local = m.group(0).split("@")[0].lower()
            if local in {"info", "noreply", "no-reply", "sales", "support", "hello",
                         "admin", "contact", "billing", "team"}:
                continue
            bad_emails.append(f"{rel}:{n}  {m.group(0)}")
for hit in bad_emails:
    fail(f"email on a non-synthetic domain (add to SAFE_DOMAINS if it is fake): {hit}")
if not bad_emails:
    print("PASS  no email addresses outside the synthetic domain list")

# API-key-shaped literals. Deliberately narrow: long unbroken high-entropy runs
# that are not prose, not a hash in a lockfile, not a hex colour.
KEYISH_RE = re.compile(r"(?<![\w/.-])[A-Za-z0-9_-]{32,}(?![\w/.-])")
keyish = []
for rel, text in FILES:
    if rel.endswith((".lock", ".woff2")) or "/golden/" in rel or "/fonts/" in rel:
        continue
    for n, line in enumerate(text.splitlines(), 1):
        for m in KEYISH_RE.finditer(line):
            s = m.group(0)
            if s.count("_") + s.count("-") > 4:      # identifier-ish, not a key
                continue
            if not (re.search(r"\d", s) and re.search(r"[A-Za-z]", s)):
                continue
            keyish.append(f"{rel}:{n}  {s[:12]}…({len(s)} chars)")
for hit in keyish:
    fail(f"looks like a credential: {hit}")
if not keyish:
    print("PASS  no API-key-shaped literals")

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
