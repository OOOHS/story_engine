"""Project-owned thin shell around a vendored Hermes AIAgent runtime."""

import asyncio
import inspect
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path


BEGIN_MARKER = "===STORY_AGENT_JSON_BEGIN==="
END_MARKER = "===STORY_AGENT_JSON_END==="
VENDOR_ROOT = Path(os.getenv("HERMES_VENDOR_ROOT", "/opt/hermes-agent")).expanduser()


MAX_REQUEST_CHARS = 4_000_000
CONTROL_OPS = frozenset({"checkpoint", "restore"})

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
    op = str(request.get("op", "turn") or "turn").strip() or "turn"
    if op not in {"turn", *CONTROL_OPS}:
        raise ValueError(f"Unsupported Story Agent op: {op}")
    session_id = str(request.get("session_id", "") or "").strip()
    checkpoint_dir = str(request.get("checkpoint_dir", "") or "").strip()
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
    if op == "turn" and not prompt:
        raise ValueError("Request prompt is empty")
    toolsets = request.get("enabled_toolsets", [])
    if not isinstance(toolsets, list) or not all(
        isinstance(item, str) for item in toolsets
    ):
        raise ValueError("Request enabled_toolsets must be a string list")
    agent_id = str(request.get("agent_id", "")).strip()
    if not agent_id:
        raise ValueError("Request agent_id is empty")
    return {
        "agent_id": agent_id,
        "prompt": prompt,
        "toolsets": list(dict.fromkeys(
            item.strip() for item in toolsets if item.strip()
        )),
        "op": op,
        "session_id": session_id,
        "checkpoint_dir": checkpoint_dir,
    }


def _read_request():
    return _parse_request(sys.stdin.read(MAX_REQUEST_CHARS + 1))


def _session_id():
    return str(os.getenv("HERMES_SESSION_ID", "") or "").strip()


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
        "session_id": _session_id(),
    }
    kwargs = {
        key: value
        for key, value in candidates.items()
        if value not in (None, "")
        and (supports_kwargs or key in signature.parameters)
    }
    agent = AIAgent(**kwargs)
    agent._story_history = _resume_session(agent)
    return agent


def _resume_session(agent):
    """Load the stable session transcript so a respawned process continues."""

    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None) or _session_id()
    if db is None or not session_id:
        return list(getattr(agent, "_story_history", None) or [])
    loader = getattr(db, "get_messages_as_conversation", None)
    if not callable(loader):
        return list(getattr(agent, "_story_history", None) or [])
    try:
        restored = loader(session_id)
    except Exception:
        return []
    if not isinstance(restored, list):
        return []
    messages = [
        item
        for item in restored
        if isinstance(item, dict) and item.get("role") != "session_meta"
    ]
    agent._session_messages = list(messages)
    agent._story_history = list(messages)
    return messages


def _invoke(agent, prompt):
    history = getattr(agent, "_story_history", None)
    method = agent.run_conversation
    signature = inspect.signature(method)
    parameters = signature.parameters
    extra = {}
    if "conversation_history" in parameters and history:
        extra["conversation_history"] = list(history)
    if "user_message" in parameters:
        result = method(user_message=prompt, **extra)
    elif "message" in parameters:
        result = method(message=prompt, **extra)
    elif "prompt" in parameters:
        result = method(prompt=prompt, **extra)
    elif parameters:
        result = method(prompt)
    else:
        raise RuntimeError("Hermes run_conversation exposes no prompt parameter")
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    messages = _messages_from_result(result, agent)
    if messages is not None:
        agent._story_history = messages
        agent._session_messages = list(messages)
    return result


def _messages_from_result(result, agent):
    if isinstance(result, dict) and isinstance(result.get("messages"), list):
        return list(result["messages"])
    messages = getattr(agent, "_session_messages", None)
    if isinstance(messages, list):
        return list(messages)
    return None


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


def _hermes_home():
    raw = str(os.getenv("HERMES_HOME", "") or "").strip()
    if not raw:
        raise RuntimeError("HERMES_HOME is required for subject checkpoint operations")
    return Path(raw).expanduser()


def _close_session_db(agent):
    db = getattr(agent, "_session_db", None)
    if db is None:
        return
    conn = getattr(db, "_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    agent._session_db = None


def _flush_session(agent):
    messages = list(getattr(agent, "_story_history", None) or [])
    persist = getattr(agent, "_persist_session", None)
    if callable(persist) and messages:
        persist(list(messages), list(messages))


def _copy_memories(source, dest):
    if dest.exists():
        shutil.rmtree(dest)
    if source.is_dir():
        shutil.copytree(source, dest)


def _clear_sqlite_db(path):
    for suffix in ("", "-wal", "-shm"):
        leftover = Path(str(path) + suffix)
        if leftover.exists() or leftover.is_symlink():
            leftover.unlink()


def _checkpoint_agent(agent, checkpoint_dir):
    dest = Path(checkpoint_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    _flush_session(agent)
    messages = list(getattr(agent, "_story_history", None) or [])
    (dest / "conversation.json").write_text(
        json.dumps(messages, ensure_ascii=False),
        encoding="utf-8",
    )
    (dest / "session_id").write_text(
        str(getattr(agent, "session_id", "") or _session_id()),
        encoding="utf-8",
    )
    home = _hermes_home()
    state_db = home / "state.db"
    if state_db.is_file():
        src = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(dest / "state.db")
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    _copy_memories(home / "memories", dest / "memories")
    return {
        "op": "checkpoint",
        "ok": True,
        "session_id": str(getattr(agent, "session_id", "") or _session_id()),
        "history_len": len(messages),
    }


def _restore_agent(agent, checkpoint_dir):
    dest = Path(checkpoint_dir).expanduser()
    if not dest.is_dir():
        raise ValueError(f"checkpoint_dir is not a directory: {dest}")
    home = _hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    _close_session_db(agent)
    snapshot_db = dest / "state.db"
    live_db = home / "state.db"
    _clear_sqlite_db(live_db)
    if snapshot_db.is_file():
        shutil.copy2(snapshot_db, live_db)
    _copy_memories(dest / "memories", home / "memories")
    history_path = dest / "conversation.json"
    if history_path.is_file():
        messages = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(messages, list):
            messages = []
    else:
        messages = []
    agent._story_history = list(messages)
    agent._session_messages = list(messages)
    return {
        "op": "restore",
        "ok": True,
        "session_id": str(getattr(agent, "session_id", "") or _session_id()),
        "history_len": len(messages),
    }


def _apply_session_id(session_id):
    if session_id:
        os.environ["HERMES_SESSION_ID"] = session_id


def _handle_request(agent, parsed, *, construct):
    op = parsed["op"]
    if op == "checkpoint":
        if not parsed["checkpoint_dir"]:
            raise ValueError("checkpoint_dir is required")
        return _checkpoint_agent(agent, parsed["checkpoint_dir"])
    if op == "restore":
        if not parsed["checkpoint_dir"]:
            raise ValueError("checkpoint_dir is required")
        return _restore_agent(agent, parsed["checkpoint_dir"])
    del construct
    return _extract_content(_invoke(agent, parsed["prompt"]))


def main():
    parsed = _read_request()
    _apply_session_id(parsed["session_id"])
    agent = _construct_agent(parsed["toolsets"])
    content = _handle_request(agent, parsed, construct=_construct_agent)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    _write_response(parsed["agent_id"], content)


def serve():
    """Keep one vendor AIAgent alive for a single character subject."""

    agent = None
    bound_agent_id = ""
    bound_toolsets = None
    for raw in sys.stdin:
        if not raw.strip():
            continue
        parsed = _parse_request(raw)
        _apply_session_id(parsed["session_id"])
        if agent is None:
            bound_agent_id = parsed["agent_id"]
            bound_toolsets = tuple(parsed["toolsets"])
            agent = _construct_agent(parsed["toolsets"])
        elif parsed["agent_id"] != bound_agent_id:
            raise ValueError("Persistent Story Subject cannot change agent_id")
        elif tuple(parsed["toolsets"]) != bound_toolsets:
            raise ValueError("Persistent Story Subject cannot change enabled_toolsets")
        content = _handle_request(agent, parsed, construct=_construct_agent)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        _write_response(parsed["agent_id"], content)


if __name__ == "__main__":
    try:
        if "--subject-server" in sys.argv[1:]:
            serve()
        else:
            main()
    except Exception as exc:
        print(f"Hermes story entrypoint error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
