## What changed?

Describe the user-visible behaviour this PR adds, fixes, or preserves.

Closes #

## Safety impact

- [ ] This change cannot affect reads from or writes to a live campaign.
- [ ] This change affects campaign data, rendering, verdicts, authentication, or `fix --apply`; the risk and failure path are described below.

If the second box applies, describe what happens on failure and how the user can recover:

## Tests

- [ ] `python3 tests/run_all.py` finishes with `ALL PASS`.
- [ ] If this touches an adapter, credentials, or platform requests, I verified a
      live path against Instantly or HeyReach. One supported platform passed.
- [ ] I added or updated a synthetic fixture where needed.
- [ ] For every changed check, I included a firing case and a near-miss.

## Data and documentation

- [ ] This PR contains no real campaign copy, prospect data, client names, screenshots, or credentials.
- [ ] I updated the README, schema, or adapter documentation for user-visible changes.

## Reviewer notes

Call out intentional changes to findings, severities, exit codes, golden files,
or platform requests.
