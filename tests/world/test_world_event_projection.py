from types import SimpleNamespace
from copy import deepcopy

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.world_events import WorldEventSystem
from src.story_engine.systems.simulation import SimulationSystem
from src.story_engine.agents import AgentScheduler


class SimulationControl(Component):
    scripted_result: dict
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


def _world():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={"大厅": {}, "密室": {}},
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "大厅"},
            "丙": {"location": "密室"},
        },
    )
    gm.add_component(scene)
    entities = {
        "GameMaster": gm,
        "甲": create_agent("甲", "持有者", "谨慎", [], agent_runtime="llm"),
        "乙": create_agent("乙", "同场者", "敏锐", [], agent_runtime="llm"),
        "丙": create_agent("丙", "远处的人", "平静", [], agent_runtime="llm"),
    }
    return entities, scene


def _context(result, *, committed=True, step=4):
    return {
        "clock": SimpleNamespace(current_step=step),
        "state_transaction": {"committed": committed, "errors": []},
        "simulation_result": result,
    }


def _action(actor="甲", visibility="local"):
    return {
        "actor": actor,
        "action_kind": "interact",
        "outcome": "success",
        "location": "大厅",
        "visibility": visibility,
        "result": "行动完成。",
    }


def test_committed_object_relocation_becomes_local_objective_event():
    entities, _ = _world()
    context = _context(
        {
            "resolved_actions": [_action()],
            "object_lifecycle": [
                {
                    "operation": "relocate",
                    "object_id": "铜钥匙",
                    "actor": "甲",
                    "owner": "甲",
                }
            ],
            "exchanges": [],
        }
    )

    WorldEventSystem().update(entities, context)

    event_id = "object:4:0:relocate:铜钥匙"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    assert fact.kind == "object_relocate"
    assert fact.source_type == "resolved_action"
    assert fact.source_ref == "step:4:actor:甲"
    assert fact.objects == ["铜钥匙"]
    assert fact.visibility == "local"
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert entities["乙"].get_component("Cognition").knows_event(event_id)
    assert not entities["丙"].get_component("Cognition").knows_event(event_id)


def test_moving_witness_keeps_origin_object_event_and_event_location():
    entities, scene = _world()
    scene.update_actor_state("乙", {"location": "密室"})
    context = _context(
        {
            "resolved_actions": [_action()],
            "object_lifecycle": [
                {
                    "operation": "relocate",
                    "object_id": "铜钥匙",
                    "actor": "甲",
                    "owner": "甲",
                }
            ],
            "exchanges": [],
        }
    )
    context["actor_observation_windows"] = {
        "甲": {"locations": ["大厅"]},
        "乙": {"locations": ["大厅", "密室"]},
        "丙": {"locations": ["密室"]},
    }

    WorldEventSystem().update(entities, context)

    event_id = "object:4:0:relocate:铜钥匙"
    event = entities[f"WorldEvent:{event_id}"]
    witnesses = event.get_component("WorldEventWitnesses")
    assert "乙" in witnesses.direct_witnesses
    cognition = entities["乙"].get_component("Cognition")
    assert cognition.knows_event(event_id)
    event_experience = next(
        item
        for experience in cognition.experiences
        for item in experience["events"]
        if item.get("action_target") == event_id
    )
    assert event_experience["location"] == "大厅"


def test_newly_spawned_actor_does_not_witness_earlier_local_event():
    entities, scene = _world()
    scene.actor_states["新来者"] = {"location": "大厅"}
    entities["新来者"] = create_agent("新来者", "新到者", "警觉", [], agent_runtime="llm")
    context = _context(
        {
            "resolved_actions": [_action()],
            "object_lifecycle": [
                {
                    "operation": "relocate",
                    "object_id": "铜钥匙",
                    "actor": "甲",
                    "owner": "甲",
                }
            ],
            "exchanges": [],
        }
    )
    context["actor_observation_windows"] = {
        "甲": {"locations": ["大厅"], "present_during_step": True},
        "乙": {"locations": ["大厅"], "present_during_step": True},
        "丙": {"locations": ["密室"], "present_during_step": True},
        "新来者": {"locations": ["大厅"], "present_during_step": False},
    }

    WorldEventSystem().update(entities, context)

    event_id = "object:4:0:relocate:铜钥匙"
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert "新来者" not in witnesses.direct_witnesses
    assert not entities["新来者"].get_component("Cognition").knows_event(event_id)


def test_committed_observable_object_property_change_becomes_local_event():
    entities, scene = _world()
    scene.world_objects["铜门"] = {
        "is_location": False,
        "location": "大厅",
        "owner": None,
        "container": None,
        "portable": False,
        "hidden": False,
        "open": False,
    }
    before = deepcopy(scene.world_objects)
    scene.update_object_state("铜门", {"open": True, "condition": "unlatched"})
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "action_kind": "interact",
                "action_target": "铜门",
                "outcome": "success",
                "location": "大厅",
                "visibility": "local",
            }
        ],
        "state_updates": {
            "scene": {},
            "world_objects": {
                "铜门": {"open": True, "condition": "unlatched"}
            },
            "actor_states": {},
        },
        "object_lifecycle": [],
        "exchanges": [],
    }
    result["object_state_changes"] = SimulationSystem._derive_object_state_changes(
        before_objects=before,
        scene_state=scene,
        result=result,
    )
    context = _context(result)
    goals = entities["乙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="把铜门修到正常状态",
        description="门的状态变化后需要重新判断修理方式",
        source_kind="visible_object",
        source_ref="铜门",
        priority=0.6,
        step=1,
        completion_conditions=[
            {
                "scope": "world_object",
                "target": "铜门",
                "path": "condition",
                "operator": "eq",
                "value": "working",
            }
        ],
    )
    assert transition and not error
    controller = entities["乙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 1
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "interact|铜门"

    WorldEventSystem().update(entities, context)

    event_id = "object-state:4:0:铜门"
    event = entities[f"WorldEvent:{event_id}"]
    fact = event.get_component("WorldEventFact")
    witnesses = event.get_component("WorldEventWitnesses")
    assert fact.kind == "object_state_changed"
    assert fact.source_type == "resolved_action"
    assert fact.source_ref == "step:4:actors:甲"
    assert fact.metadata["changed_paths"] == ["condition", "open"]
    assert {
        (impact.scope, impact.target, impact.path)
        for impact in fact.impacts
    } == {
        ("world_object", "铜门", "condition"),
        ("world_object", "铜门", "open"),
    }
    assert witnesses.direct_witnesses == ["乙"]
    assert witnesses.self_witnesses == ["甲"]
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert entities["乙"].get_component("Cognition").knows_event(event_id)
    assert not entities["丙"].get_component("Cognition").knows_event(event_id)
    assert event_id not in entities["甲"].get_component(
        "Cognition"
    ).pending_world_events
    assert event_id in entities["乙"].get_component(
        "Cognition"
    ).pending_world_events
    assert controller.repeated_goal_action_count == 0
    assert controller.goal_reactivation_count == 1
    assert context["goal_reactivations"][0]["match_basis"] == "state_dependency"


def test_hidden_object_property_change_is_known_only_to_source_actor():
    entities, scene = _world()
    scene.world_objects["密信"] = {
        "is_location": False,
        "location": "大厅",
        "owner": None,
        "container": None,
        "portable": True,
        "hidden": True,
        "seal": "intact",
    }
    before = deepcopy(scene.world_objects)
    scene.update_object_state("密信", {"seal": "broken"})
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "action_kind": "interact",
                "action_target": "密信",
                "outcome": "success",
                "location": "大厅",
                "visibility": "hidden",
            }
        ],
        "state_updates": {
            "scene": {},
            "world_objects": {"密信": {"seal": "broken"}},
            "actor_states": {},
        },
        "object_lifecycle": [],
        "exchanges": [],
    }
    result["object_state_changes"] = SimulationSystem._derive_object_state_changes(
        before_objects=before,
        scene_state=scene,
        result=result,
    )
    context = _context(result)

    WorldEventSystem().update(entities, context)

    event_id = "object-state:4:0:密信"
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert witnesses.direct_witnesses == []
    assert witnesses.self_witnesses == ["甲"]
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert not entities["乙"].get_component("Cognition").knows_event(event_id)


def test_unchanged_object_property_does_not_create_transition_event():
    entities, scene = _world()
    before = deepcopy(scene.world_objects)
    result = {
        "resolved_actions": [],
        "state_updates": {
            "scene": {},
            "world_objects": {"大厅": {"lighting": "bright"}},
            "actor_states": {},
        },
        "object_lifecycle": [],
        "exchanges": [],
    }
    scene.update_object_state("大厅", {"lighting": "bright"})
    before["大厅"]["lighting"] = "bright"
    result["object_state_changes"] = SimulationSystem._derive_object_state_changes(
        before_objects=before,
        scene_state=scene,
        result=result,
    )
    context = _context(result)

    WorldEventSystem().update(entities, context)

    assert result["object_state_changes"] == []
    assert context["world_event_updates"] == []


def test_simulation_derives_object_change_ledger_and_overwrites_forged_ledger():
    entities, scene = _world()
    gm = entities["GameMaster"]
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "甲",
                        "intent": "调暗大厅里的灯",
                        "action_kind": "interact",
                        "action_target": "大厅",
                        "outcome": "success",
                        "location": "大厅",
                        "visibility": "local",
                        "result": "甲调暗了大厅里的灯。",
                    }
                ],
                "state_updates": {
                    "scene": {"alarm": "ringing", "secret_clock": 9},
                    "world_objects": {"大厅": {"lighting": "dim"}},
                    "actor_states": {},
                },
                "object_state_changes": [
                    {
                        "object_id": "伪造对象",
                        "paths": ["invented"],
                        "location": "密室",
                    }
                ],
                "scene_state_changes": [
                    {
                        "path": "secret_clock",
                        "value": 9,
                        "visibility": "public",
                    }
                ],
            }
        )
    )
    context = {
        "clock": SimpleNamespace(current_step=4),
        "intents": [
            {
                "actor": "甲",
                "intent": "调暗大厅里的灯",
                "action_kind": "interact",
                "action_target": "大厅",
                "location": "大厅",
                "source": "ai",
            }
        ],
    }

    SimulationSystem().update(entities, context)
    WorldEventSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert scene.get_object_state("大厅")["lighting"] == "dim"
    assert scene.get_scene_flag("alarm") == "ringing"
    assert context["simulation_result"]["object_state_changes"] == [
        {
            "object_id": "大厅",
            "paths": ["lighting"],
            "location": "大厅",
            "source_actors": ["甲"],
            "visibility": "local",
        }
    ]
    assert context["simulation_result"]["scene_state_changes"] == [
        {"path": "alarm", "value": "ringing", "visibility": "public"}
    ]
    assert "WorldEvent:object-state:4:0:大厅" in entities
    assert "WorldEvent:scene-state:4:0:alarm" in entities
    assert not any("伪造对象" in name for name in entities)


def test_public_scene_flag_change_becomes_global_event_but_private_flag_does_not():
    entities, scene = _world()
    scene.public_scene_fields = ["alarm"]
    scene.scene_flags.update({"alarm": "off", "secret_clock": 1})
    before = deepcopy(scene.scene_flags)
    scene.scene_flags.update({"alarm": "ringing", "secret_clock": 2})
    result = {
        "resolved_actions": [],
        "state_updates": {
            "scene": {"alarm": "ringing", "secret_clock": 2},
            "world_objects": {},
            "actor_states": {},
        },
        "object_lifecycle": [],
        "exchanges": [],
    }
    result["scene_state_changes"] = SimulationSystem._derive_scene_state_changes(
        before_flags=before,
        scene_state=scene,
        result=result,
    )
    goals = entities["乙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="让警报恢复关闭",
        description="警报状态变化后重新处理",
        source_kind="world_event",
        source_ref="earlier-alarm",
        priority=0.6,
        step=1,
        completion_conditions=[
            {
                "scope": "scene",
                "path": "scene_flags.alarm",
                "operator": "eq",
                "value": "off",
            }
        ],
    )
    assert transition and not error
    controller = entities["乙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 1
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "interact|alarm"
    context = _context(result)

    WorldEventSystem().update(entities, context)

    event_id = "scene-state:4:0:alarm"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    assert result["scene_state_changes"] == [
        {"path": "alarm", "value": "ringing", "visibility": "public"}
    ]
    assert fact.metadata["changed_paths"] == ["alarm"]
    assert [(item.scope, item.target, item.path) for item in fact.impacts] == [
        ("scene", "scene", "scene_flags.alarm")
    ]
    for actor in ("甲", "乙", "丙"):
        assert entities[actor].get_component("Cognition").knows_event(event_id)
    assert controller.repeated_goal_action_count == 0
    assert controller.goal_reactivation_count == 1
    assert context["goal_reactivations"][0]["match_basis"] == "state_dependency"
    assert not any("secret_clock" in name for name in entities)


def test_timeline_phase_transition_becomes_global_observation_once():
    entities, scene = _world()
    entities["丙"].get_component("AgentController").activation_policy = "dormant"
    context = {
        "clock": SimpleNamespace(current_step=5),
        "timeline": {
            "phase_transition": {"from": "afternoon", "to": "night"}
        },
    }

    system = WorldEventSystem()
    system.update(entities, context)
    system.update(entities, context)

    event_id = "scene-phase:5:afternoon->night"
    assert f"WorldEvent:{event_id}" in entities
    for actor in ("甲", "乙", "丙"):
        cognition = entities[actor].get_component("Cognition")
        assert cognition.knows_event(event_id)
    assert entities["甲"].get_component("Cognition").pending_world_events.count(
        event_id
    ) == 1
    assert entities["乙"].get_component("Cognition").pending_world_events.count(
        event_id
    ) == 1
    assert event_id not in entities["丙"].get_component(
        "Cognition"
    ).pending_world_events


def test_failed_transaction_does_not_project_object_or_exchange_events():
    entities, _ = _world()
    context = _context(
        {
            "resolved_actions": [_action()],
            "object_lifecycle": [
                {"operation": "destroy", "object_id": "铜钥匙", "actor": "甲"}
            ],
            "exchanges": [
                {
                    "exchange_id": "bad_trade",
                    "parties": ["甲", "乙"],
                    "transfers": [],
                }
            ],
        },
        committed=False,
    )

    WorldEventSystem().update(entities, context)

    assert not any(name.startswith("WorldEvent:") for name in entities)
    assert context["world_event_updates"] == []


def test_hidden_object_change_is_known_only_to_its_actor():
    entities, _ = _world()
    context = _context(
        {
            "resolved_actions": [_action(visibility="hidden")],
            "object_lifecycle": [
                {
                    "operation": "set_visibility",
                    "object_id": "密信",
                    "actor": "甲",
                    "hidden": True,
                }
            ],
            "exchanges": [],
        }
    )

    WorldEventSystem().update(entities, context)

    event_id = "object:4:0:set_visibility:密信"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert fact.visibility == "hidden"
    assert witnesses.direct_witnesses == []
    assert witnesses.self_witnesses == ["甲"]
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert not entities["乙"].get_component("Cognition").knows_event(event_id)


def test_committed_movement_is_observed_at_departure_and_arrival_locations():
    entities, scene = _world()
    before_locations = {
        name: scene.get_actor_location(name) for name in scene.actor_states
    }
    scene.update_actor_state("甲", {"location": "密室"})
    movements = SimulationSystem._derive_actor_movements(
        before_locations=before_locations,
        scene_state=scene,
        actions=[
            {
                "actor": "甲",
                "action_kind": "move",
                "action_target": "密室",
                "outcome": "success",
                "location": "密室",
                "visibility": "local",
            }
        ],
    )
    context = _context(
        {
            "resolved_actions": [],
            "actor_movements": movements,
            "object_lifecycle": [],
            "exchanges": [],
        }
    )

    WorldEventSystem().update(entities, context)

    event_id = "movement:4:0:甲:大厅->密室"
    event = entities[f"WorldEvent:{event_id}"]
    fact = event.get_component("WorldEventFact")
    witnesses = event.get_component("WorldEventWitnesses")
    assert fact.kind == "actor_moved"
    assert fact.source_type == "resolved_action"
    assert fact.source_ref == "step:4:actor:甲"
    assert fact.metadata["origin_location"] == "大厅"
    assert fact.metadata["destination_location"] == "密室"
    assert witnesses.direct_witnesses == ["丙", "乙"]
    assert witnesses.self_witnesses == ["甲"]
    assert {
        (impact.scope, impact.target, impact.path)
        for impact in fact.impacts
    } >= {
        ("actor", "甲", "location"),
        ("scene", "大厅", "occupancy"),
        ("scene", "密室", "occupancy"),
    }
    for actor in ("甲", "乙", "丙"):
        assert entities[actor].get_component("Cognition").knows_event(event_id)
    assert event_id not in entities["甲"].get_component(
        "Cognition"
    ).pending_world_events
    assert event_id in entities["乙"].get_component(
        "Cognition"
    ).pending_world_events
    assert event_id in entities["丙"].get_component(
        "Cognition"
    ).pending_world_events
    event_locations = {}
    for actor in ("甲", "乙", "丙"):
        experience = next(
            item
            for batch in reversed(
                entities[actor].get_component("Cognition").experiences
            )
            for item in batch.get("events", [])
            if item.get("action_target") == event_id
        )
        event_locations[actor] = experience["location"]
    assert event_locations == {"甲": "密室", "乙": "大厅", "丙": "密室"}


def test_hidden_movement_is_not_telepathically_observed_at_either_end():
    entities, scene = _world()
    before_locations = {
        name: scene.get_actor_location(name) for name in scene.actor_states
    }
    scene.update_actor_state("甲", {"location": "密室"})
    movements = SimulationSystem._derive_actor_movements(
        before_locations=before_locations,
        scene_state=scene,
        actions=[
            {
                "actor": "甲",
                "action_kind": "move",
                "action_target": "密室",
                "outcome": "success",
                "location": "密室",
                "visibility": "hidden",
            }
        ],
    )
    context = _context(
        {
            "resolved_actions": [],
            "actor_movements": movements,
            "object_lifecycle": [],
            "exchanges": [],
        }
    )

    WorldEventSystem().update(entities, context)

    event_id = "movement:4:0:甲:大厅->密室"
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert witnesses.direct_witnesses == []
    assert witnesses.self_witnesses == ["甲"]
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert not entities["乙"].get_component("Cognition").knows_event(event_id)
    assert not entities["丙"].get_component("Cognition").knows_event(event_id)
    assert event_id not in entities["甲"].get_component(
        "Cognition"
    ).pending_world_events


def test_observed_arrival_reactivates_goal_that_depends_on_arriving_actor():
    entities, scene = _world()
    goals = entities["丙"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="与甲正式相识",
        description="等甲来到自己所在的地方后再交谈",
        source_kind="visible_actor",
        source_ref="甲",
        priority=0.6,
        step=1,
        completion_conditions=[
            {
                "scope": "relationship",
                "target": "甲",
                "path": "bits",
                "operator": "contains",
                "value": "acquainted",
            }
        ],
    )
    assert transition and not error
    controller = entities["丙"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 1
    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "communicate|甲"
    before_locations = {
        name: scene.get_actor_location(name) for name in scene.actor_states
    }
    scene.update_actor_state("甲", {"location": "密室"})
    movements = SimulationSystem._derive_actor_movements(
        before_locations=before_locations,
        scene_state=scene,
        actions=[
            {
                "actor": "甲",
                "action_kind": "move",
                "action_target": "密室",
                "outcome": "success",
                "location": "密室",
                "visibility": "local",
            }
        ],
    )
    context = _context(
        {
            "resolved_actions": [],
            "actor_movements": movements,
            "object_lifecycle": [],
            "exchanges": [],
        }
    )

    WorldEventSystem().update(entities, context)

    assert controller.repeated_goal_action_count == 0
    assert controller.goal_reactivation_count == 1
    assert context["goal_reactivations"][0]["actor"] == "丙"
    assert context["goal_reactivations"][0]["match_basis"] == "source_reference"


def test_exchange_is_one_event_known_to_parties_and_same_location_witnesses():
    entities, _ = _world()
    context = _context(
        {
            "resolved_actions": [_action("甲"), _action("乙")],
            "object_lifecycle": [],
            "exchanges": [
                {
                    "exchange_id": "letter_for_key",
                    "parties": ["甲", "乙"],
                    "transfers": [
                        {"from": "甲", "to": "乙", "object_id": "信"},
                        {"from": "乙", "to": "甲", "object_id": "钥匙"},
                    ],
                }
            ],
        }
    )

    system = WorldEventSystem()
    system.update(entities, context)
    system.update(entities, context)

    event_id = "exchange:4:0:letter_for_key"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    assert fact.source_type == "resolved_action"
    assert fact.source_ref == "step:4:actors:乙+甲"
    assert list(name for name in entities if name == f"WorldEvent:{event_id}") == [
        f"WorldEvent:{event_id}"
    ]
    assert len(context["world_event_updates"]) == 0
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert entities["乙"].get_component("Cognition").knows_event(event_id)
    assert not entities["丙"].get_component("Cognition").knows_event(event_id)


def test_internal_relationship_and_sentiment_drift_do_not_become_world_events():
    entities, _ = _world()
    context = {
        "clock": SimpleNamespace(current_step=6),
        "relationship_transitions": [
            {"relation_id": "pair:乙<->甲", "track": "trust", "value": 0.4}
        ],
        "sentiment_updates": [{"actor": "甲", "sentiment": "unease"}],
        "goal_transitions": [{"actor": "甲", "goal_id": "leave"}],
    }

    WorldEventSystem().update(entities, context)

    assert context["world_event_updates"] == []
    assert not any(name.startswith("WorldEvent:") for name in entities)


def test_relevant_observed_object_event_resets_goal_backoff_but_unrelated_does_not():
    entities, scene = _world()
    goals = entities["甲"].get_component("GoalState")
    transition, error = goals.adopt_agent_goal(
        title="取得铜钥匙",
        description="等待取得与事件有关的物品",
        source_kind="visible_object",
        source_ref="铜钥匙",
        priority=0.6,
        step=0,
        completion_conditions=[
            {
                "scope": "world_object",
                "target": "铜钥匙",
                "path": "owner",
                "operator": "eq",
                "value": "甲",
            }
        ],
    )
    assert transition and not error
    controller = entities["甲"].get_component("AgentController")
    controller.last_goal_wakeup_id = transition["goal_id"]
    controller.last_goal_wakeup_step = 10
    controller.repeated_goal_action_count = 4
    controller.last_goal_action_signature = "interact|铜钥匙"
    context = _context(
        {
            "resolved_actions": [_action("乙")],
            "object_lifecycle": [
                {
                    "operation": "relocate",
                    "object_id": "铜钥匙",
                    "actor": "乙",
                    "owner": "乙",
                }
            ],
            "exchanges": [],
        },
        step=12,
    )

    WorldEventSystem().update(entities, context)

    assert controller.repeated_goal_action_count == 0
    assert controller.last_goal_action_signature == ""
    assert controller.last_goal_wakeup_step == 12
    assert controller.goal_reactivation_count == 1
    assert context["goal_reactivations"][0]["actor"] == "甲"
    entities["甲"].get_component("Cognition").acknowledge_world_events()
    scheduler = AgentScheduler()
    assert not scheduler.activation_for(
        entities["甲"],
        step=13,
        actor_location="大厅",
        player_location="密室",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason.startswith("agent_goal:")
    assert scheduler.activation_for(
        entities["甲"],
        step=14,
        actor_location="大厅",
        player_location="密室",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    ).reason == f"agent_goal:{transition['goal_id']}"

    controller.repeated_goal_action_count = 3
    controller.last_goal_action_signature = "interact|铜钥匙"
    unrelated = _context(
        {
            "resolved_actions": [_action("乙")],
            "object_lifecycle": [
                {
                    "operation": "relocate",
                    "object_id": "无关石块",
                    "actor": "乙",
                    "owner": "乙",
                }
            ],
            "exchanges": [],
        },
        step=15,
    )
    WorldEventSystem().update(entities, unrelated)

    assert controller.repeated_goal_action_count == 3
    assert controller.goal_reactivation_count == 1
    assert unrelated.get("goal_reactivations", []) == []

    scene.update_actor_state("甲", {"location": "密室"})
    controller.repeated_goal_action_count = 2
    controller.last_goal_action_signature = "interact|铜钥匙"
    unseen_relevant = _context(
        {
            "resolved_actions": [_action("乙")],
            "object_lifecycle": [
                {
                    "operation": "set_visibility",
                    "object_id": "铜钥匙",
                    "actor": "乙",
                    "hidden": False,
                }
            ],
            "exchanges": [],
        },
        step=16,
    )
    WorldEventSystem().update(entities, unseen_relevant)

    assert controller.repeated_goal_action_count == 2
    assert controller.goal_reactivation_count == 1
    assert unseen_relevant.get("goal_reactivations", []) == []
