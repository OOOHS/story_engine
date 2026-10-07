import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.story_engine.agents import HermesCharacterAgent, default_offline_runtime_factories
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.session import create_session_from_seed, load_session
from src.story_engine.web.adapter import WebGameAdapter


def make_session():
    return create_session_from_seed("地点：房间 -> 走廊\n角色：甲 | 来客 | 谨慎 | 查看房间 | 玩家 | 地点=房间\n角色：乙 | 看守 | 安静 | 等待 | 地点=房间", profile="offline", random_seed=123)


def scene(session):
    return session.entities["WorldHost"].get_component("SceneState")


def test_save_reloads_world_identity_queue_memory_and_planner(tmp_path):
    session = make_session()
    session.run_step(overrides={"甲": "等待"})
    scene(session).update_scene_flags({"consumed_storylets": ["already"], "dynamic_storylets": [{"storylet_id": "new", "intent": "钟响", "location": "房间"}]})
    newcomer = create_agent(name="丙", role="后来人", personality="安静", goals=[],
                            agent_runtime="offline", memory_namespace=session.runner.memory_namespace)
    session.runner.add_entity(newcomer)
    scene(session).update_actor_state("丙", {"location": "房间"})
    session.runner.register_agent(newcomer)
    memory = session.entities["WorldHost"].get_component("Memory")
    memory.add_memory("独一条记录", memory_id="save-record")
    from src.story_engine.components.story_planner import StoryPlanner
    session.entities["WorldHost"].add_component(StoryPlanner(scenario=session.scenario))
    planner = session.entities["WorldHost"].get_component("StoryPlanner")
    planner.conversation.append({"role": "user", "content": "我想找钥匙"})
    session.runner.action_queue.schedule({"actor": "甲", "action": {"kind": "move", "detail": "走向走廊", "target": "走廊"}, "location": "房间"})
    pending = session.pending_action("甲")
    expected = scene(session).model_dump()
    ids = {n: e.id for n, e in session.entities.items()}
    path = session.save(tmp_path / "world.storysave")
    session.close()
    restored = load_session(path, agent_runtime_factories=default_offline_runtime_factories())
    try:
        assert scene(restored).model_dump() == expected
        assert {n: e.id for n, e in restored.entities.items()} == ids
        assert restored.pending_action("甲") == pending
        assert restored.step_count == 1
        assert restored.runner.agent_registry.is_registered("丙")
        assert "丙" not in [c.name for c in restored.scenario.characters]
        assert restored.random_seed == 123
        assert restored.entities["WorldHost"].get_component("StoryPlanner").conversation == planner.conversation
        assert any(m["id"] == "save-record" for m in restored.entities["WorldHost"].get_component("Memory").list_memories())
        context = restored.run_step()
        assert context["step_committed"]
    finally:
        restored.close()


def test_save_resumes_in_another_process(tmp_path):
    session = make_session()
    session.run_step(overrides={"甲": "等待"})
    path = session.save(tmp_path / "world.storysave")
    session.close()
    program = """import sys
from src.story_engine.session import load_session
s = load_session(sys.argv[1])
assert s.step_count == 1
assert s.run_step(overrides={'甲': '等待'})['step_committed']
s.close()
"""
    result = subprocess.run([sys.executable, "-c", program, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class NativeConversation:
    def __init__(self, home):
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.restored = False

    def capture_checkpoint(self):
        return {"checkpoint_dir": str(self.home)}

    def restore_checkpoint(self, payload):
        self.restored = True
        (self.home / "conversation.json").write_bytes((Path(payload["checkpoint_dir"]) / "conversation.json").read_bytes())

    def close(self):
        pass


def test_native_subject_is_recreated_with_same_context_and_no_bootstrap(tmp_path):
    session = make_session()
    old = NativeConversation(tmp_path / "old")
    old.home.joinpath("conversation.json").write_text('{"history":["角色自己的长期计划"]}')
    entity = session.entities["乙"]
    entity.get_component("AgentController").runtime = "hermes"
    runtime = HermesCharacterAgent(lambda e, cfg: old)
    runtime._conversations[entity.id] = old
    runtime._bootstrapped.add(entity.id)
    runtime._turn_counts[entity.id] = 7
    session.runner.agent_registry.register(entity, runtime)
    path = session.save(tmp_path / "world.storysave")
    session.close()
    created = []
    def factory(entity, cfg):
        def conversation(e, c):
            native = NativeConversation(tmp_path / "new")
            created.append(native)
            return native
        return HermesCharacterAgent(conversation)
    restored = load_session(path, agent_runtime_factories={**default_offline_runtime_factories(), "hermes": factory})
    try:
        agent = restored.runner.agent_registry.get("乙").runtime
        assert created[0].restored
        assert "角色自己的长期计划" in created[0].home.joinpath("conversation.json").read_text()
        assert entity.id in agent._bootstrapped
        assert agent._turn_counts[entity.id] == 7
    finally:
        restored.close()


def test_pending_delivery_restores_and_retries_without_repeating_world_step(tmp_path, monkeypatch):
    from src.story_engine.components.host_rule_narrative import HostRuleNarrativeRenderer
    session = make_session()
    original = HostRuleNarrativeRenderer.render
    def fail(*args):
        raise RuntimeError("renderer unavailable")
    monkeypatch.setattr(HostRuleNarrativeRenderer, "render", fail)
    result = session.run_step(overrides={"甲": "等待"})
    assert result["step_committed"] and session.delivery_pending
    path = session.save(tmp_path / "delivery.storysave")
    session.close()
    monkeypatch.setattr(HostRuleNarrativeRenderer, "render", original)
    restored = load_session(path, agent_runtime_factories=default_offline_runtime_factories())
    try:
        before = restored.runner.clock.current_step
        assert restored.delivery_pending
        assert restored.retry_delivery()["delivery_retry_status"] == "completed"
        assert restored.runner.clock.current_step == before
        assert restored.step_count == 1
    finally:
        restored.close()


def test_save_is_atomic_and_strips_provider_credentials(tmp_path):
    session = make_session()
    path = tmp_path / "world.storysave"
    control = session.entities["WorldHost"].get_component("SimulationControl")
    control.llm_config["api_key"] = "fake-test-credential"
    session.save(path)
    with ZipFile(path) as archive:
        assert "fake-test-credential" not in archive.read("session.json").decode()
    old = path.read_bytes()
    session.presentation_state = {"unserializable": object()}
    with pytest.raises(TypeError):
        session.save(path)
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]
    session.close()


def test_web_history_is_restored_with_session(tmp_path):
    session = make_session()
    adapter = WebGameAdapter(session.scenario, session=session, save_path=str(tmp_path / "web.storysave"), agent_runtime_factories=default_offline_runtime_factories())
    adapter.submit_turn("等待")
    adapter.close()
    restored = load_session(tmp_path / "web.storysave", agent_runtime_factories=default_offline_runtime_factories())
    new_adapter = WebGameAdapter(restored.scenario, session=restored, agent_runtime_factories=default_offline_runtime_factories())
    try:
        assert new_adapter._history == adapter._history
        assert new_adapter._session.step_count == 1
    finally:
        new_adapter.close()


def test_real_local_adapter_restores_native_database_and_memories(tmp_path):
    import sqlite3
    from src.story_engine.agents.hermes_container import HermesLocalProcessConfig, default_local_hermes_runtime_factories
    session = make_session()
    entity = session.entities['乙']
    entity.get_component('AgentController').runtime = 'hermes'
    entrypoint = Path(__file__).resolve().parents[2] / 'docker/hermes-story/entrypoint.py'
    def factories(root):
        return {**default_offline_runtime_factories(), **default_local_hermes_runtime_factories(
            HermesLocalProcessConfig(python_executable=sys.executable,
                                     entrypoint_path=str(entrypoint), home_root=str(root)))}
    old_factories = factories(tmp_path / 'before')
    runtime = old_factories['hermes'](entity, {})
    native = runtime._factory(entity, {})
    runtime._conversations[entity.id] = native
    runtime._bootstrapped.add(entity.id)
    with sqlite3.connect(native.subject_home / 'state.db') as database:
        database.execute('create table saved_context (message text)')
        database.execute('insert into saved_context values (?)', ('remember the hidden key',))
    (native.subject_home / 'conversation.json').write_text('{"history":["subject private plan"]}')
    (native.subject_home / 'memories').mkdir()
    (native.subject_home / 'memories' / 'MEMORY.md').write_text('private subject memory')
    session.runner.agent_registry.register(entity, runtime)
    path = session.save(tmp_path / 'native.storysave')
    session.close()
    restored = load_session(path, agent_runtime_factories=factories(tmp_path / 'after'))
    try:
        agent = restored.runner.agent_registry.get('乙').runtime
        new_native = agent._conversations[entity.id]
        assert new_native.subject_home != native.subject_home
        with sqlite3.connect(new_native.subject_home / 'state.db') as database:
            assert database.execute('select message from saved_context').fetchone() == ('remember the hidden key',)
        assert (new_native.subject_home / 'conversation.json').read_text() == '{"history":["subject private plan"]}'
        assert (new_native.subject_home / 'memories' / 'MEMORY.md').read_text() == 'private subject memory'
        assert entity.id in agent._bootstrapped
    finally:
        restored.close()


def test_unsupported_save_version_is_rejected_before_runtime_creation(tmp_path):
    path = tmp_path / 'future.storysave'
    with ZipFile(path, 'w') as archive:
        archive.writestr('session.json', json.dumps({'format_version': 999}))
    with pytest.raises(ValueError, match='version'):
        load_session(path)
