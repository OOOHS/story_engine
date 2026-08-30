from types import SimpleNamespace

import pytest

from src.story_engine.attention import HostAttentionPolicy
from src.story_engine.agents import (
    AgentDecision,
    AgentPerception,
    AgentRegistry,
    AgentScheduler,
)
from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.memory_context import (
    AgentMemoryContextBuilder,
    MemoryQueryRoute,
)
from src.story_engine.agents.memory_consolidation import MemoryConsolidator
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.identity import Identity
from src.story_engine.components.memory import Memory as VectorMemory
from src.story_engine.components.observation import Observation
from src.story_engine.components.planning import Planning
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.goal_state import GoalRecord, GoalState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.runner import Runner
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.cognition import CognitionSystem

class _WaitingRuntime:
    """A registered runtime that always commits to the same trivial action."""

    def decide(self, _entity, _perception):
        return AgentDecision(action="安静等待。")


_LLM_RUNTIME_FACTORIES = {"llm": lambda entity, cfg: _WaitingRuntime()}


class RecordingRuntime:
    def __init__(self, action="提出自己的行动。", metadata=None):
        self.action = action
        self.metadata = metadata or {}
        self.perceptions = []

    def decide(self, entity, perception):
        self.perceptions.append((entity, perception))
        return AgentDecision(
            action=self.action,
            thought="按自己的目标行动。",
            metadata=self.metadata,
        )


def _character(name: str, runtime: str = "test") -> Entity:
    entity = Entity(name)
    entity.add_component(
        Identity(
            name=name,
            role="测试角色",
            personality="谨慎",
            goals=["保护自己的利益"],
        )
    )
    entity.add_component(Observation())
    entity.add_component(Planning())
    entity.add_component(Cognition())
    entity.add_component(AgentController(runtime=runtime))
    return entity


def test_runner_registers_character_through_runtime_factory():
    runtime = RecordingRuntime()
    runner = Runner(agent_runtime_factories={"test": lambda entity, config: runtime})
    character = _character("甲")

    runner.add_entity(character)
    runner.register_agent(character)

    assert runner.agent_registry.is_registered(character)
    assert len(runner.agent_registry) == 1


def test_input_uses_registered_agent_and_gives_it_pov_bounded_perception():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(action="向同处一室的人询问发生了什么。")
    registry = AgentRegistry()
    character = _character("甲")
    registry.register(character, runtime)

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}, "远处": {}},
            actor_states={
                "甲": {
                    "location": "房间",
                    "capabilities": ["lockpicking"],
                    "fear": 0.4,
                    "dramatic_push": 9,
                    "signature_templates": ["force_scene"],
                },
                "乙": {
                    "location": "房间",
                    "stance": "seated",
                    "bias": "甲",
                    "capabilities": ["mind_reading"],
                    "secret": "不应泄露",
                },
                "丙": {"location": "远处", "secret": "不应泄露"},
            },
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": None,
        "player_name": None,
        "inject_events": [],
        "intents": [
            {"actor": "乙", "intent": "敲了敲桌子。", "location": "房间", "source": "ai"},
            {"actor": "丙", "intent": "打开密门。", "location": "远处", "source": "ai"},
        ],
    }

    InputSystem().update({"GameMaster": gm, "甲": character}, context)

    perception = runtime.perceptions[0][1]
    assert isinstance(perception, AgentPerception)
    assert [item["actor"] for item in perception.visible_proposals] == ["乙"]
    assert "丙" not in perception.world_view.get("visible_actor_states", {})
    visible_乙 = perception.world_view["visible_actor_states"]["乙"]
    assert visible_乙 == {"location": "房间", "stance": "seated"}
    assert perception.self_state["capabilities"] == ["lockpicking"]
    assert perception.self_state["fear"] == 0.4
    assert "dramatic_push" not in perception.self_state
    assert "signature_templates" not in perception.self_state
    assert context["intents"][-1]["actor"] == "甲"
    controller = character.get_component("AgentController")
    assert controller.decision_count == 1
    assert controller.last_decision_step == 0


def test_input_never_falls_back_to_an_unregistered_character_brain():
    class SimulationControl(Component):
        pass

    character = _character("甲")
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={"甲": {"location": "房间"}},
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": AgentRegistry(),
        "overrides": {},
        "clock": None,
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "甲": character}, context)

    assert context["intents"] == []
    assert context["agent_registration_errors"] == ["甲"]
    assert context["agent_activations"]["甲"] == {
        "active": False,
        "scope": "foreground",
        "reason": "missing_agent_runtime",
    }


def test_registry_rejects_runtime_without_agent_controller():
    registry = AgentRegistry()
    entity = Entity("普通实体")

    with pytest.raises(
        ValueError,
        match="Cannot register entity without AgentController: 普通实体",
    ):
        registry.register(entity, RecordingRuntime("不应执行"))

    assert not registry.is_registered(entity)


def test_runner_enforces_agent_boundary_without_scenario_loader():
    runner = Runner()
    gm = Entity("GameMaster")
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={"未注册角色": {"location": "房间"}},
        )
    )
    runner.add_entity(gm)

    context = runner.run_step()

    assert context["step_aborted"] is True
    assert context["step_abort_reason"] == "invalid_agent_boundary"
    assert context["agent_boundary_errors"] == [
        "actor has no ECS Entity:未注册角色"
    ]


def test_registry_replaces_same_named_agent_without_leaking_old_runtime():
    registry = AgentRegistry()
    old = _character("甲")
    new = _character("甲")
    registry.register(old, RecordingRuntime("旧动作"))
    registry.register(new, RecordingRuntime("新动作"))

    assert not registry.is_registered(old)
    assert registry.is_registered(new)
    assert len(registry) == 1


def test_scenario_loader_registers_every_declared_character_as_agent():
    scenario = ScenarioConfig(
        name="最小种子",
        default_agent_runtime="llm",
        description="两个角色在空房间里相遇。",
        environment="空房间",
        initial_state="甲与乙第一次见面。",
        initial_world_objects={"房间": {"kind": "room"}},
        initial_actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
        characters=[
            CharacterConfig(name="甲", role="访客", personality="好奇", goals=["了解乙"], agent_runtime="llm"),
            CharacterConfig(name="乙", role="主人", personality="谨慎", goals=["保护秘密"], agent_runtime="llm"),
        ],
    )

    session = create_session(scenario, agent_runtime_factories=_LLM_RUNTIME_FACTORIES)

    assert len(session.runner.agent_registry) == 2
    assert session.runner.agent_registry.is_registered("甲")
    assert session.runner.agent_registry.is_registered("乙")
    assert all(
        session.entities[name].get_component("AgentController")
        for name in ["甲", "乙"]
    )


def test_scenario_loader_rejects_actor_bodies_without_character_agents():
    scenario = ScenarioConfig(
        name="孤儿身体",
        default_agent_runtime="llm",
        description="场景声明了一个没有 Agent 的人物身体。",
        environment="空房间",
        initial_state="甲站在房间里。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "房间"}},
        characters=[],
    )

    with pytest.raises(
        ValueError,
        match="initial actor states without characters",
    ):
        create_session(scenario)


def test_scenario_loader_rejects_character_agents_without_world_bodies():
    scenario = ScenarioConfig(
        name="幽灵 Agent",
        default_agent_runtime="llm",
        description="角色有 Agent 配置但没有世界身体。",
        environment="空房间",
        initial_state="甲尚未被放入世界。",
        initial_world_objects={"房间": {}},
        initial_actor_states={},
        characters=[
            CharacterConfig(name="甲", role="访客", personality="好奇", goals=[], agent_runtime="llm")
        ],
    )

    with pytest.raises(
        ValueError,
        match="characters without initial actor state",
    ):
        create_session(scenario)


def test_scenario_loader_rejects_actor_body_outside_the_authored_world():
    scenario = ScenarioConfig(
        name="世界外身体",
        default_agent_runtime="llm",
        description="角色身体引用了不存在的地点。",
        environment="空房间",
        initial_state="甲的位置无效。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "不存在的走廊"}},
        characters=[
            CharacterConfig(name="甲", role="访客", personality="好奇", goals=[], agent_runtime="llm")
        ],
    )

    with pytest.raises(
        ValueError,
        match="initial actor state has unknown location",
    ):
        create_session(scenario)


def test_full_session_fails_closed_if_a_live_runtime_binding_disappears():
    scenario = ScenarioConfig(
        name="运行时边界",
        default_agent_runtime="llm",
        description="一个普通角色所在的房间。",
        environment="房间",
        initial_state="甲正在等待。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "房间"}},
        characters=[
            CharacterConfig(name="甲", role="居民", personality="平静", goals=[], agent_runtime="llm")
        ],
    )
    session = create_session(scenario, agent_runtime_factories=_LLM_RUNTIME_FACTORIES)
    before_time = session.simulation_time
    session.runner.unregister_agent(session.entities["甲"])

    context = session.run_step()

    assert context["step_aborted"] is True
    assert context["step_abort_reason"] == "invalid_agent_boundary"
    assert context["agent_boundary_errors"] == ["actor has no live runtime:甲"]
    assert session.simulation_time == before_time


def test_scheduler_runs_offscreen_agents_on_staggered_background_ticks():
    scheduler = AgentScheduler()
    character = _character("远方角色")
    controller = character.get_component("AgentController")
    controller.background_interval = 4
    due_steps = [
        step
        for step in range(8)
        if scheduler.activation_for(
            character,
            step=step,
            actor_location="港口",
            player_location="旅馆",
            proposals=[],
            is_player=False,
            has_manual_override=False,
        ).active
    ]

    assert len(due_steps) == 2
    assert due_steps[1] - due_steps[0] == 4


def test_scheduler_wakes_offscreen_agent_for_local_world_signal():
    scheduler = AgentScheduler()
    character = _character("守门人")
    character.get_component("AgentController").background_interval = 99

    activation = scheduler.activation_for(
        character,
        step=1,
        actor_location="城门",
        player_location="王宫",
        proposals=[
            {
                "actor": "World",
                "intent": "城门外传来撞击声。",
                "location": "城门",
                "source": "timeline",
            }
        ],
        is_player=False,
        has_manual_override=False,
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "local_world_signal"


def test_scheduler_wakes_offscreen_agent_once_for_pending_world_event():
    scheduler = AgentScheduler()
    character = _character("远方当事人")
    character.get_component("AgentController").background_interval = 99
    cognition = character.get_component("Cognition")
    cognition.record_world_event(
        event_id="obligation:远方当事人:delivery:breached",
        statement="远方当事人的交付义务已经违约。",
        step=4,
        location="港口",
        witness_mode="self",
    )

    activation = scheduler.activation_for(
        character,
        step=5,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "world_event:obligation:远方当事人:delivery:breached"


def test_scheduler_wakes_offscreen_agent_when_private_need_becomes_critical():
    scheduler = AgentScheduler()
    character = _character("饥饿旅人")
    character.add_component(
        DriveState.from_initial(
            [
                {
                    "name": "hunger",
                    "pressure": 0.9,
                    "critical_threshold": 0.8,
                }
            ]
        )
    )
    character.get_component("AgentController").background_interval = 99

    activation = scheduler.activation_for(
        character,
        step=1,
        actor_location="荒野",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "critical_need:hunger"


def test_verifiable_agent_goal_gets_bounded_background_continuation():
    scheduler = AgentScheduler()
    character = _character("持续行动者")
    character.get_component("AgentController").background_interval = 99
    goals = GoalState()
    transition, error = goals.adopt_agent_goal(
        title="前往远处完成后续行动",
        description="事件产生了一个需要多步推进的目标",
        source_kind="world_event",
        source_ref="event:seed",
        priority=0.7,
        step=0,
        completion_conditions=[
            {
                "scope": "actor",
                "target": "持续行动者",
                "path": "location",
                "operator": "eq",
                "value": "远处",
            }
        ],
    )
    assert transition and not error
    goals.goals["open-review"] = GoalRecord(
        goal_id="open-review",
        title="更高优先级但尚无办法的开放目标",
        priority=0.99,
        origin="agent",
        source_kind="world_event",
        source_ref="event:open",
        created_step=0,
    )
    character.add_component(goals)
    scene = SceneState(scene_flags={"agent_goal_wakeup_interval": 2})

    early = scheduler.activation_for(
        character,
        step=1,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )
    due = scheduler.activation_for(
        character,
        step=2,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )

    goal_id = transition["goal_id"]
    assert early.reason != f"agent_goal:{goal_id}"
    assert due.active is True
    assert due.reason == f"agent_goal:{goal_id}"

    controller = character.get_component("AgentController")
    controller.last_goal_wakeup_step = 2
    controller.last_goal_wakeup_id = goal_id
    assert scheduler.activation_for(
        character,
        step=3,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason != f"agent_goal:{goal_id}"
    assert scheduler.activation_for(
        character,
        step=4,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason == f"agent_goal:{goal_id}"

    controller.last_goal_wakeup_step = 4
    controller.repeated_goal_action_count = 2
    controller.last_goal_action_signature = "move|远处"
    assert scheduler.activation_for(
        character,
        step=6,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason != f"agent_goal:{goal_id}"
    assert scheduler.activation_for(
        character,
        step=8,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason == f"agent_goal:{goal_id}"


def test_open_agent_goal_gets_slow_bounded_review_but_authored_goal_does_not():
    scheduler = AgentScheduler()
    character = _character("低频角色")
    character.get_component("AgentController").background_interval = 99
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "authored",
                "title": "作者给出的长期目标",
                "completion_conditions": [
                    {"scope": "scene", "path": "done", "operator": "eq", "value": True}
                ],
            }
        ]
    )
    goals.adopt_agent_goal(
        title="无法验证的开放愿望",
        description="只用于角色策略",
        source_kind="world_event",
        source_ref="event:open",
        priority=0.6,
        step=0,
    )
    character.add_component(goals)

    scene = SceneState(
        scene_flags={
            "agent_goal_wakeup_interval": 1,
            "agent_open_goal_review_interval": 12,
        }
    )
    early = scheduler.activation_for(
        character,
        step=11,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )
    due = scheduler.activation_for(
        character,
        step=12,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )

    open_goal = next(
        record for record in goals.goals.values() if record.origin == "agent"
    )
    assert early.reason != f"agent_goal:{open_goal.goal_id}"
    assert due.reason == f"agent_goal:{open_goal.goal_id}"

    open_goal.status = "abandoned"
    authored_only = scheduler.activation_for(
        character,
        step=24,
        actor_location="港口",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )
    assert not authored_only.reason.startswith("agent_goal:")


def test_input_records_successful_goal_continuation_wakeup():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(action="继续前往远处。")
    registry = AgentRegistry()
    actor = _character("行动者")
    actor.get_component("AgentController").background_interval = 99
    goals = GoalState()
    transition, _ = goals.adopt_agent_goal(
        title="前往远处",
        description="继续事件引出的行动",
        source_kind="world_event",
        source_ref="event:travel",
        priority=0.7,
        step=0,
        completion_conditions=[
            {
                "scope": "actor",
                "target": "行动者",
                "path": "location",
                "operator": "eq",
                "value": "远处",
            }
        ],
    )
    actor.add_component(goals)
    registry.register(actor, runtime)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"王宫": {}, "港口": {}, "远处": {}},
            actor_states={"玩家": {"location": "王宫"}, "行动者": {"location": "港口"}},
            scene_flags={"agent_goal_wakeup_interval": 2},
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": "玩家",
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "行动者": actor}, context)

    controller = actor.get_component("AgentController")
    assert context["agent_activations"]["行动者"]["reason"] == (
        f"agent_goal:{transition['goal_id']}"
    )
    assert controller.last_goal_wakeup_step == 2
    assert controller.last_goal_wakeup_id == transition["goal_id"]
    assert controller.goal_continuation_attempts == 1
    assert controller.repeated_goal_action_count == 1
    assert controller.last_goal_action_signature == "move|"


def test_dormant_agent_only_wakes_for_manual_override():
    scheduler = AgentScheduler()
    character = _character("沉睡者")
    character.get_component("AgentController").activation_policy = "dormant"
    character.get_component("Cognition").pending_world_events = ["storm"]
    character.get_component("Cognition").pending_event_responses = ["apology"]

    automatic = scheduler.activation_for(
        character,
        step=0,
        actor_location="洞穴",
        player_location="村庄",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )
    manual = scheduler.activation_for(
        character,
        step=0,
        actor_location="洞穴",
        player_location="村庄",
        proposals=[],
        is_player=False,
        has_manual_override=True,
    )

    assert automatic.active is False
    assert manual.active is True
    assert manual.reason == "manual_override"


def test_input_collects_offscreen_background_proposal_when_local_event_arrives():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(action="关上城门并叫醒守卫。")
    registry = AgentRegistry()
    guard = _character("守门人")
    guard.get_component("AgentController").background_interval = 99
    registry.register(guard, runtime)

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"王宫": {}, "城门": {}},
            actor_states={
                "玩家": {"location": "王宫"},
                "守门人": {"location": "城门"},
            },
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": None,
        "player_name": "玩家",
        "inject_events": [],
        "intents": [
            {
                "actor": "World",
                "intent": "城门外传来撞击声。",
                "location": "城门",
                "source": "timeline",
            }
        ],
    }

    InputSystem().update({"GameMaster": gm, "守门人": guard}, context)

    proposal = context["intents"][-1]
    assert proposal["actor"] == "守门人"
    assert proposal["activation_scope"] == "background"
    assert proposal["proposal_role"] == "background_character_proposal"
    assert proposal["proposal_priority"] < 0.4
    assert runtime.perceptions[0][1].activation_scope == "background"


def test_input_delivers_and_acknowledges_pending_world_event_attention():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(action="重新考虑已经违约的交付安排。")
    registry = AgentRegistry()
    actor = _character("送货人")
    actor.get_component("AgentController").background_interval = 99
    cognition = actor.get_component("Cognition")
    event_id = "obligation:送货人:delivery:breached"
    cognition.record_world_event(
        event_id=event_id,
        statement="送货人的交付义务已经违约。",
        step=4,
        location="港口",
        witness_mode="self",
    )
    registry.register(actor, runtime)

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"王宫": {}, "港口": {}},
            actor_states={
                "玩家": {"location": "王宫"},
                "送货人": {"location": "港口"},
            },
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": SimpleNamespace(current_step=7),
        "player_name": "玩家",
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "送货人": actor}, context)

    assert context["agent_activations"]["送货人"]["reason"] == f"world_event:{event_id}"
    assert runtime.perceptions[0][1].private_cognition[
        "pending_world_events"
    ] == [event_id]
    assert runtime.perceptions[0][1].passive_observations[-1][
        "action_target"
    ] == event_id
    assert runtime.perceptions[0][1].passive_observations[-1][
        "observed_step"
    ] == 4
    assert runtime.perceptions[0][1].passive_observations[-1]["age_steps"] == 3
    assert cognition.pending_world_events == []


def test_manual_override_acknowledges_only_the_delivered_attention_slice():
    class SimulationControl(Component):
        pass

    actor = _character("甲")
    cognition = actor.get_component("Cognition")
    for index in range(25):
        cognition.record_world_event(
            event_id=f"event:{index:02d}",
            statement=f"第{index}件世界变化。",
            step=index,
            location="大厅",
            witness_mode="direct",
            attention_priority=50,
        )
        cognition.record_event_response(
            response_id=f"response:{index:02d}",
            event_id=f"event:{index:02d}",
            source="乙",
            response_kind="request",
            statement=f"第{index}件世界变化。",
            step=index,
            location="大厅",
            attention_priority=90,
        )
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"大厅": {}},
            actor_states={"甲": {"location": "大厅"}},
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": None,
        "overrides": {"甲": "等待并整理思绪。"},
        "clock": None,
        "player_name": "甲",
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "甲": actor}, context)

    delivered = context["manual_perceptions"]["甲"]
    delivered_events = set(delivered["pending_world_events"])
    delivered_responses = set(delivered["pending_event_responses"])
    assert len(delivered_events) == 20
    assert len(delivered_responses) == 20
    assert len(cognition.pending_world_events) == 5
    assert len(cognition.pending_event_responses) == 5
    assert delivered_events.isdisjoint(cognition.pending_world_events)
    assert delivered_responses.isdisjoint(cognition.pending_event_responses)
    assert context["intents"][0]["source"] == "manual"
    controller = actor.get_component("AgentController")
    assert controller.decision_count == 1
    assert controller.last_decision_step == 0


def test_manual_decision_context_is_bounded_and_omits_raw_cognition_ledgers():
    perception = AgentPerception(
        actor_name="甲",
        step=3,
        world_view={
            "location": "大厅",
            "visible_actors": ["乙"],
            "visible_world": {"灯": {"powered": True}},
        },
        self_state={"location": "大厅", "capabilities": ["观察"]},
        private_cognition={
            "beliefs": [{"statement": "不应整体复制"}],
            "secrets": ["不应整体复制"],
            "pending_world_events": ["event:1"],
            "pending_event_responses": ["response:1"],
            "current_focus": "灯",
        },
        passive_observations=[
            {
                "actor": "World",
                "result": "灯熄灭了。",
                "event_id": "event:1",
                "observed_step": 2,
                "age_steps": 1,
                "private_result": "不应从被动公共摘要复制",
            }
        ],
    )

    context = perception.manual_decision_context()

    assert context["visible_objects"] == ["灯"]
    assert context["pending_world_events"] == ["event:1"]
    assert context["passive_observations"] == [
        {
            "actor": "World",
            "result": "灯熄灭了。",
            "event_id": "event:1",
            "observed_step": 2,
            "age_steps": 1,
        }
    ]
    assert "beliefs" not in context
    assert "secrets" not in context


def test_repeated_report_of_known_event_does_not_requeue_attention():
    cognition = Cognition()
    kwargs = {
        "event_id": "timeline:ceremony:missed",
        "statement": "甲错过了仪式。",
        "step": 2,
        "location": "礼堂",
        "witness_mode": "direct",
    }
    cognition.record_world_event(**kwargs)
    cognition.acknowledge_world_events()

    cognition.record_world_event(**{**kwargs, "step": 3, "witness_mode": "reported"})

    assert cognition.pending_world_events == []


def test_attention_queue_keeps_high_priority_events_under_low_value_flood():
    cognition = Cognition()
    for step in range(45):
        cognition.record_world_event(
            event_id=f"movement:{step}",
            statement=f"第{step}次普通移动。",
            step=step,
            location="街道",
            witness_mode="direct",
            attention_priority=25,
        )
    cognition.record_world_event(
        event_id="obligation:delivery:breached",
        statement="关键交付已经违约。",
        step=46,
        location="街道",
        witness_mode="self",
        attention_priority=95,
    )

    assert len(cognition.pending_world_events) == 40
    assert cognition.next_pending_world_event() == "obligation:delivery:breached"
    assert cognition.get_private_snapshot()["pending_world_events"][0] == (
        "obligation:delivery:breached"
    )
    # A small oldest-first reserve lets waiting observations survive long
    # enough to receive deterministic aging, without displacing the breach.
    assert "movement:0" in cognition.pending_world_events
    assert any(
        int(event_id.split(":", 1)[1]) >= 30
        for event_id in cognition.pending_world_events
        if event_id.startswith("movement:")
    )
    assert "obligation:delivery:breached" in cognition.world_event_attention


def test_attention_aging_eventually_schedules_a_retained_ordinary_event():
    cognition = Cognition()
    cognition.record_world_event(
        event_id="movement:waiting",
        statement="有人很早以前离开了房间。",
        step=0,
        location="房间",
        witness_mode="direct",
        attention_priority=25,
    )
    cognition.record_world_event(
        event_id="obligation:fresh:breached",
        statement="刚刚发生了一次违约。",
        step=279,
        location="房间",
        witness_mode="direct",
        attention_priority=95,
    )

    assert cognition.next_pending_attention(279) == (
        "world_event",
        "obligation:fresh:breached",
    )
    assert cognition.next_pending_attention(280) == (
        "world_event",
        "movement:waiting",
    )
    assert cognition.get_private_snapshot(280)["pending_world_events"][0] == (
        "movement:waiting"
    )
    restored = Cognition(**cognition.model_dump())
    assert restored.next_pending_attention(280) == cognition.next_pending_attention(280)
    assert restored.get_private_snapshot(280) == cognition.get_private_snapshot(280)


def test_host_attention_catalog_covers_alarm_for_every_delivery_path():
    fact = SimpleNamespace(
        kind="scene_state_changed",
        metadata={"changed_paths": ["alarm"]},
        subjects=[],
    )

    assert HostAttentionPolicy.event_priority(fact, "甲") == 90


def test_scheduler_compares_event_response_and_world_event_priority():
    character = _character("乙")
    cognition = character.get_component("Cognition")
    cognition.record_world_event(
        event_id="movement:ordinary",
        statement="甲走进了房间。",
        step=2,
        location="房间",
        witness_mode="direct",
        attention_priority=25,
    )
    cognition.record_event_response(
        response_id="event-response:old:甲->乙:apologize",
        event_id="old",
        source="甲",
        response_kind="apologize",
        statement="甲此前失约。",
        step=3,
        location="房间",
        attention_priority=90,
    )

    activation = AgentScheduler().activation_for(
        character,
        step=3,
        actor_location="远处",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )

    assert activation.reason == (
        "event_response:event-response:old:甲->乙:apologize"
    )
    snapshot = cognition.get_private_snapshot()
    cognition.acknowledge_event_responses(snapshot["pending_event_responses"])
    next_activation = AgentScheduler().activation_for(
        character,
        step=3,
        actor_location="远处",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )
    assert next_activation.reason == "world_event:movement:ordinary"


def test_world_event_experience_is_passive_and_preserves_witness_mode():
    cognition = Cognition()
    cognition.record_world_event(
        event_id="exchange:4:trade",
        statement="甲与乙完成了交换。",
        step=4,
        location="集市",
        witness_mode="direct",
    )

    event = cognition.experiences[-1]["events"][0]
    assert event["observation_mode"] == "passive"
    assert event["witness_mode"] == "direct"


def test_structured_injected_event_wakes_agent_at_remote_location():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(action="查看远处传来的警报。")
    registry = AgentRegistry()
    watcher = _character("瞭望员")
    watcher.get_component("AgentController").background_interval = 99
    registry.register(watcher, runtime)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"营地": {}, "山顶": {}},
            actor_states={
                "玩家": {"location": "营地"},
                "瞭望员": {"location": "山顶"},
            },
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": None,
        "player_name": "玩家",
        "inject_events": [
            {
                "event_id": "mountain_flare",
                "intent": "北侧山脊出现红色信号弹。",
                "location": "山顶",
                "tags": ["alarm", "mountain"],
                "visibility": "local",
            }
        ],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "瞭望员": watcher}, context)

    assert context["intents"][0]["event_id"] == "mountain_flare"
    assert context["intents"][-1]["actor"] == "瞭望员"
    assert runtime.perceptions[0][1].world_signals[0]["intent"] == "北侧山脊出现红色信号弹。"


def test_cognition_keeps_agent_inference_subjective_and_bounded():
    cognition = Cognition(
        beliefs=[{"statement": "门是锁着的", "confidence": 0.4, "source": "远看"}],
        secrets=["钥匙藏在花盆下"],
        commitments=["天黑前回来"],
    )

    cognition.apply_agent_updates(
        {
            "focus": "确认门后的声音",
            "belief_updates": [
                {"statement": "门是锁着的", "confidence": 1.7, "source": "亲手试门"},
                {"statement": "屋里可能有人", "confidence": -0.2, "source": "微弱声响"},
            ],
            "commitments": ["不要让守卫发现钥匙"],
            "resolved_commitments": ["天黑前回来"],
        },
        step=4,
    )

    snapshot = cognition.get_private_snapshot()
    beliefs = {item["statement"]: item for item in snapshot["beliefs"]}
    assert beliefs["门是锁着的"]["confidence"] == 1.0
    assert beliefs["屋里可能有人"]["confidence"] == 0.0
    assert snapshot["secrets"] == ["钥匙藏在花盆下"]
    assert snapshot["commitments"] == ["不要让守卫发现钥匙"]
    assert snapshot["current_focus"] == "确认门后的声音"

    cognition.apply_agent_updates(
        {
            "clear_focus": True,
            "resolved_commitments": ["不要让守卫发现钥匙"],
        },
        step=5,
    )

    cleared = cognition.get_private_snapshot()
    assert cleared["current_focus"] == ""
    assert cleared["commitments"] == []


def test_agent_belief_updates_cannot_rewrite_host_event_knowledge():
    cognition = Cognition()
    cognition.record_world_event(
        event_id="storm:arrived",
        statement="暴风雨已经抵达港口。",
        step=2,
        location="港口",
        witness_mode="direct",
        confidence=0.9,
    )

    cognition.apply_agent_updates(
        {
            "belief_updates": [
                {
                    "operation": "upsert",
                    "statement": "暴风雨已经抵达港口。",
                    "confidence": 0.1,
                    "source": "自我安慰",
                },
                {
                    "operation": "retract",
                    "statement": "暴风雨已经抵达港口。",
                },
            ]
        },
        step=3,
    )

    event_belief = next(
        item
        for item in cognition.beliefs
        if item.get("event_id") == "storm:arrived"
    )
    assert event_belief["confidence"] == 0.9
    assert event_belief["source"] == "direct_world_event:storm:arrived"
    assert cognition.knows_event("storm:arrived") is True

    for batch in range(15):
        cognition.apply_agent_updates(
            {
                "belief_updates": [
                    {
                        "statement": f"普通推断 {batch}-{index}",
                        "confidence": 0.5,
                        "source": "持续猜测",
                    }
                    for index in range(8)
                ]
            },
            step=4 + batch,
        )

    assert len(cognition.beliefs) == 100
    assert cognition.knows_event("storm:arrived") is True


def test_agent_decision_can_update_private_plan_and_beliefs_only():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(
        action="先检查窗边留下的痕迹。",
        metadata={
            "plan": "确认痕迹来源后再决定是否告诉别人",
            "focus": "窗边痕迹",
            "belief_updates": [
                {"statement": "有人夜里进过房间", "confidence": 0.7, "source": "窗边痕迹"}
            ],
        },
    )
    registry = AgentRegistry()
    character = _character("调查者")
    character.get_component("Cognition").secrets = ["自己藏着一封信"]
    registry.register(character, runtime)

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={"调查者": {"location": "房间"}},
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": None,
        "player_name": "调查者",
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "调查者": character}, context)

    cognition = character.get_component("Cognition").get_private_snapshot()
    assert cognition["beliefs"][0]["statement"] == "有人夜里进过房间"
    assert character.get_component("Planning").get_plan() == "确认痕迹来源后再决定是否告诉别人"
    assert runtime.perceptions[0][1].private_cognition["secrets"] == ["自己藏着一封信"]
    assert "有人夜里进过房间" not in gm.get_component("SceneState").actor_states["调查者"]


def test_agent_can_retire_private_continuity_state_before_next_turn():
    class SimulationControl(Component):
        pass

    runtime = RecordingRuntime(
        action="停下旧计划，重新观察局面。",
        metadata={
            "clear_plan": True,
            "clear_focus": True,
            "resolved_commitments": ["等乙回来"],
            "belief_updates": [
                {"operation": "retract", "statement": "门后有人"}
            ],
        },
    )
    registry = AgentRegistry()
    character = _character("甲")
    character.get_component("Planning").set_plan("守在门边直到乙回来")
    cognition = character.get_component("Cognition")
    cognition.current_focus = "门外脚步"
    cognition.commitments = ["等乙回来"]
    cognition.beliefs = [
        {"statement": "门后有人", "confidence": 0.5, "source": "脚步声"}
    ]
    registry.register(character, runtime)

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"大厅": {}},
            actor_states={"甲": {"location": "大厅"}},
        )
    )

    def run_input(step):
        context = {
            "dispatcher": None,
            "agent_registry": registry,
            "overrides": {},
            "clock": SimpleNamespace(current_step=step),
            "player_name": "甲",
            "inject_events": [],
            "intents": [],
        }
        InputSystem().update({"GameMaster": gm, "甲": character}, context)

    run_input(1)
    runtime.metadata = {}
    run_input(2)

    second_perception = runtime.perceptions[1][1]
    assert second_perception.current_plan == ""
    assert second_perception.private_cognition["current_focus"] == ""
    assert second_perception.private_cognition["commitments"] == []
    assert second_perception.private_cognition["beliefs"] == []
def test_memory_retrieval_uses_structured_goal_and_social_routes_without_new_events():
    class SimulationControl(Component):
        pass

    class Memory(Component):
        queries: list = []

        def retrieve(self, query, n_results=2):
            self.queries.append((query, n_results))
            if "乙" in query:
                return ["乙曾经说过钥匙可能在钟楼。", "共同的重复记忆。"]
            if "失踪的钥匙" in query:
                return ["过去曾在钟楼附近寻找过钥匙。", "共同的重复记忆。"]
            return []

    runtime = RecordingRuntime(action="继续寻找线索。")
    registry = AgentRegistry()
    character = _character("甲")
    character.add_component(GoalState.from_initial(["找到失踪的钥匙"]))
    memory = Memory()
    character.add_component(memory)
    registry.register(character, runtime)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={
                "甲": {"location": "房间"},
                "乙": {"location": "房间"},
            },
        )
    )
    context = {
        "dispatcher": None,
        "agent_registry": registry,
        "overrides": {},
        "clock": None,
        "player_name": "甲",
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "甲": character}, context)

    perception = runtime.perceptions[0][1]
    queried_text = "\n".join(query for query, _ in memory.queries)
    assert "失踪的钥匙" in queried_text
    assert "乙" in queried_text
    assert "过去曾在钟楼附近寻找过钥匙。" in perception.relevant_memories
    assert "乙曾经说过钥匙可能在钟楼。" in perception.relevant_memories
    assert perception.relevant_memories.count("共同的重复记忆。") == 1
    trace = context["memory_retrieval_traces"]["甲"]
    assert {item["route"] for item in trace["queries"]} >= {
        "situation",
        "goals",
    }
    assert trace["result_count"] == 3


def test_memory_routes_batch_query_and_enforce_deduplicated_budget():
    class BatchMemory:
        def __init__(self):
            self.calls = []

        def retrieve_many(self, queries, n_results=2):
            self.calls.append((list(queries), n_results))
            return [
                ["同一条记忆", "目标相关记忆"],
                ["同一条记忆", "社会关系记忆"],
            ]

        def retrieve(self, query, n_results=2):
            raise AssertionError("batch-capable memory should not query one route at a time")

    memory = BatchMemory()
    results, trace = AgentMemoryContextBuilder().retrieve(
        memory,
        [
            MemoryQueryRoute("goals", "寻找钥匙"),
            MemoryQueryRoute("social", "乙 信任"),
        ],
    )

    assert memory.calls == [(["寻找钥匙", "乙 信任"], 3)]
    assert results == ["同一条记忆", "目标相关记忆", "社会关系记忆"]
    assert trace["result_count"] == 3


def test_memory_reranking_can_prioritize_old_salient_event_over_recent_noise():
    class DetailedMemory:
        def retrieve_many_detailed(self, queries, n_results=3):
            assert queries == ["当前调查"]
            return [
                [
                    {
                        "content": "昨天普通地看了一眼桌面。",
                        "metadata": {"step": 99, "salience": 1.0},
                        "distance": 0.05,
                    },
                    {
                        "content": "很久以前发现关键证据并导致目标失败。",
                        "metadata": {"step": 10, "salience": 8.0},
                        "distance": 0.8,
                    },
                ]
            ]

    results, trace = AgentMemoryContextBuilder().retrieve(
        DetailedMemory(),
        [MemoryQueryRoute("claims", "当前调查")],
        current_step=100,
    )

    assert results[0] == "很久以前发现关键证据并导致目标失败。"
    assert trace["selected"][0]["salience"] == 8.0
    assert trace["selected"][0]["score"] > trace["selected"][1]["score"]


def test_memory_consolidation_compacts_only_old_low_salience_logs_write_first():
    class ConsolidationMemory:
        def __init__(self):
            self.records = [
                {
                    "id": f"low-{step}",
                    "content": f"Step {step}\nPersonal Outcome:\n- 平常地整理了房间 {step}",
                    "metadata": {
                        "step": step,
                        "type": "episodic_log",
                        "salience": 1.5,
                    },
                }
                for step in range(1, 7)
            ] + [
                {
                    "id": "important",
                    "content": "Step 2\nPrivate Goal Transition:\n- 关键目标失败",
                    "metadata": {
                        "step": 2,
                        "type": "episodic_log",
                        "salience": 8.0,
                    },
                },
                {
                    "id": "recent",
                    "content": "Step 20\nPersonal Outcome:\n- 最近的普通行动",
                    "metadata": {
                        "step": 20,
                        "type": "episodic_log",
                        "salience": 1.0,
                    },
                },
            ]
            self.operations = []

        def list_memories(self, where=None, limit=None):
            self.operations.append(("list", where, limit))
            return list(self.records)

        def add_memory(self, content, metadata=None):
            self.operations.append(("add", content, dict(metadata or {})))
            self.records.append(
                {
                    "id": "summary",
                    "content": content,
                    "metadata": dict(metadata or {}),
                }
            )

        def delete_memories(self, ids):
            self.operations.append(("delete", list(ids)))
            removed = set(ids)
            self.records = [item for item in self.records if item["id"] not in removed]

    memory = ConsolidationMemory()
    result = MemoryConsolidator().maybe_consolidate(memory, current_step=36)

    assert result["status"] == "consolidated"
    assert result["source_count"] == 6
    assert [item[0] for item in memory.operations] == ["list", "add", "delete"]
    remaining_ids = {item["id"] for item in memory.records}
    assert remaining_ids == {"important", "recent", "summary"}
    summary = next(item for item in memory.records if item["id"] == "summary")
    assert summary["metadata"]["type"] == "consolidated_summary"
    assert summary["metadata"]["source_count"] == 6
    assert "关键目标失败" not in summary["content"]


def test_memory_consolidation_never_deletes_sources_if_summary_write_fails():
    class FailingMemory:
        def __init__(self):
            self.deleted = []

        def list_memories(self, where=None, limit=None):
            return [
                {
                    "id": f"m-{step}",
                    "content": f"Step {step}\n- 普通日志",
                    "metadata": {
                        "step": step,
                        "type": "episodic_log",
                        "salience": 1.0,
                    },
                }
                for step in range(1, 7)
            ]

        def add_memory(self, content, metadata=None):
            raise RuntimeError("storage unavailable")

        def delete_memories(self, ids):
            self.deleted.extend(ids)

    memory = FailingMemory()
    result = MemoryConsolidator().maybe_consolidate(memory, current_step=36)

    assert result["status"] == "write_failed"
    assert memory.deleted == []


def test_memory_consolidation_runs_against_real_vector_store():
    memory = VectorMemory(agent_name="consolidation_vector_store_test")
    existing = memory.list_memories()
    if existing:
        memory.delete_memories([item["id"] for item in existing])
    for step in range(1, 7):
        memory.add_memory(
            f"Step {step}\nPersonal Outcome:\n- 普通地整理房间 {step}",
            metadata={
                "step": step,
                "type": "episodic_log",
                "salience": 1.0,
            },
        )
    memory.add_memory(
        "Step 2\nPrivate Goal Transition:\n- 关键目标失败",
        metadata={"step": 2, "type": "episodic_log", "salience": 8.0},
    )

    result = MemoryConsolidator().maybe_consolidate(memory, current_step=36)
    remaining = memory.list_memories()

    assert result["status"] == "consolidated"
    assert sum(
        item["metadata"].get("type") == "consolidated_summary"
        for item in remaining
    ) == 1
    assert sum(
        float(item["metadata"].get("salience", 0)) >= 8.0
        for item in remaining
    ) == 1
    memory.delete_memories([item["id"] for item in remaining])


def test_sessions_with_same_character_name_use_isolated_memory_collections():
    scenario = ScenarioConfig(
        name="记忆隔离",
        default_agent_runtime="llm",
        description="两个独立 Session 不共享角色记忆。",
        environment="房间",
        initial_state="甲刚刚到场。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "房间"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="旅人",
                personality="谨慎",
                goals=["观察房间"],
                is_player=True,
                agent_runtime="test",
            )
        ],
    )
    factories = {"test": lambda entity, config: RecordingRuntime()}
    first = create_session(
        scenario,
        random_seed="same-seed",
        agent_runtime_factories=factories,
    )
    second = create_session(
        scenario,
        random_seed="same-seed",
        agent_runtime_factories=factories,
    )
    first_memory = first.entities["甲"].get_component("Memory")
    second_memory = second.entities["甲"].get_component("Memory")

    assert first.runner.memory_namespace != second.runner.memory_namespace
    assert first_memory.collection_name != second_memory.collection_name
    first_memory.add_memory(
        "只属于第一个 Session 的秘密经历。",
        metadata={"step": 1, "type": "episodic_log", "salience": 8.0},
    )
    assert len(first_memory.list_memories()) == 1
    assert second_memory.list_memories() == []
    first_memory.delete_memories(
        [item["id"] for item in first_memory.list_memories()]
    )


def test_explicit_memory_namespace_is_stable_for_save_resume_boundary():
    first = VectorMemory(agent_name="甲", namespace="save-slot-1")
    second = VectorMemory(agent_name="甲", namespace="save-slot-1")
    other = VectorMemory(agent_name="甲", namespace="save-slot-2")

    assert first.collection_name == second.collection_name
    assert first.collection_name != other.collection_name




def test_cognition_system_does_not_leak_remote_or_hidden_outcomes():
    class SimulationControl(Component):
        pass

    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"甲地": {}, "乙地": {}},
            actor_states={
                "甲": {"location": "甲地"},
                "乙": {"location": "乙地"},
                "丙": {"location": "甲地"},
            },
        )
    )
    observer = _character("甲")
    remote = _character("乙")
    colocated = _character("丙")
    context = {
        "clock": None,
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "intent": "打开箱子",
                    "outcome": "success",
                    "result": "乙看见了箱中的信。",
                    "location": "乙地",
                    "visibility": "public",
                },
                {
                    "actor": "丙",
                    "intent": "藏起钥匙",
                    "outcome": "success",
                    "result": "丙把钥匙藏进袖口。",
                    "location": "甲地",
                    "visibility": "hidden",
                },
                {
                    "actor": "丙",
                    "intent": "敲门",
                    "outcome": "success",
                    "result": "丙敲响了门。",
                    "location": "甲地",
                    "visibility": "public",
                },
            ]
        },
    }

    CognitionSystem().update(
        {"GameMaster": gm, "甲": observer, "乙": remote, "丙": colocated},
        context,
    )

    observer_events = observer.get_component("Cognition").experiences[-1]["events"]
    observer_results = [item["result"] for item in observer_events]
    assert observer_results == ["丙敲响了门。"]
    remote_results = [
        item["result"]
        for item in remote.get_component("Cognition").experiences[-1]["events"]
    ]
    assert remote_results == ["乙看见了箱中的信。"]
def test_same_turn_npcs_cannot_see_each_others_uncommitted_proposals():
    class SimulationControl(Component):
        pass

    def run_with_order(order):
        gm = Entity("GameMaster")
        gm.add_component(SimulationControl())
        gm.add_component(
            SceneState(
                world_objects={"大厅": {}},
                actor_states={
                    "玩家": {"location": "大厅"},
                    "甲": {"location": "大厅"},
                    "乙": {"location": "大厅"},
                },
            )
        )
        player = _character("玩家")
        first = _character(order[0])
        second = _character(order[1])
        runtimes = {
            "甲": RecordingRuntime(action="甲提出自己的方案。"),
            "乙": RecordingRuntime(action="乙提出自己的方案。"),
        }
        registry = AgentRegistry()
        registry.register(first, runtimes[first.name])
        registry.register(second, runtimes[second.name])
        entities = {"GameMaster": gm, "玩家": player, first.name: first, second.name: second}
        context = {
            "dispatcher": None,
            "agent_registry": registry,
            "overrides": {"玩家": "我要求两人分别说明方案。"},
            "clock": None,
            "player_name": "玩家",
            "inject_events": [],
            "intents": [],
        }
        InputSystem().update(entities, context)
        perceived = {
            name: [item["actor"] for item in runtime.perceptions[0][1].visible_proposals]
            for name, runtime in runtimes.items()
        }
        return perceived, context["intents"]

    forward, forward_intents = run_with_order(["甲", "乙"])
    reverse, reverse_intents = run_with_order(["乙", "甲"])

    assert forward == {"甲": ["玩家"], "乙": ["玩家"]}
    assert reverse == forward
    assert {item["actor"] for item in forward_intents} == {"玩家", "甲", "乙"}
    assert {item["actor"] for item in reverse_intents} == {"玩家", "甲", "乙"}
