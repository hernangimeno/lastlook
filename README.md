# lastlook

**A command-line tool that shows you every message your cold campaign would
actually send — before it sends.**

Your sequence builder shows you the template. Your prospect gets the *rendered*
message. Every bug that costs you a reply lives in the gap between the two:

```
Template   Hi {{firstName}}, noticed {{company}} is scaling. {{ai_research}}.

Sends      Hi , noticed  is scaling. I couldn't find information about this
           company.
```

No campaign UI shows you that line before it goes to 800 people. lastlook pulls
the live campaign, renders every variant against every real lead, and runs 35
checks on the output instead of the template.

```bash
pip install git+https://github.com/hernangimeno/lastlook
lastlook audit instantly --campaign "Q3 Outbound" --key $INSTANTLY_API_KEY
```

```
================================================================
START HERE → Edit the campaign copy  (~3 min)
================================================================
1. [MUST] Edit the campaign copy — 155 leads
      · Strip invisible characters (retype the line)
      · Reword spam-trigger vocabulary
      ~3 min — step 1 variant A, step 1 variant C
2. [then] Clean the raw values in Clay/CRM — 5 leads
      ~20 min — step 2 variant A
3. [then] Cap contacts per company domain — 2 places
      ~10 min

Clean: 31 of 35 checks found nothing.
3 fixes, roughly 33 min total.

lastlook can fix 2 of these for you (INVISIBLE_CHARS, DOUBLE_PUNCT).
    lastlook fix campaign.json            # show the diff, write nothing
    lastlook fix campaign.json --apply    # push it to the platform
================================================================
```

Findings also land in a CSV, one row per lead × variant × issue, so the broken
rows go straight back into whatever enriches your data.

## Why the summary reads like a to-do list

Because a list of 40 findings never gets fixed. Findings are grouped by the
**action that clears them** — `LEAD_DUPLICATE`, `LEAD_ROLE_ADDRESS` and
`LEAD_INVALID_EMAIL` are all "clean the list before import", so they are one
line of work, not three. Five lines maximum, ranked by blast radius, each with a
time estimate. What is already clean is stated, not implied.

It also states what did **not** run. Narrow the run with `--only`, forget
`--campaign-json`, and you get a `NOT CHECKED` block naming every rule that sat
out and why. A count of checks that never happened is the same lie as a missed
defect, and harder to notice.

Use `--no-recap` for the raw table (the coverage line survives it).

## What it catches

Run `lastlook rules` for the full catalog. The categories:

| | |
|---|---|
| **Merge failures** | Blank fields, tags with no value source, literal `{{tags}}` shipping, merge syntax the renderer cannot parse |
| **AI enrichment leaking** | "I couldn't find information", "as an AI", `null`, `N/A` sitting mid-sentence |
| **Raw CRM values** | `BOB`, `Acme Inc.` mid-sentence, `Hi John Smith,`, placeholder names, mojibake |
| **Lead list** | Duplicates, role inboxes (`info@`, `noreply@`), invalid addresses, free-mail in a B2B list, too many contacts at one domain |
| **Copy hygiene** | Invisible characters, spam vocabulary, placeholder text (`lorem ipsum`, `[insert]`, `TODO`), banned terms you supply — checked in the templates *and* in the rendered output, since a previous client's name usually arrives through the data |
| **Structure** | Duplicate A/B variants, shared openers, missing subject, follow-ups that break threading, steps with no gap between them |

Severity is **BLOCKER** (do not launch) or **WARNING** (launch with eyes open).

Every rule ships with a test that fires it *and* a near-miss that must stay
quiet. A checker that cries wolf on clean copy gets uninstalled, so the quiet
cases matter more than the loud ones. `"we test your pipeline"` does not trip the
placeholder rule; `TEST` alone does. An en dash in `11–15 hours` is correct
typography and passes.

## Credentials

lastlook reads your campaign from the platform you already pay for, so it needs
that platform's own API key. Nothing else: no account, no signup, no server of
mine in the path. It runs locally and talks only to your platform.

| Platform | Where the key lives |
|---|---|
| Instantly | Settings → Integrations → API Key |
| HeyReach | Settings → API keys |

Three ways to supply it, in precedence order:

```bash
export INSTANTLY_API_KEY=...        # 1. environment variable
echo "INSTANTLY_API_KEY=..." > .env # 2. a .env file where you run lastlook
lastlook audit instantly --key ...  # 3. the flag
```

**If it cannot find a key and you are at a terminal, it asks.** Input is hidden,
and it offers to save what you paste to `.env` (chmod 600) so you only do it
once. It never asks when stdin is not a TTY — in cron, CI, or behind a pipe it
prints the message and exits 3, because a prompt nobody can answer hangs the job
forever.

Copy `.env.example` to `.env` to start. **`.env` is gitignored** — if you add
lastlook to an existing repo, check your own `.gitignore` covers it too.

The key is read, used for the request, and never written anywhere you did not
ask for: not to the findings CSV, not to the campaign JSON, not to any config
file. The one file it can land in is the `.env` you explicitly say yes to at the
prompt.

Prefer the env var or `.env` over `--key` for one more reason: a flag on the
command line is visible in `ps` output to every other user on the machine, not
just in your own shell history.

If a key is missing or rejected you get a plain sentence and **exit 3**, never a
stack trace and never exit 1. Exit 1 means "warnings only", so an auth failure
reading as 1 would tell a script the campaign passed.

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

`fleet` takes a JSON list of campaigns and ranks them worst first, sampling leads
so a 20-campaign scan stays quick:

```json
[{"platform": "instantly", "campaign": "Q3 Outbound", "key": "...", "name": "Q3"},
 {"platform": "heyreach",  "campaign": "12345",       "key": "...", "name": "LinkedIn"}]
```

## Fixing

`lastlook fix` splits the work by where it has to happen.

**Template fixes** are deterministic text edits, shown as a unified diff:
invisible characters, another platform's merge tags (`{FIRST_NAME}` in an
Instantly campaign), space before punctuation, doubled commas, em dashes used as
prose. `--apply` writes them back to the platform after a typed confirmation.

**Data fixes** are bad values on the leads: `🔷Marco`, `Dr. Sam`,
`Initech Ltd`, `Contoso® Media AG 🇨🇭`. lastlook cannot fix your CRM, so these
export as a CSV of current → suggested.

**Suggested removals** are the third output: leads worth excluding rather than
correcting. A first name that is nothing but symbols, a placeholder like
`there`, or a "company" that is actually a community the person belongs to —
enrichment routinely stores Pavilion or Exit Five as an employer, and
personalizing on it produces "relevant for Pavilion", which reads as automated
to the recipient. Extend the community list with `--communities`.

The line between a data fix and a removal is whether cleaning can recover
anything usable. `Initech.co | We are hiring!` is a fix; `🌀` is a removal.
lastlook never drops a lead — it only tells you which ones to drop.

What it deliberately will **not** auto-fix: spam vocabulary, blank merge fields,
placeholder text, duplicate variants, pacing. Every one of those needs a
judgement about the offer, and a fixer that guesses does more damage than the
defect.

On HeyReach, a sequence cannot be written while the campaign runs, so `--apply`
pauses it, updates, and resumes. If the resume fails you are told loudly and by
name — a campaign left silently paused is worse than the bug being fixed.

`coverage` is the proactive view of the blank-merge problem: instead of finding
one empty `{{title}}` at a time, it tells you up front that `title` fills for 12%
of the list.

Useful flags:

```
--forbidden-terms "OldClient,Competitor"   # or a path to a file
--disable EM_DASH,LEAD_FREEMAIL            # skip rules
--only PLACEHOLDER_TEXT                    # run just these
--check-links                              # probe every URL in the copy
--no-recap                                 # verdict table only
```

A misspelled rule name is a hard error, never a silent no-op. A filter that
quietly matched nothing would let a campaign look clean for the wrong reason.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clear |
| `1` | Warnings only |
| `2` | Blockers — do not launch |
| `3` | Tool error, and nothing was checked |

`3` covers every case where the tool did not do its job: a bad key, a missing
file, a mistyped flag, a campaign that rendered nothing. Usage errors exit `3`
and not argparse's usual `2`, because `2` here means "this campaign will damage
your domain" and a wrapper script has to be able to tell those apart.

Rendering zero messages is always exit `3`, never "clear". A pass over nothing
is the most dangerous output this tool could produce.

## Supported platforms

Instantly (email) and HeyReach (LinkedIn) out of the box.

Everything after the adapter is platform-agnostic: both emit the same normalized
JSON, and `render` and `check` never learn which one produced it. Adding a
platform is one file.

## Writing an adapter

Emit the shape in [`lastlook/schema/campaign.schema.json`](lastlook/schema/campaign.schema.json)
and everything else works. Read that file, not another adapter.

Three fields where `null` and "empty" mean different things, and the schema says
so explicitly:

- `defined_vars: null` — the platform has no variable registry, so the
  undefined-tag check is skipped. `[]` means it has one and it is empty, which
  flags every tag in the copy.
- `delay_days: null` — the platform did not state a gap, so the pacing check is
  skipped. `0` means no gap, which is a blocker.
- `lead.email: null` — legitimate on a LinkedIn campaign; the list-hygiene rules
  skip those campaigns entirely.

Silence about a thing is not evidence about a thing. Rules skip rather than
assume.

## Development

```bash
git clone https://github.com/hernangimeno/lastlook && cd lastlook
pip install -e .
python3 tests/run_all.py           # behavioural + golden + schema + CLI
python3 tests/run_all.py --regen   # rewrite goldens — read the diff first
```

Goldens are regenerated deliberately, never to make red go green. Spintax choice
is seeded from the lead id, so renders are reproducible and diffable.

`tests/test_no_private_data.py` fails the suite if a git-tracked file contains a
name from your local `.private-terms` denylist, an email on a domain outside the
synthetic set, or anything shaped like an API key. Copy `.private-terms.example`
to `.private-terms` (gitignored) and put your clients' names in it. Real campaign
copy is the most convenient test fixture there is, which is exactly why the check
is mechanical rather than a matter of remembering.

## License

MIT
