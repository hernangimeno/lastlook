"""make_benchmark.py — generate a large, realistic campaign for perf work.

The example fixtures are ~2 KB, which proves correctness but says nothing about
speed. This builds a mid-size campaign of the shape a real client actually runs
(thousands of leads, several variants, a few steps) so render and check can be
timed against a stable baseline.

Deterministic: seeded RNG, so the same arguments always produce the same file
and two timing runs are comparable.

    python3 tests/make_benchmark.py --leads 5000 --out tests/bench_campaign.json
"""

import argparse
import json
import random

FIRST = ["Jane", "Bob", "Sam", "Priya", "Wei", "Tomás", "Aisha", "Liam", "Noor", "Erik"]
LAST = ["Smith", "Okafor", "Nguyen", "Rossi", "Dubois", "Larsson", "Haddad", "Kim"]
COMPANIES = ["Acme", "Globex", "Initech", "Umbrella", "Soylent", "Vandelay",
             "Stark Industries", "Wayne Enterprises", "Cyberdyne", "Tyrell Corp"]
STAGES = ["Seed", "Series A", "Series B", "Series C", "bootstrapped"]
INDUSTRIES = ["fintech", "healthtech", "logistics", "devtools", "e-commerce"]
AI_LINES = [
    "saw your team doubled headcount last quarter",
    "noticed you just opened a second office in Berlin",
    "read your post about migrating off Salesforce",
    "spotted the new pricing page you shipped",
]

# Deliberately seeded junk, at realistic rates. A benchmark on perfectly clean
# data would let a rule that early-exits on clean input look artificially fast.
JUNK_RATE = 0.12


def build_variants():
    """Four steps, five variants on step 1, tapering after — a normal shape."""
    steps = []
    bodies = [
        ("is {{company}}'s paid spend mapping to pipeline?",
         "Hi {{firstName}},\n\nNoticed {{company}} is scaling ads. Most {{industry}} teams "
         "at {{funding_stage}} stage hit the same wall.\n\n{a|Worth a quick chat?|Open to a look?}"
         "\n\nHernan"),
        ("quick one for {{firstName}}",
         "Hey {{firstName}},\n\nI liked what you're building at {{company}}. "
         "{{ai_personalization}}.\n\n{{RANDOM | Worth 15 minutes? | Free this week?}}\n\nHernan"),
        ("following up on {{company}}",
         "{{firstName}}, {{ai_personalization}}. Thought it was worth a second note.\n\nHernan"),
        ("{{firstName}} — last one",
         "Hi {{firstName}},\n\n{{#if funding_stage}}Since you're at {{funding_stage}}, "
         "timing may be off.{{else}}Timing may be off.{{/if}}\n\nClosing the loop.\n\nHernan"),
        ("re: {{company}}",
         "Hey {{firstName}},\n\nOne more idea for {{company}} — {{ai_personalization}}."
         "\n\nHernan"),
    ]
    counts = [5, 3, 2, 1]  # variants per step
    for si, n in enumerate(counts, start=1):
        variants = []
        for vi in range(n):
            subj, body = bodies[(si + vi) % len(bodies)]
            variants.append({
                "id": f"{si}{chr(65 + vi)}",
                "signal": ["overspend", "hiring", "funding"][vi % 3],
                "subject": subj,
                "body": body,
                "fallbacks": {"funding_stage": "your", "industry": "your"},
            })
        steps.append({"step": si, "channel": "email", "limits": {}, "variants": variants})
    return steps


def build_leads(n, rng):
    leads = []
    for i in range(n):
        first = rng.choice(FIRST)
        company = rng.choice(COMPANIES)
        junk = rng.random() < JUNK_RATE
        payload = {
            "company": company,
            "industry": rng.choice(INDUSTRIES),
            "funding_stage": rng.choice(STAGES),
            "ai_personalization": rng.choice(AI_LINES),
        }
        if junk:
            # The four junk classes the checks actually exist to catch.
            kind = rng.randrange(4)
            if kind == 0:
                payload["company"] = ""                       # blank merge
            elif kind == 1:
                payload["ai_personalization"] = "I couldn't find information about this company"
            elif kind == 2:
                first = first.upper()                          # shouting name
            else:
                payload["company"] = company.upper() + " Inc." # casing + legal suffix
        leads.append({
            "id": f"L{i}",
            "email": f"{first.lower()}.{rng.choice(LAST).lower()}{i}@{company.split()[0].lower()}.com",
            "first_name": first,
            "company_name": payload["company"],
            "payload": payload,
        })
    return leads


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leads", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", default="tests/bench_campaign.json")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    steps = build_variants()
    campaign = {
        "platform": "fixture",
        "campaign": {"id": "bench-001", "name": "Benchmark — mid-size campaign"},
        "steps": steps,
        "leads": build_leads(args.leads, rng),
        "handoffs": [],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(campaign, f, ensure_ascii=False)

    nvar = sum(len(s["variants"]) for s in steps)
    print(f"{args.out}: {len(steps)} steps, {nvar} variants, {args.leads} leads "
          f"-> {nvar * args.leads} messages to render")


if __name__ == "__main__":
    main()
