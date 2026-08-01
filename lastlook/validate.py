"""validate.py — structural check on a normalized campaign, before anything runs.

`schema/campaign.schema.json` is the full published contract and the thing an
adapter author should read. This module is the runtime guard: it checks the
structure the engine actually depends on and names the offending field.

Hand-rolled rather than pulling in `jsonschema`, because the only install-time
dependency is httpx and one more package is a real cost for a CLI people try
once. The tests validate every fixture against the real schema, so the two
cannot drift silently.

The contract that matters: fail loudly and specifically. A campaign that
half-renders because a field was the wrong type produces a verdict over partial
data, and a verdict over partial data is worse than an error.
"""

import math


class CampaignError(ValueError):
    """Raised with a message naming the field that is wrong."""


def validate(campaign):
    """Raise CampaignError on anything that would make the run meaningless."""
    if not isinstance(campaign, dict):
        raise CampaignError(f"campaign must be an object, got {type(campaign).__name__}")

    for field in ("platform", "campaign", "steps", "leads"):
        if field not in campaign:
            raise CampaignError(f"missing required field {field!r}")

    if not isinstance(campaign["platform"], str) or not campaign["platform"].strip():
        raise CampaignError("'platform' must be a non-empty string")
    if not isinstance(campaign["campaign"], dict):
        raise CampaignError("'campaign' must be an object with a 'name'")
    if not isinstance(campaign["campaign"].get("name"), str):
        raise CampaignError("'campaign.name' must be a string")
    cid = campaign["campaign"].get("id")
    if cid is not None and not isinstance(cid, str):
        raise CampaignError("'campaign.id' must be a string when present")
    if not isinstance(campaign["steps"], list):
        raise CampaignError("'steps' must be a list")
    if not isinstance(campaign["leads"], list):
        raise CampaignError("'leads' must be a list")

    seen_ids = set()
    for i, step in enumerate(campaign["steps"]):
        at = f"steps[{i}]"
        if not isinstance(step, dict):
            raise CampaignError(f"{at} must be an object")
        if "step" not in step:
            raise CampaignError(f"{at} is missing 'step' (its position in the sequence)")
        if isinstance(step["step"], bool) or not isinstance(step["step"], (int, str)):
            raise CampaignError(f"{at}.step must be a string or integer")
        if "variants" not in step or not isinstance(step["variants"], list):
            raise CampaignError(f"{at}.variants must be a list")

        channel = step.get("channel", "email")
        if channel not in ("email", "connection_request", "message", "inmail"):
            raise CampaignError(f"{at}.channel has unsupported value {channel!r}")

        delay = step.get("delay_days")
        if delay is not None and (isinstance(delay, bool)
                                  or not isinstance(delay, (int, float))
                                  or not math.isfinite(delay)
                                  or delay < 0):
            raise CampaignError(
                f"{at}.delay_days must be a finite non-negative number or null, "
                f"got {delay!r}. "
                f"Null means 'the platform did not say', which disables the pacing check; "
                f"0 means 'no gap', which is a blocker. They are not the same.")

        limits = step.get("limits", {})
        if not isinstance(limits, dict):
            raise CampaignError(f"{at}.limits must be an object")
        for name, limit in limits.items():
            if not isinstance(name, str) or isinstance(limit, bool) \
                    or not isinstance(limit, int) or limit < 0:
                raise CampaignError(f"{at}.limits values must be non-negative integers")

        for j, v in enumerate(step["variants"]):
            vat = f"{at}.variants[{j}]"
            if not isinstance(v, dict):
                raise CampaignError(f"{vat} must be an object")
            if "id" not in v:
                raise CampaignError(f"{vat} is missing 'id'")
            if isinstance(v["id"], bool) or not isinstance(v["id"], (str, int)):
                raise CampaignError(f"{vat}.id must be a string or integer")
            key = (step["step"], v["id"])
            if key in seen_ids:
                raise CampaignError(
                    f"{vat}: duplicate variant id {v['id']!r} in step {step['step']}. "
                    f"Findings are keyed on (step, variant), so duplicates would merge.")
            seen_ids.add(key)
            for f in ("subject", "body"):
                if f in v and v[f] is not None and not isinstance(v[f], str):
                    raise CampaignError(f"{vat}.{f} must be a string or null")
            fb = v.get("fallbacks")
            if fb is not None and not isinstance(fb, dict):
                raise CampaignError(f"{vat}.fallbacks must be an object")
            if isinstance(fb, dict) and any(not isinstance(k, str) or not isinstance(val, str)
                                           for k, val in fb.items()):
                raise CampaignError(f"{vat}.fallbacks keys and values must be strings")
            if "disabled" in v and not isinstance(v["disabled"], bool):
                raise CampaignError(f"{vat}.disabled must be a boolean")

    for i, lead in enumerate(campaign["leads"]):
        if not isinstance(lead, dict):
            raise CampaignError(f"leads[{i}] must be an object")
        payload = lead.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise CampaignError(f"leads[{i}].payload must be an object")
        lead_id = lead.get("id")
        if lead_id is not None and (isinstance(lead_id, bool)
                                    or not isinstance(lead_id, (str, int))):
            raise CampaignError(f"leads[{i}].id must be a string or integer")
        for field in ("email", "first_name", "last_name", "company_name", "company_domain"):
            value = lead.get(field)
            if value is not None and not isinstance(value, str):
                raise CampaignError(f"leads[{i}].{field} must be a string or null")

    dv = campaign.get("defined_vars")
    if dv is not None and not isinstance(dv, list):
        raise CampaignError(
            "'defined_vars' must be a list or null. Null means the platform has no "
            "variable registry and disables the undefined-tag check; an empty list "
            "means it has one and it is empty, which flags every tag.")
    if isinstance(dv, list) and any(not isinstance(value, str) for value in dv):
        raise CampaignError("'defined_vars' entries must be strings")

    handoffs = campaign.get("handoffs", [])
    if not isinstance(handoffs, list) or any(not isinstance(item, dict) for item in handoffs):
        raise CampaignError("'handoffs' must be a list of objects")

    return campaign
