"""pull_instantly.py — campaign-preflight adapter (Instantly v2).

Pulls a live Instantly campaign + its leads and emits the normalized campaign
JSON that render.py consumes. Knows the Instantly schema so nothing downstream
has to.

Instantly schema (verified against developer.instantly.ai):
  campaign.sequences[].steps[].variants[].{subject, body, v_disabled}
  lead: {email, first_name, last_name, company_name, company_domain, payload{...}}
  - payload is the custom-variables object (arbitrary keys).
  - merge tags appear as {{firstName}} or {{first_name}}; render.py normalizes both.

Auth: Authorization: Bearer <raw key>. A User-Agent header is REQUIRED
(Cloudflare blocks requests without one). Key resolution order:
  --api-key  >  $INSTANTLY_API_KEY  >  .env  >  prompt

Usage:
    python3 pull_instantly.py --campaign "Acme Q3 Outbound" --out preflight_acme.json
    python3 pull_instantly.py --campaign 0e9... --api-key "$KEY" --out out.json
"""

import argparse
import json
import os
import re
import sys

import httpx

BASE = "https://api.instantly.ai/api/v2"
UA = "lastlook/0.1 (+https://github.com/hernangimeno/lastlook)"
PAGE = 100


def client(api_key):
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": UA,
                 "Content-Type": "application/json"},
        timeout=30.0,
    )


def resolve_campaign(cx, campaign):
    """Accept either a campaign id or a name; return the full campaign object."""
    # try as id first
    r = cx.get(f"/campaigns/{campaign}")
    if r.status_code == 200:
        return r.json()
    # else search by name through the paginated list
    starting_after = None
    while True:
        params = {"limit": PAGE}
        if starting_after:
            params["starting_after"] = starting_after
        r = cx.get("/campaigns", params=params)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data if isinstance(data, list) else [])
        for c in items:
            if c.get("name", "").strip().lower() == campaign.strip().lower():
                full = cx.get(f"/campaigns/{c['id']}")
                full.raise_for_status()
                return full.json()
        starting_after = data.get("next_starting_after")
        if not starting_after or not items:
            break
    # LookupError, not sys.exit: sys.exit(str) exits 1, which the CLI documents
    # as "warnings only" — a typo'd campaign name read as a passing campaign.
    # The CLI boundary turns this into a plain sentence and exit 3.
    raise LookupError(f"no campaign matching id or name {campaign!r} on this account "
                      f"— check the name, and which account the key belongs to")


def extract_steps(campaign):
    """Flatten campaign.sequences[].steps[].variants[] into normalized steps."""
    steps = []
    seqs = campaign.get("sequences") or []
    step_no = 0
    for seq in seqs:
        for st in seq.get("steps", []):
            step_no += 1
            variants = []
            for i, v in enumerate(st.get("variants", [])):
                variants.append({
                    "id": chr(ord("A") + i),            # A, B, C... within the step
                    "subject": v.get("subject", ""),
                    "body": v.get("body", ""),
                    "disabled": bool(v.get("v_disabled", False)),
                    "fallbacks": {},                      # Instantly uses inline {{x|fb}}
                    "signal": v.get("signal"),            # only if a tag map added it
                })
            steps.append({
                "step": step_no,
                "channel": "email",
                "limits": {},                              # email has no hard caps
                "variants": variants,
                # Instantly puts `delay` on every step including the last (where
                # it does nothing). Verified live 2026-07-30 against three
                # multi-step campaigns: unit is "days" and the value is the wait BEFORE the
                # next step fires, so the gap between step N and N+1 is step N's
                # delay. Normalized to days so the checks never parse units.
                "delay_days": _delay_days(st),
            })
    return steps


def _delay_days(step):
    """Step delay in days, or None when the campaign doesn't state one."""
    raw = step.get("delay")
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    unit = (step.get("delay_unit") or "days").lower()
    if unit.startswith("hour"):
        return val / 24.0
    if unit.startswith("minute"):
        return val / 1440.0
    if unit.startswith("week"):
        return val * 7.0
    return val


def fetch_leads(cx, campaign_id, max_leads=None):
    leads, starting_after = [], None
    while True:
        body = {"campaign": campaign_id, "limit": PAGE}
        if starting_after:
            body["starting_after"] = starting_after
        r = cx.post("/leads/list", json=body)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data if isinstance(data, list) else [])
        for ld in items:
            leads.append({
                "id": ld.get("id") or ld.get("email"),
                "email": ld.get("email"),
                "first_name": ld.get("first_name"),
                "last_name": ld.get("last_name"),
                "company_name": ld.get("company_name"),
                "company_domain": ld.get("company_domain"),
                "payload": ld.get("payload") or {},        # custom variables
            })
        starting_after = data.get("next_starting_after")
        if max_leads and len(leads) >= max_leads:
            return leads[:max_leads]
        if not starting_after or not items:
            break
    return leads


def pull(api_key, campaign, max_leads=None):
    """Pull a campaign into the normalized dict. Reusable by fleet runners.

    Captures the campaign's DEFINED variable registry (core_variables +
    custom_variables) so downstream checks can tell a certain breakage (copy
    references a tag that isn't a defined variable at all — no value, no possible
    fallback) from an uncertain one (a defined variable that's just empty for
    some leads — Instantly may apply a UI-configured fallback we can't see)."""
    with client(api_key) as cx:
        c = resolve_campaign(cx, campaign)
        cid = c.get("id")
        steps = extract_steps(c)
        leads = fetch_leads(cx, cid, max_leads)
    defined = {}
    for src in (c.get("core_variables"), c.get("custom_variables")):
        if isinstance(src, dict):
            defined.update(src)
    return {
        "platform": "instantly",
        "campaign": {"id": cid, "name": c.get("name", "")},
        "steps": steps, "leads": leads, "handoffs": [],
        "defined_vars": sorted(defined.keys()),   # the campaign's known variables
    }


def main():
    ap = argparse.ArgumentParser(description="Pull an Instantly campaign into normalized JSON.")
    ap.add_argument("--campaign", required=True, help="campaign id or exact name")
    ap.add_argument("--out", required=True)
    ap.add_argument("--api-key", default=os.environ.get("INSTANTLY_API_KEY"))
    ap.add_argument("--max-leads", type=int, default=None,
                    help="sample at most N leads (for fleet scans); default all")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("ERROR: no API key. Pass --api-key or set INSTANTLY_API_KEY.")

    normalized = pull(args.api_key, args.campaign, args.max_leads)
    cid = normalized["campaign"]["id"]
    campaign = {"name": normalized["campaign"]["name"]}
    steps, leads = normalized["steps"], normalized["leads"]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    nvar = sum(len(s["variants"]) for s in steps)
    print(f"Pulled '{campaign.get('name','')}' ({cid}): {len(steps)} steps, "
          f"{nvar} variants, {len(leads)} leads -> {args.out}")
    if not leads:
        print("WARNING: 0 leads pulled — preflight needs leads to render against.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
