"""Project-owned thin shell around a vendored Hermes AIAgent runtime."""

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path


BEGIN_MARKER = "===STORY_AGENT_JSON_BEGIN==="
END_MARKER = "===STORY_AGENT_JSON_END==="
VENDOR_ROOT = Path(os.getenv("HERMES_VENDOR_ROOT", "/opt/hermes-agent")).expanduser()


MAX_REQUEST_CHARS = 4_000_000

# Operator framing, not first-person inhabitance: the process chooses the
# assigned character's next action. Character autonomy still holds because
# only this agent may propose for that body.
SUBJECT_SYSTEM_PROMPT = (
    "You are a persistent agent assigned to one character in a "
    "state-authoritative story engine. Choose the next intentional action "
    "that character would take, given their persona, private knowledge, and "
    "the evidence in this turn. You are not the fictional person, not a "
    "narrator, and not a director of other people. Tools, memory retrieval, "
    "and this JSON protocol are your work interface; they are not facts the "
    "character knows. Do not invent world outcomes. Return only the requested "
    "character decision JSON. The response content must be exactly one JSON "
    "object with a non-empty natural-language string in the `action` field, "
    "for example {\"action\":\"检查门锁并保持安静。\"}. The `action` value "
    "is what the character proposes to do, not a structured object: do not "
    "put `kind`, `target`, or other host fields inside it. Do not return a "
    "`candidates` array, tool call, Markdown fence, explanation, or claimed "
    "world outcome. Optional `thought`, `goal_requests`, `sentiment_updates`, "
    "and `motive_refs` fields must remain concise JSON values; the Host will "
    "validate any registration and ignores unsupported mental-state claims."
)


def _parse_request(raw):
    if len(raw) > MAX_REQUEST_CHARS:
        raise ValueError("Story Agent protocol request exceeds the size limit")
    request = json.loads(raw)
    if not isinstance(request, dict) or int(request.get("protocol_version", 0)) != 1:
        raise ValueError("Unsupported Story Agent protocol request")
    subject_packet = request.get("subject_packet")
    if subject_packet is not None:
        if not isinstance(subject_packet, dict):
            raise ValueError("Request subject_packet must be an object")
        if int(subject_packet.get("subject_protocol_version", 0)) != 1:
            raise ValueError("Unsupported Story Subject protocol request")
        if str(subject_packet.get("subject_id", "")).strip() != str(
            request.get("agent_id", "")
        ).strip():
            raise ValueError("Story Subject request agent_id mismatch")
        prompt = json.dumps(subject_packet, ensure_ascii=False, separators=(",", ":"))
    else:
        prompt = str(request.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Request prompt is empty")
    toolsets = request.get("enabled_toolsets", [])
    if not isinstance(toolsets, list) or not all(
        isinstance(item, str) for item in toolsets
    ):
        raise ValueError("Request enabled_toolsets must be a string list")
    agent_id = str(request.get("agent_id", "")).strip()
    if not agent_id:
        raise ValueError("Request agent_id is empty")
    return agent_id, prompt, list(dict.fromkeys(
        item.strip() for item in toolsets if item.strip()
    ))


def _read_request():
    return _parse_request(sys.stdin.read(MAX_REQUEST_CHARS + 1))


def _construct_agent(toolsets):
    if not VENDOR_ROOT.exists():
        raise RuntimeError("Hermes vendor runtime is not present at /opt/hermes-agent")
    sys.path.insert(0, str(VENDOR_ROOT))
    from run_agent import AIAgent  # type: ignore

    signature = inspect.signature(AIAgent)
    supports_kwargs = any(
        item.kind == inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )
    base_url = os.getenv("HERMES_BASE_URL", "").strip()
    model = os.getenv("HERMES_MODEL", "").strip()
    explicit_provider = os.getenv("HERMES_PROVIDER", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    api_key = os.getenv("IKUN_API_KEY", "").strip() or openai_key
    provider = explicit_provider
    if not provider and base_url:
        provider = "custom"
    elif not provider and openai_key:
        provider = "openai"
    candidates = {
        "enabled_toolsets": toolsets,
        "quiet_mode": True,
        "ephemeral_system_prompt": SUBJECT_SYSTEM_PROMPT,
        "base_url": base_url,
        "api_key": api_key,
        "provider": provider,
        "model": model,
    }
    kwargs = {
        key: value
        for key, value in candidates.items()
        if value not in (None, "")
        and (supports_kwargs or key in signature.parameters)
    }
    return AIAgent(**kwargs)


def _invoke(agent, prompt):
    method = agent.run_conversation
    signature = inspect.signature(method)
    parameters = signature.parameters
    if "user_message" in parameters:
        result = method(user_message=prompt)
    elif "message" in parameters:
        result = method(message=prompt)
    elif "prompt" in parameters:
        result = method(prompt=prompt)
    elif parameters:
        result = method(prompt)
    else:
        raise RuntimeError("Hermes run_conversation exposes no prompt parameter")
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


def _extract_content(result):
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("content", "final_response", "response", "text"):
            if isinstance(result.get(key), str):
                return result[key]
    for key in ("content", "final_response", "response", "text"):
        value = getattr(result, key, None)
        if isinstance(value, str):
            return value
    raise RuntimeError("Hermes did not return a text final response")


def _write_response(agent_id, content):
    print(BEGIN_MARKER)
    print(json.dumps({
        "protocol_version": 1,
        "agent_id": agent_id,
        "content": content,
    }, ensure_ascii=False))
    print(END_MARKER)
    sys.stdout.flush()


def main():
    agent_id, prompt, toolsets = _read_request()
    agent = _construct_agent(toolsets)
    _write_response(agent_id, _extract_content(_invoke(agent, prompt)))


def serve():
    """Keep one vendor AIAgent alive for a single character subject."""

    agent = None
    bound_agent_id = ""
    bound_toolsets = None
    for raw in sys.stdin:
        if not raw.strip():
            continue
        agent_id, prompt, toolsets = _parse_request(raw)
        if agent is None:
            bound_agent_id = agent_id
            bound_toolsets = tuple(toolsets)
            agent = _construct_agent(toolsets)
        elif agent_id != bound_agent_id:
            raise ValueError("Persistent Story Subject cannot change agent_id")
        elif tuple(toolsets) != bound_toolsets:
            raise ValueError("Persistent Story Subject cannot change enabled_toolsets")
        _write_response(agent_id, _extract_content(_invoke(agent, prompt)))


if __name__ == "__main__":
    try:
        if "--subject-server" in sys.argv[1:]:
            serve()
        else:
            main()
    except Exception as exc:
        print(f"Hermes story entrypoint error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
