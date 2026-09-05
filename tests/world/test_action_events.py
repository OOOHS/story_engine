from src.story_engine.agents import AgentAction, AgentDecision, AgentRegistry
from src.story_engine.clocks.game_clock import GameClock
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.action_queue import ActionEventQueue
from src.story_engine.systems.action_scheduling import ActionSchedulingSystem
from src.story_engine.systems.cognition import CognitionSystem
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


class SimulationControl(Component):
    pass


class Runtime:
    def decide(self, entity, perception):
        return AgentDecision(
            action="仔细检查门锁",
            action_spec=AgentAction(
                kind="observe",
                detail="仔细检查门锁",
                target="门锁",
            ),
        )


def _proposal(actor, action):
    spec = AgentAction.from_value(action)
    return {
        "actor": actor,
        "intent": spec.detail,
        "action": spec.to_dict(),
        "action_kind": spec.kind,
        "action_target": spec.target,
        "location": "房间",
        "source": "ai",
        "proposal_priority": 0.5,
    }


def test_atomic_action_protocol_keeps_small_kinds_and_natural_language_parameters():
    explicit = AgentAction.from_value(
        {"kind": "observe", "detail": "检查信封背面的压痕", "target": "信封"}
    )
    legacy = AgentAction.from_value("走到门边询问守卫")

    assert explicit.to_dict() == {
        "kind": "observe",
        "detail": "检查信封背面的压痕",
        "target": "信封",
    }
    assert legacy.kind == "move"


def test_interact_action_can_carry_a_non_authoritative_affordance_reference():
    action = AgentAction.from_value(
        {
            "kind": "interact",
            "detail": "吃掉眼前的面包",
            "target": "面包",
            "affordance_id": "eat",
        }
    )

    assert action.to_dict() == {
        "kind": "interact",
        "detail": "吃掉眼前的面包",
        "target": "面包",
        "affordance_id": "eat",
    }
    assert AgentAction.from_value(
        {
            "kind": "move",
            "detail": "带着面包前往走廊",
            "target": "走廊",
            "affordance_id": "eat",
        }
    ).affordance_id == ""


def test_communicate_action_can_reference_known_claim_and_evidence():
    action = AgentAction.from_value(
        {
            "kind": "communicate",
            "detail": "向乙展示账册并否认其内容",
            "target": "乙",
            "claim_id": "ledger_owner",
            "claim_stance": "rejects",
            "evidence_refs": ["账册", "账册", "  "],
        }
    )

    assert action.to_dict() == {
        "kind": "communicate",
        "detail": "向乙展示账册并否认其内容",
        "target": "乙",
        "claim_id": "ledger_owner",
        "claim_stance": "rejects",
        "evidence_refs": ["账册"],
    }


def test_interact_action_can_reference_single_object_delivery_recipient():
    action = AgentAction.from_value(
        {
            "kind": "interact",
            "detail": "把信件递给乙",
            "target": "信件",
            "delivery_recipient": "乙",
        }
    )

    assert action.to_dict() == {
        "kind": "interact",
        "detail": "把信件递给乙",
        "target": "信件",
        "delivery_recipient": "乙",
    }


def test_discrete_event_queue_orders_by_completion_time_and_batches_ties():
    queue = ActionEventQueue()
    queue.schedule(_proposal("甲", {"kind": "interact", "detail": "修理门锁"}))
    queue.schedule(_proposal("乙", {"kind": "communicate", "detail": "喊住甲"}))

    first = queue.pop_next_batch()

    assert queue.current_time == 1
    assert [item["actor"] for item in first] == ["乙"]
    assert queue.is_busy("甲") is True
    assert queue.is_busy("乙") is False

    queue.schedule(_proposal("乙", {"kind": "observe", "detail": "查看门锁"}))
    second = queue.pop_next_batch()

    assert queue.current_time == 2
    assert [item["actor"] for item in second] == ["乙", "甲"]
    assert all(item["action_phase"] == "completed" for item in second)


def test_action_scheduling_system_exposes_only_next_completion_to_simulation():
    queue = ActionEventQueue()
    clock = GameClock()
    context = {
        "action_queue": queue,
        "clock": clock,
        "intents": [
            _proposal("甲", {"kind": "move", "detail": "前往走廊", "target": "走廊"}),
            _proposal("乙", {"kind": "communicate", "detail": "提醒甲停下"}),
        ],
    }

    ActionSchedulingSystem().update({}, context)

    assert [item["actor"] for item in context["intents"]] == ["乙"]
    assert context["scheduled_actions"][0]["starts_at"] == 0
    assert clock.current_step == 1
    assert queue.pending_for("甲")["completes_at"] == 2


def test_stated_reason_follows_long_action_without_leaking_to_semantic_intent():
    queue = ActionEventQueue()
    clock = GameClock()
    original_refs = [{"kind": "goal", "ref": "reach-hall"}]
    context = {
        "action_queue": queue,
        "clock": clock,
        "intents": [
            _proposal(
                "甲", {"kind": "move", "detail": "前往走廊", "target": "走廊"}
            ),
            _proposal("乙", {"kind": "communicate", "detail": "提醒甲停下"}),
        ],
        "agent_motive_refs": {
            "甲": original_refs,
            "乙": [{"kind": "goal", "ref": "stop-him"}],
        },
    }

    ActionSchedulingSystem().update({}, context)

    assert [item["actor"] for item in context["intents"]] == ["乙"]
    assert all("_host_metadata" not in item for item in context["intents"])
    assert "host_metadata" not in queue.pending_for("甲")

    # By the time the walk finishes she is thinking about something else. The
    # completion still has to be attributed to why she set out.
    context["intents"] = []
    context["agent_motive_refs"] = {
        "甲": [{"kind": "sentiment", "ref": "乙:annoyed"}]
    }
    ActionSchedulingSystem().update({}, context)

    assert [item["actor"] for item in context["intents"]] == ["甲"]
    assert all("_host_metadata" not in item for item in context["intents"])
    assert (
        context["completed_action_motive_refs"]["甲"]["motive_refs"]
        == original_refs
    )


def test_input_does_not_awaken_agent_while_external_action_is_in_progress():
    queue = ActionEventQueue()
    queue.schedule(_proposal("甲", {"kind": "interact", "detail": "撬开旧锁"}))
    registry = AgentRegistry()
    actor = Entity("甲")
    actor.add_component(AgentController(runtime="test"))
    actor.add_component(Cognition())
    registry.register(actor, Runtime())
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(world_objects={"房间": {}}, actor_states={"甲": {"location": "房间"}})
    )
    context = {
        "action_queue": queue,
        "agent_registry": registry,
        "overrides": {},
        "clock": GameClock(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"WorldHost": gm, "甲": actor}, context)

    assert context["intents"] == []
    assert context["agent_activations"]["甲"]["reason"] == "action_in_progress"
    assert context["agent_activations"]["甲"]["busy_until"] == 2


def test_preempting_an_in_flight_action_voids_it_and_shields_the_next_one():
    queue = ActionEventQueue()
    queue.schedule(
        _proposal("甲", {"kind": "interact", "detail": "撬开旧锁", "target": "旧锁"})
    )

    receipt = queue.preempt("甲", reason="world_event:alarm-1")

    assert receipt["actor"] == "甲"
    assert receipt["action_kind"] == "interact"
    assert receipt["action_target"] == "旧锁"
    assert receipt["reason"] == "world_event:alarm-1"
    assert receipt["planned_completion"] == 2
    # Her own phrasing of what she was doing is not part of the receipt, because
    # the receipt is what witnesses are allowed to learn.
    assert "detail" not in receipt and "intent" not in receipt
    # Conservative settlement: she is free again and the aborted action never
    # reaches Simulation as a completion, so it yields none of its effects.
    assert queue.is_busy("甲") is False
    assert queue.pop_next_batch() == []

    queue.schedule(
        _proposal("甲", {"kind": "interact", "detail": "再撬一次", "target": "旧锁"})
    )

    # Whatever she chooses instead runs to the end, so a character standing in a
    # stream of critical signals still makes progress instead of restarting.
    assert queue.preempt("甲", reason="world_event:alarm-2") is None
    assert queue.is_busy("甲") is True


def test_preemption_is_undone_by_the_step_checkpoint():
    queue = ActionEventQueue()
    queue.schedule(_proposal("甲", {"kind": "interact", "detail": "撬开旧锁"}))
    checkpoint = queue.checkpoint()

    assert queue.preempt("甲", reason="world_event:alarm-1") is not None
    queue.restore(checkpoint)

    # A rolled-back step must leave no trace of the abort, including the
    # immunity it granted -- otherwise a replay would preempt different actions.
    assert queue.is_busy("甲") is True
    queue.preempt("甲", reason="world_event:alarm-1")
    queue.schedule(_proposal("甲", {"kind": "interact", "detail": "再撬一次"}))
    assert queue.preempt("甲", reason="world_event:alarm-2") is None


def _busy_actor_world(*, priority, witness_mode="direct"):
    queue = ActionEventQueue()
    queue.schedule(
        _proposal("甲", {"kind": "interact", "detail": "撬开旧锁", "target": "旧锁"})
    )
    registry = AgentRegistry()
    actor = Entity("甲")
    actor.add_component(AgentController(runtime="test"))
    cognition = Cognition()
    cognition.record_world_event(
        event_id="alarm-1",
        statement="警钟骤然响起。",
        step=0,
        location="房间",
        witness_mode=witness_mode,
        attention_priority=priority,
    )
    actor.add_component(cognition)
    registry.register(actor, Runtime())
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={"甲": {"location": "房间"}},
        )
    )
    context = {
        "action_queue": queue,
        "agent_registry": registry,
        "overrides": {},
        "clock": GameClock(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }
    return queue, {"WorldHost": gm, "甲": actor}, context


def test_critical_signal_interrupts_the_action_a_character_is_in_the_middle_of():
    queue, entities, context = _busy_actor_world(priority=95)

    InputSystem().update(entities, context)

    # Without this she would be skipped for being busy and stay deaf to an alarm
    # for the whole duration of her action.
    assert queue.is_busy("甲") is False
    # Nothing is aborted without a decision replacing it: the same pending
    # record that bought the interruption also guarantees the activation.
    assert context["agent_activations"]["甲"]["active"] is True
    assert [item["actor"] for item in context["intents"]] == ["甲"]
    interrupted = context["interrupted_actions"]
    assert [item["action_kind"] for item in interrupted] == ["interact"]
    assert interrupted[0]["reason"] == "world_event:alarm-1"


def test_non_critical_signal_leaves_a_busy_character_to_finish_her_action():
    queue, entities, context = _busy_actor_world(priority=50)

    InputSystem().update(entities, context)

    # Ambient and direct news is worth a wake once she is free, never worth
    # making her drop what she is holding.
    assert queue.is_busy("甲") is True
    assert context["intents"] == []
    assert context["interrupted_actions"] == []
    assert context["agent_activations"]["甲"]["reason"] == "action_in_progress"


def test_perception_exposes_ongoing_action_shape_without_private_detail_or_hidden_target():
    queue = ActionEventQueue()
    public = _proposal(
        "甲",
        {"kind": "interact", "detail": "悄悄调换公开信", "target": "公开信"},
    )
    hidden = _proposal(
        "丙",
        {"kind": "interact", "detail": "偷走隐藏戒指", "target": "隐藏戒指"},
    )
    queue.schedule(public)
    queue.schedule(hidden)
    observer = Entity("乙")
    observer.add_component(Cognition())
    scene = SceneState(
        world_objects={
            "房间": {},
            "公开信": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "hidden": False,
                "portable": True,
            },
            "隐藏戒指": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "hidden": True,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
            "丙": {"location": "房间"},
        },
    )

    perception = InputSystem().build_agent_perception(
        observer,
        scene,
        [],
        {"clock": GameClock(), "action_queue": queue},
    )

    ongoing = {item["actor"]: item for item in perception.ongoing_actions}
    assert ongoing["甲"]["action_kind"] == "interact"
    assert ongoing["甲"]["visible_target"] == "公开信"
    assert ongoing["丙"]["visible_target"] == ""
    assert "detail" not in ongoing["甲"]


def test_active_observation_is_private_while_other_world_events_are_passive():
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={"甲": {"location": "房间"}, "乙": {"location": "房间"}},
        )
    )
    actors = {}
    for name in ("甲", "乙"):
        entity = Entity(name)
        entity.add_component(Cognition())
        actors[name] = entity
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "intent": "检查门锁",
                "action_kind": "observe",
                "action_target": "门锁",
                "outcome": "success",
                "location": "房间",
                "visibility": "public",
                "result": "甲俯身查看门锁。",
                "private_result": "锁孔边缘有刚留下的铜屑。",
            },
            {
                "actor": "乙",
                "intent": "提醒甲",
                "action_kind": "communicate",
                "outcome": "success",
                "location": "房间",
                "visibility": "public",
                "result": "乙提醒甲不要碰坏门锁。",
                "private_result": "",
            },
        ],
        "knowledge_updates": [],
    }
    context = {"simulation_result": result, "clock": GameClock()}

    CognitionSystem().update({"WorldHost": gm, **actors}, context)

    a_events = actors["甲"].get_component("Cognition").experiences[-1]["events"]
    b_events = actors["乙"].get_component("Cognition").experiences[-1]["events"]
    a_observe = next(item for item in a_events if item["actor"] == "甲")
    b_observe = next(item for item in b_events if item["actor"] == "甲")
    heard_b = next(item for item in a_events if item["actor"] == "乙")

    assert a_observe["observation_mode"] == "active"
    assert a_observe["private_result"] == "锁孔边缘有刚留下的铜屑。"
    assert b_observe["observation_mode"] == "passive"
    assert b_observe["private_result"] == ""
    assert heard_b["observation_mode"] == "passive"

    perception = InputSystem().build_agent_perception(
        actors["甲"],
        gm.get_component("SceneState"),
        [],
        {"clock": GameClock()},
    )
    assert perception.active_observation_results[-1]["private_result"] == (
        "锁孔边缘有刚留下的铜屑。"
    )
    assert any(
        item["actor"] == "乙" for item in perception.passive_observations
    )

    visible = RenderingSystem()._build_visible_simulation(
        result,
        gm.get_component("SceneState").get_view_pov("乙"),
        visible_locations=["房间"],
    )
    assert "private_result" not in visible["resolved_actions"][0]


def test_moving_actor_observes_origin_and_destination_without_remote_leak():
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"出发地": {}, "目的地": {}, "远处": {}},
            actor_states={
                "移动者": {"location": "目的地"},
                "出发地旁观者": {"location": "出发地"},
                "目的地旁观者": {"location": "目的地"},
                "远处角色": {"location": "远处"},
            },
        )
    )
    actors = {}
    for name in ("移动者", "出发地旁观者", "目的地旁观者", "远处角色"):
        entity = Entity(name)
        entity.add_component(Cognition())
        actors[name] = entity
    result = {
        "resolved_actions": [
            {
                "actor": "出发地旁观者",
                "action_kind": "interact",
                "outcome": "success",
                "location": "出发地",
                "visibility": "public",
                "result": "出发地的门被推开。",
            },
            {
                "actor": "目的地旁观者",
                "action_kind": "communicate",
                "outcome": "success",
                "location": "目的地",
                "visibility": "public",
                "result": "目的地有人出声招呼。",
            },
            {
                "actor": "远处角色",
                "action_kind": "interact",
                "outcome": "success",
                "location": "远处",
                "visibility": "public",
                "result": "远处发生了无人可见的变化。",
            },
        ],
        "knowledge_updates": [],
    }
    context = {
        "simulation_result": result,
        "clock": GameClock(),
        "actor_observation_windows": {
            "移动者": {"locations": ["出发地", "目的地"]},
            "出发地旁观者": {"locations": ["出发地"]},
            "目的地旁观者": {"locations": ["目的地"]},
            "远处角色": {"locations": ["远处"]},
        },
    }

    CognitionSystem().update({"WorldHost": gm, **actors}, context)

    mover_events = actors["移动者"].get_component("Cognition").experiences[-1][
        "events"
    ]
    mover_results = {item["result"] for item in mover_events}
    origin_results = {
        item["result"]
        for item in actors["出发地旁观者"]
        .get_component("Cognition")
        .experiences[-1]["events"]
    }
    destination_results = {
        item["result"]
        for item in actors["目的地旁观者"]
        .get_component("Cognition")
        .experiences[-1]["events"]
    }

    assert "出发地的门被推开。" in mover_results
    assert "目的地有人出声招呼。" in mover_results
    assert "远处发生了无人可见的变化。" not in mover_results
    assert "目的地有人出声招呼。" not in origin_results
    assert "出发地的门被推开。" not in destination_results
def test_runner_advances_by_completion_events_instead_of_global_one_action_round():
    class FixedRuntime:
        def __init__(self, action):
            self.action = action

        def decide(self, entity, perception):
            return AgentDecision(
                action=self.action.detail,
                action_spec=self.action,
            )

    scenario = ScenarioConfig(
        name="事件测试",
        default_agent_runtime="llm",
        simulation_mode="rules",
        narration_mode="rules",
        description="两个动作具有不同耗时。",
        environment="房间与走廊",
        initial_state="甲与乙准备行动。",
        initial_world_objects={
            "房间": {"connected_to": ["走廊"]},
            "走廊": {"connected_to": ["房间"]},
        },
        initial_actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
        characters=[
            CharacterConfig(
                name="甲",
                role="说话者",
                personality="直接",
                goals=["提醒乙"],
                agent_runtime="fixed",
            ),
            CharacterConfig(
                name="乙",
                role="行者",
                personality="行动派",
                goals=["去走廊"],
                agent_runtime="fixed",
            ),
        ],
    )

    def factory(entity, config):
        del config
        if entity.name == "甲":
            return FixedRuntime(
                AgentAction(kind="communicate", detail="提醒乙小心", target="乙")
            )
        return FixedRuntime(
            AgentAction(kind="move", detail="前往走廊", target="走廊")
        )

    session = create_session(scenario, agent_runtime_factories={"fixed": factory})
    first = session.run_step()

    assert session.simulation_time == 1
    assert [item["actor"] for item in first["completed_action_events"]] == ["甲"]
    assert session.is_actor_ready("乙") is False

    second = session.run_step()

    assert session.simulation_time == 2
    assert {item["actor"] for item in second["completed_action_events"]} == {"甲", "乙"}
    completed = {
        item["actor"]: item for item in second["completed_action_events"]
    }
    assert completed["乙"]["stale_by_versions"] == 1
    assert completed["甲"]["stale_by_versions"] == 0
    assert session.is_actor_ready("乙") is True
