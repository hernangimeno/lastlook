"""The interactive key prompt, driven through a real pseudo-terminal.

isatty() cannot be faked with a pipe, so a prompt guarded on it is untestable
without a pty. That guard is the important part: a prompt that fires in a cron
job or a CI step never gets answered and hangs the job forever, which is far
worse than the clear error it replaced. Both sides are asserted here — it must
ask a human, and it must never ask anything else.
"""
import os
import pty
import select
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = 0


def t(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print("        " + detail.replace("\n", "\n        ")[:500])


def _env():
    e = dict(os.environ)
    e.pop("INSTANTLY_API_KEY", None)
    e.pop("HEYREACH_API_KEY", None)
    # so `-m lastlook.cli` resolves no matter which directory we run from
    e["PYTHONPATH"] = ROOT + os.pathsep + e.get("PYTHONPATH", "")
    return e


def under_pty(argv, replies, cwd, timeout=25):
    """`replies` maps a prompt substring -> what to type.

    Keyed on the prompt TEXT, not on punctuation. An earlier version answered on
    any ":" / "?" / "]" and desynced whenever two prompts landed in one read,
    which made this test pass alone and fail under the runner."""
    m, s = pty.openpty()
    p = subprocess.Popen([sys.executable, "-m", "lastlook.cli"] + argv,
                         stdin=s, stdout=s, stderr=s, cwd=cwd, env=_env(), close_fds=True)
    os.close(s)
    out, answered, deadline = b"", set(), time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([m], [], [], 0.4)
        if r:
            try:
                chunk = os.read(m, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            for needle, reply in replies.items():
                if needle in out.decode(errors="replace") and needle not in answered:
                    answered.add(needle)
                    os.write(m, reply.encode() + b"\n")
        if p.poll() is not None and not r:
            break
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
    os.close(m)
    return p.returncode, out.decode(errors="replace")


print("— a human is present: ask —")
with tempfile.TemporaryDirectory() as d:
    rc, out = under_pty(["pull", "instantly", "--campaign", "x", "-o", f"{d}/c.json"],
                        {"Paste your": "bogus-key-typed-by-hand", "Save to": "n"}, cwd=d)
    t("prompts for the key", "Paste your instantly API key" in out, out)
    t("input is hidden", "input hidden" in out, out)
    t("says where to get one", "Settings" in out, out)
    t("actually uses what was typed (401, not 'no key')", "401" in out, out)
    t("bad key still exits 3", rc == 3, f"rc={rc}")
    t("declining the save writes nothing", not os.path.exists(f"{d}/.env"))

print("\n— accepting the save —")
with tempfile.TemporaryDirectory() as d:
    rc, out = under_pty(["pull", "instantly", "--campaign", "x", "-o", f"{d}/c.json"],
                        {"Paste your": "typed-key-abc", "Save to": "y"}, cwd=d)
    envp = os.path.join(d, ".env")
    t("offers to save", "Save to" in out, out)
    t("writes .env", os.path.exists(envp), out)
    if os.path.exists(envp):
        body = open(envp).read()
        t("under the right variable name", "INSTANTLY_API_KEY=typed-key-abc" in body, body)
        t("chmod 600", oct(os.stat(envp).st_mode & 0o777) == "0o600",
          oct(os.stat(envp).st_mode & 0o777))
        t("warns about gitignore", "gitignore" in out.lower(), out)

print("\n— an existing key is never clobbered —")
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, ".env"), "w").write("INSTANTLY_API_KEY=already-here\n")
    rc, out = under_pty(["pull", "instantly", "--campaign", "x", "-o", f"{d}/c.json"],
                        {"Paste your": "different-key", "Save to": "y"}, cwd=d)
    body = open(os.path.join(d, ".env")).read()
    # .env is loaded first, so it should not even reach the prompt
    t("existing .env key is used, no prompt", "Paste your" not in out, out)
    t("file untouched", body.strip() == "INSTANTLY_API_KEY=already-here", body)

print("\n— NO human: must fail fast, never hang —")
for label, argv in [("audit", ["audit", "instantly", "--campaign", "x"]),
                    ("pull", ["pull", "heyreach", "--campaign", "1"])]:
    start = time.time()
    try:
        # pipes, not a pty -> the guard must skip the prompt entirely
        r = subprocess.run([sys.executable, "-m", "lastlook.cli"] + argv,
                           capture_output=True, text=True, env=_env(), cwd=ROOT, timeout=20)
        rc, out, took = r.returncode, r.stdout + r.stderr, time.time() - start
        t(f"{label}: exits rather than waiting for input", True)
        t(f"{label}: exit 3", rc == 3, f"rc={rc}")
        t(f"{label}: never printed a prompt", "Paste your" not in out, out)
        t(f"{label}: returned quickly ({took:.1f}s)", took < 15, f"{took:.1f}s")
    except subprocess.TimeoutExpired:
        t(f"{label}: HUNG waiting for input that will never come", False)

print("\nall pass" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
