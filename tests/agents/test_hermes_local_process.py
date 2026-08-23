import json
import sys

from src.story_engine.agents import (
    HermesLocalProcessConfig,
    HermesLocalProcessConversation,
)


def test_local_process_uses_same_actor_bound_protocol(tmp_path):
    entrypoint = tmp_path / "fake_hermes.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        "print('===STORY_AGENT_JSON_BEGIN===')\n"
        "print(json.dumps({'protocol_version': 1, 'agent_id': request['agent_id'], 'content': json.dumps({'action': {'kind': 'wait', 'detail': '等待'}})}))\n"
        "print('===STORY_AGENT_JSON_END===')\n",
        encoding="utf-8",
    )
    conversation = HermesLocalProcessConversation(
        "actor-1",
        HermesLocalProcessConfig(
            python_executable=sys.executable,
            entrypoint_path=str(entrypoint),
            persistent_subject=False,
        ),
    )

    result = conversation.run_conversation("{}").get("content")
    assert json.loads(result)["action"]["kind"] == "wait"
    assert conversation.build_command() == [sys.executable, str(entrypoint)]
