# lastlook — design

**Date:** 2026-07-29
**Status:** approved, not yet implemented
**Author:** Hernán Gimeno

## What it is

A CLI that renders every message a cold outreach campaign would actually send —
merge fields resolved against real leads, spintax expanded, conditionals
evaluated — and then runs deterministic checks on the *rendered output*.

The bugs that lose clients only exist after merge fields resolve: a blank
`{{firstName}}`, an AI-research column that returned "I couldn't find
information" sitting mid-sentence, a LinkedIn note three characters over the
limit. No campaign UI shows you this. `lastlook` does.

Name: the trading term — the final chance to reject before execution.

## Why this project

It is the one candidate Hernán dogfoods by default. Client campaign audits
already happen on a real cadence; once the private `campaign-preflight` skill
imports this package instead of carrying its own copy, every client audit is a
production exercise of the public code. Maintenance stops being a chore to
remember and becomes something that breaks loudly during work already underway.

## Scope

### In v1

- Three-stage pipeline, each stage usable alone.
- Adapters for Instantly (email) and HeyReach (LinkedIn), plus a `--from-csv`
  path for people on unsupported platforms.
- A documented, versioned JSON Schema for the normalized campaign shape.
- The check catalog as a registry: enable/disable per rule, config via
  `lastlook.toml`.
- Merge-tag coverage report (fill rate per variable across the lead list).
- Fleet mode: run across many campaigns from a manifest, ranked worst-first.
- Tests. The current private code has none; for an adoption-track repo that is
  the real gap.

### Explicitly not in v1

No web UI. No hosted service. No GitHub Action. No LLM-based checks.
Deterministic rules over rendered text is the entire product.

## Architecture

### Pipeline

```
lastlook pull instantly --campaign "Q3 ACME" --key $K  -> campaign.json
lastlook render campaign.json                          -> rendered.jsonl
lastlook check rendered.jsonl                          -> verdict + findings.csv

lastlook audit instantly --campaign "Q3 ACME" --key $K  # all three
lastlook coverage campaign.json                         # merge-tag fill rates
lastlook fleet --manifest m.json                        # many campaigns, ranked
lastlook rules                                          # print the check catalog
```

### The normalized campaign JSON is the keystone

Every adapter emits the same shape. `render` and `check` never learn which
platform they are auditing. Adding Lemlist or Smartlead means writing one
adapter and touching nothing else — that is the contributor contract, so the
shape ships as a versioned JSON Schema in the repo, not as tribal knowledge.

### Units and boundaries

| Unit | Does | Depends on |
|---|---|---|
| `adapters/*` | Fetch a live campaign, emit normalized JSON | Platform HTTP API |
| `schema/` | Versioned JSON Schema + validator | nothing |
| `render` | Normalized JSON -> one rendered row per (step, variant, lead) | schema |
| `rules/` | Registry of checks; each is `(row \| campaign) -> findings` | nothing |
| `check` | Run the registry, dedupe to distinct issues, emit verdict + CSV | rules |
| `coverage` | Per-variable fill rate across leads | render's resolution logic |
| `fleet` | Execute a manifest of campaigns, aggregate, rank | adapters, render, check |
| `cli` | Argument parsing, config loading, output formatting | all of the above |

`render` and `coverage` share resolution logic so that "resolved" in a coverage
report means exactly what the renderer would substitute. That shared logic lives
in `render`, imported by `coverage` — not duplicated.

### Rules registry

Today the private code hardcodes `PER_ROW_CHECKS = [...]`. In `lastlook` each
check is decorator-registered with an id, default severity, and a one-line
description:

```python
@rule("blank_merge", severity="blocker",
      help="A merge field resolved to nothing, leaving a gap in the sentence.")
def blank_merge(row): ...
```

`lastlook rules` prints the catalog. `--disable spam --enable emdash` and
`lastlook.toml` control which run. Two registries, because they see different
things:

- **Per-row rules** see one rendered message: blank merge, dangling
  punctuation, unresolved variable, unknown syntax, AI-research boilerplate,
  em dash, casing, length, link on first touch, double punctuation, full-name
  greeting, spam words.
- **Cross-campaign rules** see everything at once: signal collision, name
  quality, company quality, undefined tags, broken step handoffs, link health.

Hernán's em-dash ban ships as a real rule, **default off**. It is a house style,
not a universal truth, and shipping it on by default would be presumptuous.

### Findings are deduped to distinct issues

A spam word baked into a template produces an identical finding for every lead.
That is one issue affecting N leads, not N issues. The verdict counts distinct
issues with a leads-affected column; the CSV keeps the per-lead rows so they can
be handed to an enrichment tool to fix.

## Public / private boundary

**Public (`lastlook`):** renderer, rule registry and all rules, both adapters,
coverage, fleet executor, CLI, schema.

**Private (`~/.claude/skills/campaign-preflight`):** `SKILL.md` workflow, the
Airtable per-client key lookup, the manifest builder for fleet mode, a
`hernan.toml` preset (em dash on, house thresholds), and the Clay handoff for
fixing flagged rows.

Nothing in the adapters is secret. Only the keys are, and those are already
flags and environment variables.

## Testing

- **Renderer:** table-driven cases per merge-syntax feature — spintax
  determinism across reruns, `{{RANDOM|a|b}}` resolving before the variable
  pass, conditionals with and without `{{else}}`, inline-fallback precedence
  over the fallback map, keys containing internal spaces.
- **Rules:** every rule gets a fixture that triggers it *and* a near-miss that
  must not. False positives are what kill a linter — a tool that cries wolf on a
  clean campaign gets uninstalled.
- **Adapters:** recorded HTTP response fixtures. No live keys in CI.
- **Schema:** every fixture campaign validates against the published schema, so
  the contributor contract cannot silently drift.

## Error handling

Fail loudly and specifically, never silently degrade:

- Zero leads pulled: stop with a clear message. Rendering nothing and reporting
  CLEAR is the worst possible outcome — it reads as a pass.
- Adapter auth failure: report the platform's own error, do not swallow it.
- Unknown merge syntax encountered by the renderer: a finding, not a crash and
  not silence.
- Malformed normalized JSON: validate against the schema up front and say which
  field is wrong.

Exit codes: `0` clear, `1` warnings only, `2` blockers, `3` tool error. This
makes the tool usable in a pre-send gate even though shipping an actual CI
action is out of scope for v1.

## Open items

- `lastlook` is free on PyPI as of 2026-07-29 (`verified via` PyPI JSON API,
  404). Claim the name before announcing.
- License not chosen. MIT unless there is a reason otherwise.
