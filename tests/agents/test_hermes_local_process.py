import json
import sys

from src.story_engine.agents import (
    AgentPerception,
    HermesCharacterAgent,
    HermesLocalProcessConfig,
    HermesLocalProcessConversation,
    SubjectLedgerProjector,
    default_hermes_runtime_factories,
)
from src.story_engine.agents.hermes_container import (
    subject_home_for,
    subject_session_id,
)
from src.story_engine.environment.step_checkpoint import RunnerStepCheckpoint
from src.story_engine.prefabs.templates import create_agent


def test_local_process_uses_same_actor_bound_protocol(tmp_path):
    entrypoint = tmp_path / "fake_hermes.py"
    entrypoint.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        "print('===STORY_AGENT_JSON_BEGIN===')\n"
        "print(json.dumps({\n"
        "    'protocol_version': 1,\n"
        "    'agent_id': request['agent_id'],\n"
        "    'content': json.dumps({\n"
        "        'action': {'kind': 'wait', 'detail': '等待'},\n"
        "        'home': os.environ.get('HERMES_HOME', ''),\n"
        "        'session_id': request.get('session_id', ''),\n"
        "    }),\n"
        "}))\n"
        "print('===STORY_AGENT_JSON_END===')\n",
        encoding="utf-8",
    )
    conversation = HermesLocalProcessConversation(
        "actor-1",
        HermesLocalProcessConfig(
            python_executable=sys.executable,
            entrypoint_path=str(entrypoint),
            persistent_subject=False,
            home_root=str(tmp_path / "homes"),
        ),
    )

    result = json.loads(conversation.run_conversation("{}").get("content"))
    assert result["action"]["kind"] == "wait"
    assert result["session_id"] == subject_session_id("actor-1")
    assert result["home"] == str(conversation.subject_home)
    assert conversation.subject_home == subject_home_for(
        "actor-1",
        home_root=str(tmp_path / "homes"),
    )
    assert conversation.build_command() == [sys.executable, str(entrypoint)]


def test_local_checkpoint_restores_subject_home(tmp_path):
    entrypoint = tmp_path / "fake_hermes.py"
    entrypoint.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    conversation = HermesLocalProcessConversation(
        "actor-1",
        HermesLocalProcessConfig(
            python_executable=sys.executable,
            entrypoint_path=str(entrypoint),
            persistent_subject=False,
            home_root=str(tmp_path / "homes"),
        ),
    )
    memories = conversation.subject_home / "memories"
    memories.mkdir()
    (memories / "note.txt").write_text("remembered", encoding="utf-8")
    (conversation.subject_home / "state.db").write_text("db-v1", encoding="utf-8")

    checkpoint = conversation.capture_checkpoint()
    (memories / "note.txt").write_text("changed", encoding="utf-8")
    (conversation.subject_home / "state.db").write_text("db-v2", encoding="utf-8")

    conversation.restore_checkpoint(checkpoint)

    assert (conversation.subject_home / "memories" / "note.txt").read_text(
        encoding="utf-8"
    ) == "remembered"
    assert (conversation.subject_home / "state.db").read_text(encoding="utf-8") == "db-v1"


def test_restore_clears_files_missing_from_snapshot(tmp_path):
    entrypoint = tmp_path / "fake_hermes.py"
    entrypoint.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    conversation = HermesLocalProcessConversation(
        "actor-1",
        HermesLocalProcessConfig(
            python_executable=sys.executable,
            entrypoint_path=str(entrypoint),
            persistent_subject=False,
            home_root=str(tmp_path / "homes"),
        ),
    )
    checkpoint = conversation.capture_checkpoint()
    (conversation.subject_home / "state.db").write_text("late", encoding="utf-8")
    memories = conversation.subject_home / "memories"
    memories.mkdir()
    (memories / "late.txt").write_text("nope", encoding="utf-8")

    conversation.restore_checkpoint(checkpoint)

    assert not (conversation.subject_home / "state.db").exists()
    assert not memories.exists()


def test_restore_drops_subject_created_during_step(tmp_path):
    entrypoint = tmp_path / "fake_hermes.py"
    entrypoint.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    def factory(entity, config):
        del entity, config
        return HermesLocalProcessConversation(
            "actor-1",
            HermesLocalProcessConfig(
                python_executable=sys.executable,
                entrypoint_path=str(entrypoint),
                persistent_subject=False,
                home_root=str(tmp_path / "homes"),
            ),
        )

    entity = create_agent(
        name="甲",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    runtime = HermesCharacterAgent(conversation_factory=factory)
    snapshot = runtime.capture_subject_checkpoint()
    conversation = factory(entity, {})
    runtime._conversations[entity.id] = conversation
    (conversation.subject_home / "state.db").write_text("during", encoding="utf-8")

    runtime.restore_subject_checkpoint(snapshot)

    assert entity.id not in runtime._conversations
    assert not (conversation.subject_home / "state.db").exists()


def test_ledger_projector_state_roundtrip():
    projector = SubjectLedgerProjector()
    projector._current[("claim_position", "c1")] = "digest-a"
    projector._revisions[("claim_position", "c1")] = 3
    projector._pov_last_location = "门厅"
    projector._pov_last_step = 4
    restored = SubjectLedgerProjector()
    restored.restore_state(projector.export_state())
    assert restored._current[("claim_position", "c1")] == "digest-a"
    assert restored._revisions[("claim_position", "c1")] == 3
    assert restored._pov_last_location == "门厅"
    assert restored._pov_last_step == 4


def test_hermes_runtime_restores_subject_checkpoint():
    class FakeConversation:
        def __init__(self):
            self.marker = "before"

        def capture_checkpoint(self):
            return {"marker": self.marker}

        def restore_checkpoint(self, payload):
            self.marker = payload["marker"]

        def run_subject_turn(self, packet):
            del packet
            return {
                "protocol_version": 1,
                "agent_id": entity.id,
                "content": json.dumps({"action": "等待。"}, ensure_ascii=False),
            }

    entity = create_agent(
        name="甲",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    conversation = FakeConversation()
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation,
    )
    perception = AgentPerception(actor_name="甲", step=1)
    runtime.decide(entity, perception)
    runtime._turn_counts[entity.id] = 7
    snapshot = runtime.capture_subject_checkpoint()
    runtime._turn_counts[entity.id] = 99
    conversation.marker = "after"
    runtime.restore_subject_checkpoint(snapshot)
    assert runtime._turn_counts[entity.id] == 7
    assert conversation.marker == "before"
    assert entity.id in runtime._bootstrapped


def test_step_checkpoint_restores_subject_runtime():
    class FakeRuntime:
        def __init__(self):
            self.value = 1

        def capture_subject_checkpoint(self):
            return {"value": self.value}

        def restore_subject_checkpoint(self, payload):
            self.value = payload["value"]

    entity = create_agent(
        name="甲",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    runtime = FakeRuntime()

    class FakeQueue:
        def checkpoint(self):
            return {"pending": []}

        def restore(self, payload):
            del payload

    class FakeClock:
        current_step = 3
        current_time = None

    class FakeRegistry:
        def __init__(self):
            self.runtime = runtime

        def runtime_snapshot(self):
            return {"甲": self.runtime}

        def restore_runtimes(self, snapshot, world_entities):
            del snapshot, world_entities

        def get(self, name):
            class Registered:
                runtime = self.runtime
            registered = Registered()
            registered.runtime = runtime
            return registered

        def binding_snapshot(self):
            return {}

        def restore_bindings(self, snapshot, entities):
            del snapshot, entities

    class FakeRunner:
        def __init__(self):
            self.entities = {"甲": entity}
            self.agent_registry = FakeRegistry()
            self.relation_registry = FakeRegistry()
            self.claim_registry = FakeRegistry()
            self.action_queue = FakeQueue()
            self.clock = FakeClock()

    runner = FakeRunner()
    captured = RunnerStepCheckpoint.capture(runner)
    runtime.value = 9
    captured.restore(runner)
    assert runtime.value == 1


def test_docker_factory_is_deprecated():
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        default_hermes_runtime_factories()
    assert any(item.category is DeprecationWarning for item in caught)
