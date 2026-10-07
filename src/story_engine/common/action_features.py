"""Finite Host-owned semantic features used by character action policy."""

ACTION_POLICY_TAGS = frozenset({
    "access",
    "acquire",
    "aid",
    "cautious",
    "commitment",
    "conceal",
    "confront",
    "cooperate",
    "deception",
    "information",
    "patient",
    "release",
    "rest",
    "retreat",
    "risk",
    "social",
})

SOCIAL_RESPONSE_KINDS = (
    "apologize",
    "forgive",
    "accuse",
    "request",
    "explain",
    "acknowledge",
)

def resolve_social_response_kind(value, suggested="report"):
    """Validate a model-provided semantic label; prose stays opaque to the Host."""
    normalized = str(suggested or "report").strip().casefold()
    return normalized if normalized in {*SOCIAL_RESPONSE_KINDS, "report"} else "report"


def normalize_action_policy_tags(value):
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(
        str(item).strip()
        for item in value[:16]
        if isinstance(item, str)
        and str(item).strip() in ACTION_POLICY_TAGS
    ))
