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


class CampaignError(ValueError):
    """Raised with a message naming the field that is wrong."""


def validate(campaign):
    """Raise CampaignError on anything that would make the run meaningless."""
    if not isinstance(campaign, dict):
        raise CampaignError(f"campaign must be an object, got {type(campaign).__name__}")

    for field in ("platform", "campaign", "steps", "leads"):
        if field not in campaign:
            raise CampaignError(f"missing required field {field!r}")

    if not isinstance(campaign["campaign"], dict):
        raise CampaignError("'campaign' must be an object with a 'name'")
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
        if "variants" not in step or not isinstance(step["variants"], list):
            raise CampaignError(f"{at}.variants must be a list")

        delay = step.get("delay_days")
        if delay is not None and not isinstance(delay, (int, float)):
            raise CampaignError(
                f"{at}.delay_days must be a number or null, got {type(delay).__name__}. "
                f"Null means 'the platform did not say', which disables the pacing check; "
                f"0 means 'no gap', which is a blocker. They are not the same.")

        for j, v in enumerate(step["variants"]):
            vat = f"{at}.variants[{j}]"
            if not isinstance(v, dict):
                raise CampaignError(f"{vat} must be an object")
            if "id" not in v:
                raise CampaignError(f"{vat} is missing 'id'")
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

    for i, lead in enumerate(campaign["leads"]):
        if not isinstance(lead, dict):
            raise CampaignError(f"leads[{i}] must be an object")
        payload = lead.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise CampaignError(f"leads[{i}].payload must be an object")

    dv = campaign.get("defined_vars")
    if dv is not None and not isinstance(dv, list):
        raise CampaignError(
            "'defined_vars' must be a list or null. Null means the platform has no "
            "variable registry and disables the undefined-tag check; an empty list "
            "means it has one and it is empty, which flags every tag.")

    return campaign
