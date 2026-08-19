from types import SimpleNamespace

from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.cognition import Cognition
from src.story_engine.core.entity import Entity
from src.story_engine.narrative.timeline import TimelineEngine
from src.story_engine.agents import AgentScheduler
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.cognition import CognitionSystem
from src.story_engine.systems.goals import GoalSystem
from src.story_engine.systems.world_events import WorldEventSystem


def _world():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={"住处": {}, "礼堂": {}},
        actor_states={
            "甲": {"location": "住处"},
            "乙": {"location": "礼堂"},
            "丙": {"location": "住处"},
        },
        scene_flags={
            "upcoming_commitments": [
                {
                    "commitment_id": "ceremony",
                    "title": "公开仪式",
                    "participants": ["甲"],
                    "location": "礼堂",
                    "due_step": 2,
                    "grace_steps": 0,
                }
            ]
        },
    )
    gm.add_component(scene)
    entities = {
        "GameMaster": gm,
        "甲": create_agent("甲", "受邀者", "谨慎", [], agent_runtime="llm"),
        "乙": create_agent("乙", "旁观者", "敏锐", [], agent_runtime="llm"),
        "丙": create_agent("丙", "远处的人", "平静", [], agent_runtime="llm"),
    }
    return entities, scene


def _finalized_timeline(scene):
    clock = SimpleNamespace(current_step=2)
    engine = TimelineEngine()
    engine.refresh(scene, {"clock": clock})
    return engine.finalize(scene, {"clock": clock}, player_name=None)


def test_missed_attendance_becomes_objective_event_with_limited_witnesses():
    entities, scene = _world()
    timeline = _finalized_timeline(scene)
    context = {"timeline": timeline}

    WorldEventSystem().update(entities, context)

    event = entities["WorldEvent:timeline:ceremony:missed"]
    fact = event.get_component("WorldEventFact")
    witnesses = event.get_component("WorldEventWitnesses")
    assert fact.kind == "timeline_attendance_missed"
    assert fact.source_type == "timeline_resolution"
    assert fact.source_ref == "ceremony:missed"
    assert fact.subjects == ["甲"]
    assert fact.metadata["missing_participants"] == ["甲"]
    assert witnesses.direct_witnesses == ["乙"]
    assert witnesses.self_witnesses == ["甲"]
    assert entities["甲"].get_component("Cognition").knows_event(fact.event_id)
    assert entities["乙"].get_component("Cognition").knows_event(fact.event_id)
    assert entities["甲"].get_component("Cognition").world_event_attention[
        fact.event_id
    ].priority == 85
    assert entities["乙"].get_component("Cognition").world_event_attention[
        fact.event_id
    ].priority == 80
    assert not entities["丙"].get_component("Cognition").knows_event(fact.event_id)
    assert context["world_event_errors"] == []


def test_object_event_preserves_host_validated_affordance_identity():
    entities, scene = _world()
    scene.world_objects["木匣"] = {
        "is_location": False,
        "location": "住处",
        "is_container": True,
        "container_open": True,
    }
    context = {
        "clock": SimpleNamespace(current_step=3),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "action_kind": "interact",
                    "action_target": "木匣",
                    "outcome": "success",
                    "location": "住处",
                    "visibility": "local",
                }
            ],
            "object_lifecycle": [
                {
                    "operation": "set_container_state",
                    "object_id": "木匣",
                    "actor": "甲",
                    "affordance_id": "engine:open",
                    "open": True,
                }
            ],
        },
    }

    WorldEventSystem().update(entities, context)

    fact = entities[
        "WorldEvent:object:3:0:set_container_state:木匣"
    ].get_component("WorldEventFact")
    assert fact.source_type == "resolved_action"
    assert fact.subjects == ["甲"]
    assert fact.objects == ["木匣"]
    assert fact.metadata["affordance_id"] == "engine:open"


def test_event_report_uses_authoritative_statement_and_does_not_telepathically_spread():
    entities, scene = _world()
    context = {"timeline": _finalized_timeline(scene)}
    WorldEventSystem().update(entities, context)
    event_id = "timeline:ceremony:missed"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")

    # 乙后来与丙同场并主动转述。模型试图改写 statement，但宿主从
    # WorldEventFact 取回客观原文。
    scene.update_actor_state("乙", {"location": "住处"})
    transfer_context = {
        "clock": SimpleNamespace(current_step=3),
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "communicate",
                    "outcome": "success",
                    "location": "住处",
                    "visibility": "local",
                    "result": "乙向丙说明了仪式缺席的情况。",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "乙",
                    "target": "丙",
                    "event_id": event_id,
                    "statement": "甲其实参加了仪式。",
                    "mode": "told",
                    "reason": "乙主动转述自己目击的缺席",
                }
            ],
        },
    }

    CognitionSystem().update(entities, transfer_context)

    cognition = entities["丙"].get_component("Cognition")
    assert cognition.knows_event(event_id)
    assert cognition.event_statement(event_id) == fact.statement
    assert "甲其实参加了仪式" not in cognition.event_statement(event_id)
    assert cognition.world_event_attention[event_id].priority == 80
    assert transfer_context["knowledge_transfers"][0]["confidence"] == 0.65


def test_nonwitness_cannot_forge_event_report():
    entities, scene = _world()
    context = {"timeline": _finalized_timeline(scene)}
    WorldEventSystem().update(entities, context)
    event_id = "timeline:ceremony:missed"
    scene.update_actor_state("丙", {"location": "礼堂"})
    transfer_context = {
        "clock": SimpleNamespace(current_step=3),
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "丙",
                    "action_kind": "communicate",
                    "outcome": "success",
                    "location": "礼堂",
                    "visibility": "local",
                    "result": "丙试图编造仪式消息。",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "丙",
                    "target": "乙",
                    "event_id": event_id,
                    "mode": "told",
                    "reason": "丙试图声称自己知道",
                }
            ],
        },
    }

    CognitionSystem().update(entities, transfer_context)

    assert transfer_context["knowledge_transfers"] == []


def test_known_world_event_can_seed_agent_goal_but_unknown_event_cannot():
    entities, scene = _world()
    event_context = {"timeline": _finalized_timeline(scene)}
    WorldEventSystem().update(entities, event_context)
    event_id = "timeline:ceremony:missed"
    goal_context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "向主持人解释缺席原因",
                "source_kind": "world_event",
                "source_ref": event_id,
                "reason": "甲知道自己错过了公开仪式",
            },
            {
                "actor": "丙",
                "operation": "adopt",
                "title": "利用甲缺席仪式",
                "source_kind": "world_event",
                "source_ref": event_id,
                "reason": "丙并不知道这件事",
            },
        ],
    }

    GoalSystem().update(entities, goal_context)

    goals_a = entities["甲"].get_component("GoalState")
    goals_c = entities["丙"].get_component("GoalState")
    assert any(
        record.title == "向主持人解释缺席原因"
        for record in goals_a.goals.values()
    )
    assert not goals_c.goals
    assert any(error.startswith("丙:") for error in goal_context["goal_errors"])


def test_event_communication_goal_resolves_only_from_verified_transfer():
    entities, scene = _world()
    event_context = {"timeline": _finalized_timeline(scene)}
    WorldEventSystem().update(entities, event_context)
    event_id = "timeline:ceremony:missed"
    scene.update_actor_state("乙", {"location": "住处"})
    adopt_context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "乙",
                "operation": "adopt",
                "title": "把甲缺席仪式的事实告诉丙",
                "source_kind": "world_event",
                "source_ref": event_id,
                "reason": "丙需要知道这件已经发生的事",
                "resolution_kind": "communicate_event",
                "resolution_target": "丙",
            }
        ],
    }

    GoalSystem().update(entities, adopt_context)

    goals = entities["乙"].get_component("GoalState")
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"
    assert goal.completion_conditions == [
        {
            "scope": "world_event",
            "target": event_id,
            "path": "communications",
            "operator": "contains",
            "value": "乙->丙",
        }
    ]

    transfer_context = {
        "clock": SimpleNamespace(current_step=4),
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "communicate",
                    "action_target": "丙",
                    "outcome": "success",
                    "location": "住处",
                    "visibility": "local",
                    "result": "乙把甲缺席仪式的事实告诉了丙。",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "乙",
                    "target": "丙",
                    "event_id": event_id,
                    "mode": "told",
                    "reason": "乙明确转述自己目击的事件",
                }
            ],
        },
    }
    CognitionSystem().update(entities, transfer_context)
    GoalSystem().update(
        entities,
        {"clock": SimpleNamespace(current_step=5), "agent_goal_requests": []},
    )

    responses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventResponses"
    )
    assert responses.communication_keys() == ["乙->丙"]
    assert entities["丙"].get_component("Cognition").knows_event(event_id)
    assert entities["丙"].get_component("SentimentState").sentiments == {}
    assert goal.status == "achieved"


def test_noncommunication_action_cannot_forge_event_response():
    entities, scene = _world()
    WorldEventSystem().update(entities, {"timeline": _finalized_timeline(scene)})
    event_id = "timeline:ceremony:missed"
    scene.update_actor_state("乙", {"location": "住处"})
    context = {
        "clock": SimpleNamespace(current_step=4),
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "interact",
                    "action_target": "桌子",
                    "outcome": "success",
                    "location": "住处",
                    "visibility": "local",
                    "result": "乙整理了桌子。",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "乙",
                    "target": "丙",
                    "event_id": event_id,
                    "mode": "told",
                    "reason": "GM试图把普通行动伪造成转述",
                }
            ],
        },
    }

    CognitionSystem().update(entities, context)

    responses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventResponses"
    )
    assert context["knowledge_transfers"] == []
    assert responses.communication_keys() == []
    assert not entities["丙"].get_component("Cognition").knows_event(event_id)


def test_new_response_to_already_known_event_wakes_recipient_once():
    entities, scene = _world()
    WorldEventSystem().update(entities, {"timeline": _finalized_timeline(scene)})
    event_id = "timeline:ceremony:missed"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    recipient = entities["丙"].get_component("Cognition")
    recipient.record_world_event(
        event_id=event_id,
        statement=fact.statement,
        step=2,
        location="住处",
        witness_mode="reported",
    )
    recipient.acknowledge_world_events()
    goals = entities["丙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="前往礼堂处理缺席后果",
        description="事件仍需要进一步处理",
        source_kind="world_event",
        source_ref=event_id,
        priority=0.6,
        step=2,
        completion_conditions=[
            {
                "scope": "actor",
                "target": "丙",
                "path": "location",
                "operator": "eq",
                "value": "礼堂",
            }
        ],
    )
    assert transition and not error
    controller = entities["丙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 2
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "move|礼堂"
    scene.update_actor_state("乙", {"location": "住处"})
    transfer = {
        "clock": SimpleNamespace(current_step=3),
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "communicate",
                    "action_target": "丙",
                    "outcome": "success",
                    "location": "住处",
                    "visibility": "local",
                    "result": "乙为自己此前的处理方式向丙道歉。",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "乙",
                    "target": "丙",
                    "event_id": event_id,
                    "response_kind": "accuse",
                    "mode": "told",
                    "reason": "乙围绕双方都知道的事件作出新的道歉",
                }
            ],
        },
    }

    CognitionSystem().update(entities, transfer)

    response_id = (
        f"event-response:{event_id}:乙->丙:apologize"
    )
    assert recipient.pending_world_events == []
    assert recipient.pending_event_responses == [response_id]
    assert controller.repeated_goal_action_count == 0
    assert controller.goal_reactivation_count == 1
    assert transfer["goal_reactivations"][0]["actor"] == "丙"
    activation = AgentScheduler().activation_for(
        entities["丙"],
        step=3,
        actor_location="住处",
        player_location="礼堂",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )
    assert activation.active is True
    assert activation.reason == f"event_response:{response_id}"

    recipient.acknowledge_event_responses()
    CognitionSystem().update(entities, transfer)
    assert recipient.pending_event_responses == []
    assert controller.goal_reactivation_count == 1


def test_dormant_cognition_records_response_without_attention_interrupt():
    cognition = Cognition()
    cognition.record_event_response(
        response_id="event-response:storm:甲->乙:explain",
        event_id="storm",
        source="甲",
        response_kind="explain",
        statement="暴风雨已经到来。",
        step=3,
        location="屋内",
        enqueue_attention=False,
    )

    assert cognition.pending_event_responses == []
    assert cognition.experiences[-1]["events"][0]["response_kind"] == "explain"


def test_opening_container_reactivates_goal_for_nested_object_via_state_dependency():
    entities, scene = _world()
    scene.world_objects.update(
        {
            "木匣": {
                "is_location": False,
                "is_container": True,
                "container_open": True,
                "container_opaque": True,
                "location": "住处",
            },
            "铜钥匙": {
                "is_location": False,
                "portable": True,
                "container": "木匣",
            },
        }
    )
    goals = entities["丙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="取得铜钥匙",
        description="钥匙此前被关在木匣里",
        source_kind="visible_object",
        source_ref="铜钥匙",
        priority=0.6,
        step=2,
        completion_conditions=[
            {
                "scope": "world_object",
                "target": "铜钥匙",
                "path": "owner",
                "operator": "eq",
                "value": "丙",
            }
        ],
    )
    assert transition and not error
    controller = entities["丙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 2
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "interact|铜钥匙"
    context = {
        "clock": SimpleNamespace(current_step=3),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "action_kind": "interact",
                    "action_target": "木匣",
                    "location": "住处",
                    "visibility": "local",
                    "outcome": "success",
                }
            ],
            "object_lifecycle": [
                {
                    "operation": "set_container_state",
                    "object_id": "木匣",
                    "actor": "甲",
                    "open": True,
                }
            ],
        },
    }

    WorldEventSystem().update(entities, context)

    fact = next(
        entity.get_component("WorldEventFact")
        for name, entity in entities.items()
        if name.startswith("WorldEvent:object:3:")
    )
    assert {
        (impact.scope, impact.target, impact.path)
        for impact in fact.impacts
    } >= {
        ("world_object", "木匣", "container_open"),
        ("world_object", "铜钥匙", "accessibility"),
        ("world_object", "铜钥匙", "visibility"),
    }
    assert controller.repeated_goal_action_count == 0
    assert controller.goal_reactivation_count == 1
    assert context["goal_reactivations"][0]["match_basis"] == "state_dependency"


def test_unseen_container_impact_does_not_reactivate_nested_object_goal():
    entities, scene = _world()
    scene.world_objects.update(
        {
            "远处木匣": {
                "is_location": False,
                "is_container": True,
                "container_open": True,
                "container_opaque": True,
                "location": "礼堂",
            },
            "银钥匙": {
                "is_location": False,
                "portable": True,
                "container": "远处木匣",
            },
        }
    )
    goals = entities["丙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="取得银钥匙",
        description="等待远处局势发生变化",
        source_kind="visible_object",
        source_ref="银钥匙",
        priority=0.6,
        step=2,
        completion_conditions=[
            {
                "scope": "world_object",
                "target": "银钥匙",
                "path": "owner",
                "operator": "eq",
                "value": "丙",
            }
        ],
    )
    assert transition and not error
    controller = entities["丙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 2
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "interact|银钥匙"
    context = {
        "clock": SimpleNamespace(current_step=3),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "interact",
                    "action_target": "远处木匣",
                    "location": "礼堂",
                    "visibility": "local",
                    "outcome": "success",
                }
            ],
            "object_lifecycle": [
                {
                    "operation": "set_container_state",
                    "object_id": "远处木匣",
                    "actor": "乙",
                    "open": True,
                }
            ],
        },
    }

    WorldEventSystem().update(entities, context)

    fact = next(
        entity.get_component("WorldEventFact")
        for name, entity in entities.items()
        if name.startswith("WorldEvent:object:3:")
    )
    assert any(
        impact.target == "银钥匙" and impact.path == "accessibility"
        for impact in fact.impacts
    )
    assert not entities["丙"].get_component("Cognition").knows_event(fact.event_id)
    assert controller.repeated_goal_action_count == 3
    assert controller.goal_reactivation_count == 0
    assert context.get("goal_reactivations", []) == []
