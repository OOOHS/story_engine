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

SOCIAL_RESPONSE_PATTERNS = {
    "acknowledge": ("承认", "确认", "认可", "acknowledge", "admit"),
    "apologize": ("道歉", "赔罪", "致歉", "apolog", "sorry"),
    "accuse": ("指责", "控告", "归咎", "accuse", "blame"),
    "explain": ("解释", "澄清", "说明", "explain", "clarify"),
    "forgive": ("原谅", "宽恕", "forgiv"),
    "request": ("请求", "恳求", "拜托", "request", "plead"),
}


def infer_social_response_kinds(value):
    text = str(value or "").casefold()
    return tuple(
        kind
        for kind in SOCIAL_RESPONSE_KINDS
        if any(token.casefold() in text for token in SOCIAL_RESPONSE_PATTERNS[kind])
    )


def resolve_social_response_kind(value, suggested="report"):
    inferred = infer_social_response_kinds(value)
    normalized = str(suggested or "report").strip().casefold()
    if normalized in inferred:
        return normalized
    if inferred:
        return inferred[0]
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
