"""pull_heyreach.py — campaign-preflight adapter (HeyReach Campaign API).

Pulls a live HeyReach LinkedIn campaign + its leads and emits the SAME
normalized JSON as pull_instantly.py, so render.py / check.py are unchanged.

HeyReach schema (verified against the public Postman collection + Campaign API):
  GET  /api/public/campaign/GetCampaignSequence?campaignId=  -> sequence tree
       node.nodeType in {CONNECTION_REQUEST, MESSAGE, INMAIL, SEND_LEAD_TO_INSTANTLY, ...}
       node.payload.messages[]      (templates; one chosen at random per lead)
       node.payload.fallbackMessage (used when a variable can't be resolved)
       node.unconditionalNode / node.conditionalNode (tree edges)
  GET  /api/public/campaign/GetById  -> campaign meta incl. its lead-list id
  GET  /api/public/list/GetLeadsFromList -> leads with customFields[{name,value}]
       (the campaign-leads endpoint omits custom fields; we join via the list)

Hard limits baked in from HeyReach's own spec: connection note 300, InMail
subject 200, InMail body 1900.

Auth: X-API-KEY header. 300 req/min. Key resolution: --api-key > $HEYREACH_API_KEY.

Usage:
    python3 pull_heyreach.py --campaign 12345 --out preflight_li.json
"""

import argparse
import json
import os
import re
import sys

import httpx

BASE = "https://api.heyreach.io"
UA = "lastlook/0.1 (+https://github.com/hernangimeno/lastlook)"
PAGE = 100

# HeyReach personalizes with single-brace tags like {FIRST_NAME}, {COMPANY}.
# render.py speaks canonical {{field}}, so the adapter rewrites them here and
# maps HeyReach's standard tokens onto the lead fields this adapter emits.
# Single-brace groups containing a pipe are spintax ({a|b|c}) and left untouched.
HR_TAG_MAP = {
    "firstname": "first_name", "lastname": "last_name", "fullname": "full_name",
    "name": "first_name", "company": "company_name", "companyname": "company_name",
    "position": "position", "jobtitle": "position", "title": "position",
    "headline": "headline", "location": "location", "industry": "industry",
}
SINGLE_BRACE_RE = re.compile(r"\{([^{}]+)\}")


def normalize_merge_tags(text):
    if not text:
        return text

    def repl(m):
        inner = m.group(1)
        if "|" in inner:
            return m.group(0)  # spintax — leave for render.py
        key = re.sub(r"[^a-z0-9]", "", inner.lower())
        return "{{" + HR_TAG_MAP.get(key, inner.strip()) + "}}"

    return SINGLE_BRACE_RE.sub(repl, text)

# normalized channel + char limits per HeyReach node type
NODE_MAP = {
    "CONNECTION_REQUEST": ("connection_request", {"body": 300}),
    "MESSAGE":            ("message",            {}),
    "INMAIL":             ("inmail",             {"subject": 200, "body": 1900}),
}


def client(api_key):
    return httpx.Client(
        base_url=BASE,
        headers={"X-API-KEY": api_key, "User-Agent": UA, "Content-Type": "application/json"},
        timeout=30.0,
    )


def walk_sequence(node, step_no=0, steps=None, handoffs=None):
    """Depth-first walk of the sequence tree, emitting one normalized step per
    message-bearing node, and recording cross-platform handoffs."""
    if steps is None:
        steps, handoffs = [], []
    if not node:
        return steps, handoffs

    ntype = node.get("nodeType")
    if ntype == "SEND_LEAD_TO_INSTANTLY":
        pl = node.get("payload") or {}
        handoffs.append({"type": "SEND_LEAD_TO_INSTANTLY",
                         "targetId": pl.get("instantlyResourceId"),
                         "resourceType": pl.get("resourceType")})
    elif ntype in NODE_MAP:
        channel, limits = NODE_MAP[ntype]
        pl = node.get("payload") or {}
        step_no += 1
        fb = pl.get("fallbackMessage")
        variants = []
        if ntype == "INMAIL":
            msgs = pl.get("messages", [])
            for i, m in enumerate(msgs):
                variants.append({
                    "id": chr(ord("A") + i),
                    "subject": normalize_merge_tags((m or {}).get("subject", "")),
                    "body": normalize_merge_tags((m or {}).get("message", "")),
                    "disabled": False,
                    "fallbacks": _fb_map(fb, inmail=True),
                    "signal": None,
                })
        else:
            for i, m in enumerate(pl.get("messages", []) or [""]):
                variants.append({
                    "id": chr(ord("A") + i),
                    "subject": "",
                    "body": normalize_merge_tags(m or ""),
                    "disabled": False,
                    "fallbacks": _fb_map(fb),
                    "signal": None,
                })
        steps.append({"step": step_no, "channel": channel, "limits": limits, "variants": variants})

    # follow both branches; share the running step_no via list length
    for child_key in ("unconditionalNode", "conditionalNode"):
        child = node.get(child_key)
        if child:
            steps, handoffs = walk_sequence(child, len(steps), steps, handoffs)
    return steps, handoffs


def _fb_map(fallback, inmail=False):
    """HeyReach has one fallbackMessage per node, not per variable. We expose it
    under a wildcard key render.py falls back to; simplest faithful model is to
    treat it as the body-level fallback. Stored under '*' is not consumed by
    render.py, so instead we leave fallbacks empty and rely on UNRESOLVED_VAR to
    flag truly missing vars — matching HeyReach's actual send behavior, where a
    missing var triggers the whole fallbackMessage swap."""
    return {}


def fetch_list_leads(cx, list_id, max_leads=None):
    # GetLeadsFromList is POST with a JSON body (the docs' GET summary is wrong;
    # the live API returns 405 for GET). Verified against a live campaign.
    leads, offset = [], 0
    while True:
        r = cx.post("/api/public/list/GetLeadsFromList",
                    json={"listId": int(list_id), "offset": offset, "limit": PAGE})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("HeyReach returned a non-object lead-list response")
        items = data.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("HeyReach returned a non-list 'items' field")
        for ld in items:
            cf = {c.get("name"): c.get("value") for c in (ld.get("customFields") or [])}
            fn, ln_ = ld.get("firstName") or "", ld.get("lastName") or ""
            leads.append({
                "id": ld.get("profileUrl") or ld.get("emailAddress"),
                "email": ld.get("emailAddress"),
                "first_name": ld.get("firstName"),
                "last_name": ld.get("lastName"),
                "full_name": (fn + " " + ln_).strip() or None,
                "company_name": ld.get("companyName"),
                "headline": ld.get("headline"),
                "position": ld.get("position"),
                "location": ld.get("location"),
                "payload": cf,
            })
        offset += len(items)
        if max_leads and len(leads) >= max_leads:
            return leads[:max_leads]
        total = data.get("totalCount")
        if not items or (total is not None and offset >= total) \
                or (total is None and len(items) < PAGE):
            break
    return leads


def pull(api_key, campaign_id, max_leads=None):
    """Pull a HeyReach campaign into the normalized dict. Reusable by fleet runners."""
    with client(api_key) as cx:
        meta = cx.get("/api/public/campaign/GetById", params={"campaignId": campaign_id})
        meta.raise_for_status()
        meta = meta.json()
        seq = cx.get("/api/public/campaign/GetCampaignSequence",
                     params={"campaignId": campaign_id})
        seq.raise_for_status()
        root = seq.json() if seq.text.strip() else None
        steps, handoffs = walk_sequence(root)
        list_id = meta.get("linkedInUserListId") or meta.get("listId")
        leads = fetch_list_leads(cx, list_id, max_leads) if list_id else []
    return {
        "platform": "heyreach",
        "campaign": {"id": campaign_id, "name": meta.get("name", "")},
        "steps": steps, "leads": leads, "handoffs": handoffs,
    }


def main():
    ap = argparse.ArgumentParser(description="Pull a HeyReach campaign into normalized JSON.")
    ap.add_argument("--campaign", required=True, help="HeyReach campaignId")
    ap.add_argument("--out", required=True)
    ap.add_argument("--api-key", default=os.environ.get("HEYREACH_API_KEY"))
    ap.add_argument("--max-leads", type=int, default=None,
                    help="sample at most N leads (for fleet scans); default all")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("ERROR: no API key. Pass --api-key or set HEYREACH_API_KEY.")

    normalized = pull(args.api_key, args.campaign, args.max_leads)
    steps, leads, handoffs = normalized["steps"], normalized["leads"], normalized["handoffs"]
    meta = {"name": normalized["campaign"]["name"]}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    print(f"Pulled '{meta.get('name','')}' ({args.campaign}): {len(steps)} steps, "
          f"{len(leads)} leads, {len(handoffs)} handoff(s) -> {args.out}")
    if not leads:
        print("WARNING: 0 leads pulled — preflight needs leads to render against.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
