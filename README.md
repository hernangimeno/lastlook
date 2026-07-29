# lastlook

**See every message your cold campaign would actually send — before it sends.**

Your sequence builder shows you the template. Your prospect gets the *rendered*
message. The bugs that lose deals only exist in the gap between those two:

```
Template   Hi {{firstName}}, noticed {{company}} is scaling. {{ai_research}}.

Sends      Hi , noticed  is scaling. I couldn't find information about this
           company.
```

No campaign UI will show you that line before it goes out to 800 people.
`lastlook` renders every variant against every real lead, then runs 35 checks on
the output.

```bash
pip install lastlook
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
================================================================
```

Findings also land in a CSV, one row per lead × variant × issue, so you can push
the broken rows straight back into whatever enriches your data.

## Why the summary looks like that

Because a list of 40 findings does not get fixed. Findings are grouped by the
**action that clears them** — `LEAD_DUPLICATE`, `LEAD_ROLE_ADDRESS` and
`LEAD_INVALID_EMAIL` are all "clean the list before import", so they are one
line of work, not three. Capped at five, ranked by blast radius, each with a
time estimate, and what is already clean is stated instead of implied.

Use `--no-recap` for the raw table.

## What it catches

Run `lastlook rules` for the full catalog. The categories:

| | |
|---|---|
| **Merge failures** | Blank fields, tags with no value source, literal `{{tags}}` shipping, merge syntax the renderer cannot parse |
| **AI enrichment leaking** | "I couldn't find information", "as an AI", `null`, `N/A` sitting mid-sentence |
| **Raw CRM values** | `BOB`, `Acme Inc.` mid-sentence, `Hi John Smith,`, placeholder names, mojibake |
| **Lead list** | Duplicates, role inboxes (`info@`, `noreply@`), invalid addresses, free-mail in a B2B list, too many contacts at one domain |
| **Copy hygiene** | Invisible characters, spam vocabulary, placeholder text (`lorem ipsum`, `[insert]`, `TODO`), banned terms you supply |
| **Structure** | Duplicate A/B variants, shared openers, missing subject, follow-ups that break threading, steps with no gap between them |

Severity is **BLOCKER** (do not launch) or **WARNING** (launch with eyes open).

Every rule ships with a test that fires it *and* a near-miss that must stay
quiet. A checker that cries wolf on clean copy gets uninstalled, so the quiet
cases are treated as the important ones. `"we test your pipeline"` does not trip
the placeholder rule; `TEST` alone does. An en dash in `11–15 hours` is correct
typography and passes.

## Commands

```bash
lastlook audit instantly --campaign "Q3 ACME" --key $K   # pull, render, check
lastlook pull instantly --campaign "Q3 ACME" -o c.json   # fetch only
lastlook render c.json -o rendered.jsonl                 # render only
lastlook check rendered.jsonl --campaign-json c.json     # check only
lastlook coverage c.json                                 # merge-tag fill rates
lastlook rules                                           # the catalog
```

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
| `3` | Tool error |

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
pip install -e .
python3 tests/run_all.py           # behavioural + golden + schema + CLI
python3 tests/run_all.py --regen   # rewrite goldens — read the diff first
```

Goldens are regenerated deliberately, never to make red go green. Spintax choice
is seeded from the lead id, so renders are reproducible and diffable.

## License

MIT
