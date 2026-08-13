from src.story_engine.agents import AgentDecision
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.host_mutations import HostMutationTransaction
from src.story_engine.environment.runner import Runner
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


def _scene() -> SceneState:
    return SceneState(
        world_objects={
            "大厅": {"connected_to": [], "lighting": "暗"},
            "庭院": {"connected_to": []},
        },
        actor_states={"甲": {"location": "大厅"}},
        scene_flags={"world_version": 10},
    )


def test_object_and_topology_host_commands_commit_once_on_one_shared_copy():
    scene = _scene()

    outcome = HostMutationTransaction().apply(
        scene,
        world_edits=[("大厅", {"lighting": "亮"})],
        topology_changes=[
            {"operation": "connect", "source": "大厅", "target": "庭院"}
        ],
        current_step=3,
    )

    assert outcome.committed is True
    assert len(outcome.object_changes) == 1
    assert len(outcome.topology_changes) == 1
    assert scene.get_object_state("大厅")["lighting"] == "亮"
    assert scene.get_object_state("大厅")["connected_to"] == ["庭院"]
    assert scene.get_object_state("庭院")["connected_to"] == ["大厅"]
    assert scene.get_scene_flag("world_version") == 11


def test_invalid_topology_rolls_back_valid_object_edit_in_same_host_batch():
    scene = _scene()
    before = scene.model_dump()

    outcome = HostMutationTransaction().apply(
        scene,
        world_edits=[("大厅", {"lighting": "亮"})],
        topology_changes=[
            {"operation": "connect", "source": "大厅", "target": "不存在"}
        ],
        current_step=3,
    )

    assert outcome.committed is False
    assert outcome.object_changes == []
    assert outcome.topology_changes == []
    assert any("unknown target location" in error for error in outcome.errors)
    assert outcome.world_edit_errors == [
        "rolled back with rejected host mutation batch"
    ]
    assert scene.model_dump() == before


class CountingRuntime:
    def __init__(self):
        self.calls = 0

    def decide(self, entity, perception):
        self.calls += 1
        return AgentDecision(action="等待", thought="")


def test_runner_rejects_invalid_host_batch_before_agent_or_time_execution():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(scene)
    runtime = CountingRuntime()
    runner = Runner(
        random_seed="host-rejection",
        agent_runtime_factories={"test": lambda entity, config: runtime},
    )
    actor = create_agent("甲", "居民", "平静", [], agent_runtime="test")
    runner.add_entity(gm)
    runner.add_entity(actor)
    runner.register_agent(actor)
    before_queue = runner.action_queue.snapshot()

    context = runner.run_step(
        world_edits=[("大厅", {"lighting": "亮"})],
        topology_changes=[
            {"operation": "disconnect", "source": "大厅", "target": "不存在"}
        ],
        player_name="甲",
    )

    assert context["step_aborted"] is True
    assert context["step_abort_reason"] == "host_mutation_rejected"
    assert context["host_mutation_transaction"]["committed"] is False
    assert runtime.calls == 0
    assert runner.clock.current_step == 0
    assert runner.action_queue.snapshot() == before_queue
    assert scene.get_object_state("大厅")["lighting"] == "暗"
    assert not any(name.startswith("WorldEvent:") for name in runner.entities)
    assert "simulation_result" not in context


def test_aborted_session_step_does_not_increment_count_and_retry_keeps_ids():
    scenario = ScenarioConfig(
        name="宿主事务测试",
        description="验证宿主步前事务",
        environment="测试环境",
        initial_state="甲位于大厅。",
        initial_world_objects={
            "大厅": {"connected_to": [], "lighting": "暗"},
            "庭院": {"connected_to": []},
        },
        initial_actor_states={"甲": {"location": "大厅"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="居民",
                personality="平静",
                goals=[],
                is_player=True,
                agent_runtime="test",
            )
        ],
    )
    runtime = CountingRuntime()
    session = create_session(
        scenario,
        agent_runtime_factories={"test": lambda entity, config: runtime},
        random_seed="host-retry",
    )

    rejected = session.run_step(
        topology_changes=[
            {"operation": "connect", "source": "大厅", "target": "不存在"}
        ]
    )
    accepted = session.run_step(
        world_edits=[("大厅", {"lighting": "亮"})],
        topology_changes=[
            {"operation": "connect", "source": "大厅", "target": "庭院"}
        ],
    )

    assert rejected["step_aborted"] is True
    assert session.step_count == 1
    assert accepted["step_aborted"] is False
    assert accepted["host_object_state_changes"][0]["change_id"].startswith(
        "host-world-edit:0:"
    )
    assert accepted["topology_changes"][0]["change_id"].startswith(
        "0:0:connect:"
    )
    assert runtime.calls == 1
    assert "WorldEvent:object-state:host-world-edit:0:0:大厅" in session.entities
    assert "WorldEvent:topology:0:0:connect:大厅->庭院" in session.entities
