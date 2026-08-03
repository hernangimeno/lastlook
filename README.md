# lastlook

**A command-line tool that shows you every message your cold campaign would actually send, before it sends.**

Your sequence builder shows you the template. Your prospect gets the *rendered* message. The bugs that cost you replies live in the gap between the two:

```
Template   Hi {{firstName}}, noticed {{company}} is scaling. {{ai_research}}.

Sends      Hi , noticed  is scaling. I couldn't find information about this
           company.
```

No campaign UI shows you that line at scale for all your leads before it goes to 800 people. lastlook pulls the live campaign from Instantly or HeyReach, renders every variant against every real lead, and runs 35 checks on the output instead of the template.

```bash
pip install git+https://github.com/hernangimeno/lastlook
lastlook audit instantly --campaign "Q3 Outbound" --key $INSTANTLY_API_KEY
```

```

================================================================
START HERE → Clean the raw values in Clay/CRM  (~40 min)
================================================================
1. [MUST] Clean the raw values in Clay/CRM — 3 leads
      · Title-case the raw CRM values
      · Blank the failed AI-enrichment values
      · Strip legal suffixes from company names
      ~40 min — step 1 variant 1A, step 1 variant 1B, step 2 variant 2A
2. [MUST] Edit the campaign copy — 3 leads
      ~2 min — step 2 variant 2A
3. [MUST] Add fallbacks, or enrich the blank fields
      · Add fallbacks or enrich the blank fields
      · Fix punctuation at the merge seam
      ~20 min — step 1 variant 1A, step 1 variant 1B, step 2 variant 2A
4. [then] Fix the campaign settings — 2 places
      · Give colliding variants distinct signals
      · Clear the follow-up subject so it threads
      ~7 min — step 1 variant 1A+1B, step 2 variant 2A

Clean: 25 of 33 checks found nothing.
NOT CHECKED: 2 rule(s) did not run.
    needs --check-links: LINK_HEALTH
    needs --forbidden-terms: FORBIDDEN_TERM
4 fixes, roughly 1.1h total.

lastlook can fix 1 of these for you (EM_DASH).
It can also suggest corrected values for 2 (CASING, LEGAL_SUFFIX).
    lastlook fix c.json            # show the diff, write nothing
    lastlook fix c.json --apply    # push it to the platform
================================================================
```

That's a real run, pasted whole, against the three-lead fixture in this repo (`tests/fixtures/fixture_planted_bugs.json`). The counts are small and two rules report NOT CHECKED because it's a tiny fixture. Clone it and you get the same output.

Findings also land in a CSV, one row per lead × variant × issue, so the broken rows go straight back into whatever enriches your data.

## Why the recap reads like a to-do list

A list of 40 findings never gets fixed. So lastlook groups findings by the action that clears them: `LEAD_DUPLICATE`, `LEAD_ROLE_ADDRESS` and `LEAD_INVALID_EMAIL` are all "clean the list before import", so they collapse into one line of work. The recap caps at five lines, ranks them by blast radius, and puts a time estimate on each. It also prints what came back clean, so nothing passes by omission.

It names the rules that did **not** run, too. Narrow the run with `--only`, forget `--campaign-json`, and you get a `NOT CHECKED` block listing every rule that sat out and why. That block is there so a skipped rule never gets read as a passing one.

Use `--no-recap` for the verdict table alone (the `Clean: N of M` line survives it).

## What it catches

Run `lastlook rules` for the full catalog. The categories:

| | |
|---|---|
| **Merge failures** | Blank fields, tags with no value source, literal `{{tags}}` shipping, merge syntax the renderer cannot parse |
| **AI enrichment leaking** | "I couldn't find information", "as an AI", `null`, `N/A` sitting mid-sentence |
| **Raw CRM values** | `BOB`, `Acme Inc.` mid-sentence, `Hi John Smith,`, placeholder names, mojibake |
| **Lead list** | Duplicates, role inboxes (`info@`, `noreply@`), invalid addresses, free-mail in a B2B list, too many contacts at one domain |
| **Copy hygiene** | Invisible characters, spam vocabulary, em dashes used mid-sentence, placeholder text (`lorem ipsum`, `[insert]`, `TODO`), banned terms you supply. Checked in the templates and in the rendered output, since a previous client's name usually arrives through the data |
| **Structure** | Duplicate A/B variants, shared openers, missing subject, follow-ups that break threading, steps with no gap between them |

Nineteen rules can block a launch, sixteen only warn. Three of the nineteen decide per finding rather than by category: a three-day gap between steps warns, a zero-day gap blocks. `lastlook rules` prints the severity of each.

Every rule ships with a test that fires it and a near-miss that has to stay quiet, because a checker that cries wolf on clean copy gets uninstalled. `"we test your pipeline"` doesn't trip the placeholder rule; `TEST` alone does. An en dash in `11–15 hours` is correct typography and passes.

## Credentials

lastlook reads your campaign from the platform you already pay for, so all it needs is that platform's own API key. There's no account to create and no server of mine in the path. It runs locally and talks only to your platform.

| Platform | Where the key lives |
|---|---|
| Instantly | Settings → Integrations → API Key |
| HeyReach | Settings → API keys |

Three ways to supply it, highest precedence first, plus a prompt if you supply none:

```bash
lastlook audit instantly --key ...  # 1. the flag wins over everything
export INSTANTLY_API_KEY=...        # 2. environment variable
echo "INSTANTLY_API_KEY=..." > .env # 3. a .env file where you run lastlook
```

**If it can't find a key and you're at a terminal, it asks.** Input is hidden, and it offers to save what you paste to `.env` (chmod 600) so you only do it once. It never asks when stdin is not a TTY. In cron, CI, or behind a pipe you get one plain sentence and **exit 3**, never a stack trace and never exit 1. (Exit 1 means "warnings only", so an auth failure reading as exit 1 would tell a script the campaign passed. And a prompt nobody can answer would hang the job forever.)

Copy `.env.example` to `.env` to start. **`.env` is gitignored.** If you add lastlook to an existing repo, check your own `.gitignore` covers it too.

The key is read, used for the request, and never written anywhere you didn't ask for: not the findings CSV, not the campaign JSON, not any config file. The one place it can land is the `.env` you say yes to at the prompt.

Prefer the env var or `.env` over `--key` for one more reason: a flag on the command line shows up in `ps` output to every other user on the machine, not just in your own shell history.

## Commands

```bash
lastlook audit instantly --campaign "Q3 ACME" --key $K   # pull, render, check
lastlook pull instantly --campaign "Q3 ACME" -o c.json   # fetch only
lastlook render c.json -o rendered.jsonl                 # render only
lastlook check rendered.jsonl --campaign-json c.json     # check only
lastlook fix c.json                                      # show the safe fixes
lastlook fix c.json --apply                              # write them back
lastlook coverage c.json                                 # merge-tag fill rates
lastlook fleet --manifest m.json                         # many campaigns, worst first
lastlook rules                                           # the catalog, with severities
lastlook --version
```

`fleet` takes a JSON list of campaigns and ranks them worst first. It samples 200 leads per campaign by default (`--max-leads`) instead of pulling every lead in every list, and anything that flags can be re-run in full with `audit`:

```json
[{"platform": "instantly", "campaign": "Q3 Outbound", "key_env": "ACME_INSTANTLY_KEY", "name": "Q3"},
 {"platform": "heyreach",  "campaign": "12345",       "key_env": "ACME_HEYREACH_KEY",  "name": "LinkedIn"}]
```

Set those environment variables before the scan. If `key_env` is omitted, `fleet` falls back to `INSTANTLY_API_KEY` or `HEYREACH_API_KEY`. A literal `"key"` still works for older manifests, but lastlook warns: a manifest is easy to commit by accident, so use `lastlook.fleet.json` (gitignored here) and environment variables instead.

## Fixing

`lastlook fix` splits the work by where it has to happen.

**Template fixes** are deterministic text edits, shown as a unified diff: invisible characters, another platform's merge tags (`{FIRST_NAME}` in an Instantly campaign), space before punctuation, doubled commas, em dashes used mid-sentence. `--apply` writes them back to the platform after a typed confirmation.

**Data fixes** are bad values on the leads: `🔷Marco`, `Dr. Sam`, `Initech Ltd`, `Contoso® Media AG 🇨🇭`. lastlook can't touch your CRM, so these export as a CSV of current → suggested.

**Suggested removals** are the third output: leads worth excluding rather than correcting. A first name that's nothing but symbols, a placeholder like `there`, or a "company" that's actually a community the person belongs to. Enrichment routinely stores Pavilion or Exit Five as an employer, and personalizing on it produces "relevant for Pavilion", which reads as automated. Extend the community list with `--communities`.

The line between a data fix and a removal is whether cleaning can recover anything usable. `Initech.co | We are hiring!` is a fix; `🌀` is a removal. lastlook never drops a lead itself; it only tells you which ones to drop.

What it won't auto-fix, by design: spam vocabulary, blank merge fields, placeholder text, duplicate variants, pacing. Each needs a judgement about the offer that the tool can't make for you, so it leaves them flagged and untouched.

On HeyReach, a sequence can't be written while the campaign runs, so `--apply` pauses it, updates, and resumes. If the resume fails, you get a loud, named error instead of a campaign left quietly paused.

`coverage` is the proactive version of the blank-merge check: instead of catching one empty `{{title}}` at a time, it tells you up front that `title` fills for 12% of the list.

Useful flags:

```
--forbidden-terms "OldClient,Competitor"   # or a path to a file
--disable EM_DASH,LEAD_FREEMAIL            # skip rules
--only PLACEHOLDER_TEXT                    # run just these
--check-links                              # probe every URL in the copy
--no-recap                                 # verdict table only
```

Link probing is restricted to public HTTP(S) destinations on ports 80 and 443. Localhost, private and link-local addresses, URL credentials, and redirects to any of those are blocked, so campaign copy can't turn the checker into a route to an internal service.

A misspelled rule name is a hard error, never a silent no-op, so a filter can't quietly match nothing and leave the campaign looking clean for the wrong reason.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clear |
| `1` | Warnings only |
| `2` | Blockers — do not launch |
| `3` | Tool error, and nothing was checked |

`3` covers every case where the tool didn't do its job: a bad key, a missing file, a mistyped flag, a campaign that rendered nothing. Usage errors exit `3`, not argparse's usual `2`, because `2` here means "this campaign will damage your domain" and a wrapper script has to tell those apart.

Rendering zero messages is always exit `3`, never "clear": if nothing rendered, nothing was checked.

## Supported platforms

Instantly (email) and HeyReach (LinkedIn) out of the box.

Everything after the adapter is platform-agnostic. Both emit the same normalized JSON, and `render` and `check` never learn which one produced it. Adding a platform is one file.

## Writing an adapter

Emit the shape in [`lastlook/schema/campaign.schema.json`](lastlook/schema/campaign.schema.json) and everything else works. Read that file, not another adapter.

There are three fields where `null` and "empty" mean different things, and the schema says so:

- `defined_vars: null` — the platform has no variable registry, so the undefined-tag check is skipped. `[]` means it has one and it's empty, which flags every tag in the copy.
- `delay_days: null` — the platform didn't state a gap, so the pacing check is skipped. `0` means no gap, which is a blocker.
- `lead.email: null` — legitimate on a LinkedIn campaign, so the list-hygiene rules skip those campaigns entirely.

In each case a `null` disables the check rather than being read as an empty value that would fail it.

## Development

```bash
git clone https://github.com/hernangimeno/lastlook && cd lastlook
pip install -e .
python3 tests/run_all.py           # behavioural + golden + schema + CLI
python3 tests/run_all.py --regen   # rewrite goldens — read the diff first
```

Regenerate goldens deliberately, never to turn red green. Spintax choice is seeded from the lead id, so renders are reproducible and diffable.

`tests/test_no_private_data.py` fails the suite if a git-tracked file contains a name from your local `.private-terms` denylist, an email on a domain outside the synthetic set, or anything shaped like an API key. Copy `.private-terms.example` to `.private-terms` (gitignored) and put your clients' names in it. Real campaign copy makes a convenient test fixture, which is exactly why this check is mechanical instead of a thing you have to remember.

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, fixture and privacy rules, the required test coverage, and the review process. The suite has to finish with `ALL PASS`, and an adapter or credential change also needs a live path through Instantly or HeyReach. `main` is protected: every PR needs code-owner approval before it merges, because this tool writes to live campaigns.

## License

MIT
