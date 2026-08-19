from types import SimpleNamespace

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.world_events import WorldEventSystem


def _public_world(count: int = 16, budget: int = 5, reverse: bool = False):
    names = [f"角色{index:02d}" for index in range(count)]
    ordered = list(reversed(names)) if reverse else names
    scene = SceneState(
        world_objects={"城中": {}},
        actor_states={name: {"location": "城中"} for name in ordered},
        scene_flags={"public_event_attention_budget": budget},
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    entities = {"GameMaster": gm}
    for name in ordered:
        entities[name] = create_agent(name, "居民", "平静", [], agent_runtime="llm")
    return entities, scene, names


def _publish_phase(entities, step: int = 3):
    context = {
        "clock": SimpleNamespace(current_step=step),
        "timeline": {"phase_transition": {"from": "白昼", "to": "夜晚"}},
        "simulation_result": {},
    }
    WorldEventSystem().update(entities, context)
    return context, f"scene-phase:{step}:白昼->夜晚"


def test_public_fact_reaches_everyone_but_only_budgeted_agents_are_interrupted():
    entities, _, names = _public_world(count=16, budget=5)

    context, event_id = _publish_phase(entities)

    assert all(
        entities[name].get_component("Cognition").knows_event(event_id)
        for name in names
    )
    interrupted = {
        name
        for name in names
        if event_id
        in entities[name].get_component("Cognition").pending_world_events
    }
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert len(interrupted) == 5
    assert interrupted == set(witnesses.attention_recipients)
    assert context["world_event_updates"][0]["attention_recipients"] == sorted(
        interrupted
    )


def test_public_attention_selection_is_stable_across_entity_insertion_order():
    first, _, _ = _public_world(count=20, budget=6, reverse=False)
    second, _, _ = _public_world(count=20, budget=6, reverse=True)

    _, first_id = _publish_phase(first, step=7)
    _, second_id = _publish_phase(second, step=7)

    first_recipients = first[f"WorldEvent:{first_id}"].get_component(
        "WorldEventWitnesses"
    ).attention_recipients
    second_recipients = second[f"WorldEvent:{second_id}"].get_component(
        "WorldEventWitnesses"
    ).attention_recipients
    assert first_id == second_id
    assert first_recipients == second_recipients


def test_goal_dependency_precedes_hash_selection_under_public_budget():
    entities, scene, names = _public_world(count=12, budget=1)
    relevant_actor = names[-1]
    goals = entities[relevant_actor].get_component("GoalState")
    goals.goals = goals.from_initial(
        structured=[
            {
                "goal_id": "wait-for-night",
                "title": "等到夜晚行动",
                "completion_conditions": [
                    {
                        "scope": "scene",
                        "target": "scene",
                        "path": "scene_flags.day_phase",
                        "operator": "eq",
                        "value": "夜晚",
                    }
                ],
            }
        ]
    ).goals

    _, event_id = _publish_phase(entities, step=9)

    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert witnesses.attention_recipients == [relevant_actor]
    assert event_id in entities[relevant_actor].get_component(
        "Cognition"
    ).pending_world_events


def test_public_subject_and_local_witness_bypass_zero_general_budget():
    entities, scene, names = _public_world(count=4, budget=0)
    subject = names[2]
    context = {
        "clock": SimpleNamespace(current_step=4),
        "timeline": {
            "attendance_events": [
                {
                    "event_id": "public-subject-event",
                    "kind": "timeline_attendance_missed",
                    "statement": f"{subject}错过了公开集合。",
                    "occurred_step": 4,
                    "location": "",
                    "subjects": [subject],
                    "objects": [],
                    "direct_witnesses": names,
                    "self_witnesses": [subject],
                    "visibility": "public",
                }
            ]
        },
        "simulation_result": {},
    }

    WorldEventSystem().update(entities, context)

    witnesses = entities["WorldEvent:public-subject-event"].get_component(
        "WorldEventWitnesses"
    )
    assert witnesses.attention_recipients == [subject]
    assert all(
        entities[name].get_component("Cognition").knows_event(
            "public-subject-event"
        )
        for name in names
    )


def test_public_event_local_witness_bypasses_zero_general_budget():
    scene = SceneState(
        world_objects={
            "桥头": {"connected_to": []},
            "彼岸": {"connected_to": []},
            "远方": {},
        },
        actor_states={
            "现场者": {"location": "桥头"},
            "远方者": {"location": "远方"},
        },
        scene_flags={"public_event_attention_budget": 0},
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    entities = {
        "GameMaster": gm,
        "现场者": create_agent("现场者", "旅人", "警觉", [], agent_runtime="llm"),
        "远方者": create_agent("远方者", "居民", "平静", [], agent_runtime="llm"),
    }
    context = {
        "clock": SimpleNamespace(current_step=6),
        "topology_changes": [
            {
                "change_id": "public-bridge-open",
                "operation": "connect",
                "source": "桥头",
                "target": "彼岸",
                "bidirectional": True,
                "visibility": "public",
                "occurred_step": 6,
                "changed_arcs": [
                    {"source": "桥头", "target": "彼岸"},
                    {"source": "彼岸", "target": "桥头"},
                ],
                "statement": "桥头与彼岸之间的通路已经开放。",
            }
        ],
        "simulation_result": {},
    }

    WorldEventSystem().update(entities, context)

    event_id = "topology:public-bridge-open"
    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert witnesses.attention_recipients == ["现场者"]
    assert all(
        entities[name].get_component("Cognition").knows_event(event_id)
        for name in ("现场者", "远方者")
    )
    assert event_id not in entities["远方者"].get_component(
        "Cognition"
    ).pending_world_events


def test_dormant_actor_learns_public_fact_without_consuming_attention_budget():
    entities, _, names = _public_world(count=8, budget=3)
    dormant = names[0]
    entities[dormant].get_component("AgentController").activation_policy = "dormant"

    _, event_id = _publish_phase(entities, step=12)

    witnesses = entities[f"WorldEvent:{event_id}"].get_component(
        "WorldEventWitnesses"
    )
    assert entities[dormant].get_component("Cognition").knows_event(event_id)
    assert dormant not in witnesses.attention_recipients
    assert len(witnesses.attention_recipients) == 3


def test_semantic_state_updates_cannot_rewrite_public_attention_budget():
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={},
        scene_flags={"public_event_attention_budget": 4},
    )
    result = {
        "state_updates": {
            "scene": {"public_event_attention_budget": 64},
            "world_objects": {},
            "actor_states": {},
        },
        "resolved_actions": [],
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
    )

    assert outcome.committed is False
    assert any("engine-managed flags" in error for error in outcome.errors)
    assert scene.get_scene_flag("public_event_attention_budget") == 4
