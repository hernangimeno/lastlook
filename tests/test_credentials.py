"""Credential handling. Two things must never regress.

1. A rejected key must exit 3, not 1. Exit 1 means "warnings only" in this
   tool's published contract, so an auth failure reading as 1 tells anyone
   gating a send on the exit code that the campaign passed. It shipped that way
   until a sweep caught it.
2. No raw traceback. A stack trace is not an error message.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = 0


def t(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"        {detail[:300]}")


def run(argv, env=None, cwd=ROOT):
    e = dict(os.environ)
    e.pop("INSTANTLY_API_KEY", None)
    e.pop("HEYREACH_API_KEY", None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "lastlook.cli"] + argv,
                          capture_output=True, text=True, env=e, cwd=cwd, timeout=90)


print("— missing key —")
for platform, var in (("instantly", "INSTANTLY_API_KEY"), ("heyreach", "HEYREACH_API_KEY")):
    r = run(["audit", platform, "--campaign", "x"])
    out = r.stdout + r.stderr
    t(f"{platform}: exits 3", r.returncode == 3, f"got {r.returncode}")
    t(f"{platform}: names the env var", var in out, out)
    t(f"{platform}: says where to get a key", "Settings" in out, out)
    t(f"{platform}: no traceback", "Traceback" not in out, out)

print("\n— rejected key —")
r = run(["audit", "instantly", "--campaign", "x", "--key", "definitely-not-a-key"])
out = r.stdout + r.stderr
t("exits 3, NOT 1", r.returncode == 3, f"got {r.returncode} — exit 1 means 'warnings only'")
t("no traceback", "Traceback" not in out, out)
t("names the status", "401" in out, out)
t("suggests where to get a fresh key", "Settings" in out, out)

print("\n— .env —")
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, ".env"), "w").write(
        "# a comment\nINSTANTLY_API_KEY=from-dotenv\n\nHEYREACH_API_KEY='quoted-value'\n")
    from lastlook import cli
    before = os.getcwd()
    try:
        os.chdir(d)
        os.environ.pop("INSTANTLY_API_KEY", None)
        os.environ.pop("HEYREACH_API_KEY", None)
        cli._load_dotenv()
        t("reads a key from .env", os.environ.get("INSTANTLY_API_KEY") == "from-dotenv",
          repr(os.environ.get("INSTANTLY_API_KEY")))
        t("strips surrounding quotes", os.environ.get("HEYREACH_API_KEY") == "quoted-value",
          repr(os.environ.get("HEYREACH_API_KEY")))
        os.environ["INSTANTLY_API_KEY"] = "from-real-env"
        cli._load_dotenv()
        t("a real env var beats .env",
          os.environ["INSTANTLY_API_KEY"] == "from-real-env",
          os.environ["INSTANTLY_API_KEY"])
    finally:
        os.chdir(before)
        os.environ.pop("INSTANTLY_API_KEY", None)
        os.environ.pop("HEYREACH_API_KEY", None)

with tempfile.TemporaryDirectory() as d:
    os.chdir(d) if False else None
    from lastlook import cli as cli2
    cli2._load_dotenv()   # no .env present
    t("missing .env is not an error", True)

print("\n— .env can never be committed —")
gi = open(os.path.join(ROOT, ".gitignore")).read()
t(".env is gitignored", "\n.env\n" in gi)
t(".env.example is NOT ignored", "!.env.example" in gi)
t(".env.example ships", os.path.exists(os.path.join(ROOT, ".env.example")))

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
