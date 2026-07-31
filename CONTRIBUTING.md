# Contributing to lastlook

Thanks for helping make campaign audits more trustworthy. lastlook can read and,
with explicit confirmation, write to live campaigns. Treat a change to its
verdicts, rendering, or adapters as a change to a safety boundary.

## Before you start

- Search existing issues and pull requests before opening a new one.
- Open an issue first for a new check, a new platform adapter, or a behaviour
  change that could alter a campaign verdict. It lets us agree on the failure
  mode and severity before implementation.
- Never include real campaign payloads, prospect data, client names, API keys,
  or screenshots containing them. Reduce a report to the smallest synthetic
  fixture that reproduces it.

## Local setup

```bash
git clone https://github.com/hernangimeno/lastlook
cd lastlook
python3 -m pip install -e .
python3 tests/run_all.py
```

The test command must finish with `ALL PASS`; a network failure is a failed
verification, not an exception to that rule. It covers behavioural checks,
goldens, schema validation, CLI contracts, and the guard against private data.
For a change that touches an adapter, credentials, or platform requests, also
verify the live path against **Instantly or HeyReach**. One supported platform
must pass; do not merge on an unverified integration path. Do not run `--regen`
just to make a failing test pass: regenerated goldens are a reviewable
consequence of an intentional behaviour change.

To enable the local client-name scan, copy `.private-terms.example` to
`.private-terms` and add terms that must not appear in tracked files. The file
is gitignored; never commit it.

## Making a change

1. Branch from the current `main` and keep the pull request focused.
2. Add or update the smallest synthetic fixture that demonstrates the change.
3. For every new or changed check, include both:
   - a case that fires the check; and
   - a near-miss that must stay quiet.
4. Preserve the public CLI contract. Exit `0` means clear, `1` means warnings,
   `2` means blockers, and `3` means the tool could not perform the audit.
5. Update the README, schema, or adapter documentation when behaviour visible
   to users changes.
6. Run `python3 tests/run_all.py` and describe the result in the PR.

Changes affecting `fix --apply`, platform writes, authentication, or campaign
normalisation should explain their failure path: what is written, what happens
if it fails part-way through, and how a user can recover safely.

## Pull requests

Use the PR template and link any related issue. Explain the user-visible
behaviour, the safety impact, and the tests that cover it. Keep generated output
and local campaign artifacts out of the PR.

`main` is protected. Every pull request requires code-owner approval before it
merges. This review is intentionally required because a false clear or unsafe
write can damage a live sending domain.

## Reporting security or data exposure

Do not open a public issue for an exposed credential, client data, or a way to
modify a campaign without the expected confirmation. Email the repository owner
privately with a minimal reproduction and enough detail to assess impact.

For ordinary bugs, a public issue with a synthetic fixture is welcome.
