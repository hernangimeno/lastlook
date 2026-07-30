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
the live campaign from Instantly or HeyReach, renders every variant against every
real lead, and runs 35 checks on the output instead of the template.

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

That is a real run, pasted whole, against the three-lead fixture in this repo
(`tests/fixtures/fixture_planted_bugs.json`) — which is why the counts are small
and why two rules report as NOT CHECKED. Clone it and you get the same output.

Findings also land in a CSV, one row per lead × variant × issue, so the broken
rows go straight back into whatever enriches your data.

## Why the recap reads like a to-do list

Because a list of 40 findings never gets fixed. Findings are grouped by the
**action that clears them** — `LEAD_DUPLICATE`, `LEAD_ROLE_ADDRESS` and
`LEAD_INVALID_EMAIL` are all "clean the list before import", so they are one
line of work, not three. The recap caps at five lines, ranks them by blast
radius, and puts a time estimate on each. What came back clean is on the screen,
so you never have to assume it.

It also names the rules that did **not** run. Narrow the run with `--only`,
forget `--campaign-json`, and you get a `NOT CHECKED` block listing every rule
that sat out and why. A check that never ran but counts as clean is the same lie
as a missed defect, and much harder to spot.

Use `--no-recap` for the verdict table alone (the `Clean: N of M` line survives
it).

## What it catches

Run `lastlook rules` for the full catalog. The categories:

| | |
|---|---|
| **Merge failures** | Blank fields, tags with no value source, literal `{{tags}}` shipping, merge syntax the renderer cannot parse |
| **AI enrichment leaking** | "I couldn't find information", "as an AI", `null`, `N/A` sitting mid-sentence |
| **Raw CRM values** | `BOB`, `Acme Inc.` mid-sentence, `Hi John Smith,`, placeholder names, mojibake |
| **Lead list** | Duplicates, role inboxes (`info@`, `noreply@`), invalid addresses, free-mail in a B2B list, too many contacts at one domain |
| **Copy hygiene** | Invisible characters, spam vocabulary, em dashes used mid-sentence, placeholder text (`lorem ipsum`, `[insert]`, `TODO`), banned terms you supply — checked in the templates *and* in the rendered output, since a previous client's name usually arrives through the data |
| **Structure** | Duplicate A/B variants, shared openers, missing subject, follow-ups that break threading, steps with no gap between them |

Nineteen rules can block a launch, sixteen only warn. Three of the nineteen
decide per finding rather than by category: a three-day gap between steps warns,
a zero-day gap blocks. `lastlook rules` prints the severity of each.

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

Three ways to supply it, highest precedence first, and a prompt if you supply none:

```bash
lastlook audit instantly --key ...  # 1. the flag wins over everything
export INSTANTLY_API_KEY=...        # 2. environment variable
echo "INSTANTLY_API_KEY=..." > .env # 3. a .env file where you run lastlook
```

**If it cannot find a key and you are at a terminal, it asks.** Input is hidden,
and it offers to save what you paste to `.env` (chmod 600) so you only do it
once. It never asks when stdin is not a TTY: in cron, CI, or behind a pipe you
get one plain sentence and **exit 3**, never a stack trace and never exit 1. Exit
1 means "warnings only", so an auth failure reading as 1 would tell a script the
campaign passed. A prompt nobody can answer hangs the job forever.

Copy `.env.example` to `.env` to start. **`.env` is gitignored** — if you add
lastlook to an existing repo, check your own `.gitignore` covers it too.

The key is read, used for the request, and never written anywhere you did not
ask for: not to the findings CSV, not to the campaign JSON, not to any config
file. The one file it can land in is the `.env` you explicitly say yes to at the
prompt.

Prefer the env var or `.env` over `--key` for one more reason: a flag on the
command line is visible in `ps` output to every other user on the machine, not
just in your own shell history.

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

`fleet` takes a JSON list of campaigns and ranks them worst first. It samples 200
leads per campaign by default (`--max-leads`) rather than pulling every lead in
every list, and anything that flags can be re-run in full with `audit`:

```json
[{"platform": "instantly", "campaign": "Q3 Outbound", "key": "...", "name": "Q3"},
 {"platform": "heyreach",  "campaign": "12345",       "key": "...", "name": "LinkedIn"}]
```

## Fixing

`lastlook fix` splits the work by where it has to happen.

**Template fixes** are deterministic text edits, shown as a unified diff:
invisible characters, another platform's merge tags (`{FIRST_NAME}` in an
Instantly campaign), space before punctuation, doubled commas, em dashes used
mid-sentence. `--apply` writes them back to the platform after a typed confirmation.

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

## Contributing

Issues and pull requests are welcome. `main` is protected and every PR needs a
review from the code owner before it merges, because this tool writes to live
campaigns and a bad merge damages a stranger's sending domain, not a test fixture.

Before you open a PR:

```bash
python3 tests/run_all.py       # must print ALL PASS
```

A new rule needs two tests: one that fires it, and a near-miss that must stay
quiet. The second one is the one that matters — a checker that cries wolf on
clean copy gets uninstalled, and then it protects nobody.

## License

MIT
