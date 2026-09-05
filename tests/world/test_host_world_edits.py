from types import SimpleNamespace

from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.runner import Runner
from src.story_engine.environment.world_edits import HostWorldEditTransaction
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.world_events import WorldEventSystem


def _scene() -> SceneState:
    return SceneState(
        world_objects={
            "大厅": {"lighting": "昏暗"},
            "远处": {},
            "灯": {
                "is_location": False,
                "location": "大厅",
                "owner": None,
                "container": None,
                "hidden": False,
                "portable": False,
                "powered": False,
            },
            "暗格机关": {
                "is_location": False,
                "location": "大厅",
                "owner": None,
                "container": None,
                "hidden": True,
                "portable": False,
                "condition": "闭合",
            },
        },
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "远处"},
        },
        scene_flags={"world_version": 2},
    )


def test_host_world_edit_commits_an_authoritative_stable_diff():
    scene = _scene()

    outcome = HostWorldEditTransaction().apply(
        scene,
        [("灯", {"powered": True, "color": "暖黄"})],
        current_step=4,
    )

    assert outcome.committed is True
    assert outcome.errors == []
    assert scene.get_object_state("灯")["powered"] is True
    assert scene.get_object_state("灯")["color"] == "暖黄"
    assert scene.get_scene_flag("world_version") == 3
    assert outcome.changes == [
        {
            "change_id": "host-world-edit:4:0:灯",
            "object_id": "灯",
            "paths": ["color", "powered"],
            "location": "大厅",
            "source_actors": [],
            "visibility": "local",
            "occurred_step": 4,
            "source_type": "host_world_edit",
            "statement": "“灯”的可观察状态发生了变化（color、powered）。",
        }
    ]


def test_noop_world_edit_creates_no_version_or_fact():
    scene = _scene()

    outcome = HostWorldEditTransaction().apply(
        scene,
        [("灯", {"powered": False})],
        current_step=4,
    )

    assert outcome == outcome.__class__(True)
    assert scene.get_scene_flag("world_version") == 2


def test_invalid_world_edit_batch_rolls_back_valid_siblings():
    scene = _scene()
    before = scene.model_dump()

    outcome = HostWorldEditTransaction().apply(
        scene,
        [
            ("灯", {"powered": True}),
            ("大厅", {"connected_to": ["远处"]}),
        ],
        current_step=4,
    )

    assert outcome.committed is False
    assert "explicit host lifecycle/topology API" in outcome.errors[0]
    assert scene.model_dump() == before


def test_duplicate_object_edits_have_no_implicit_order_semantics():
    scene = _scene()

    outcome = HostWorldEditTransaction().apply(
        scene,
        [("灯", {"powered": True}), ("灯", {"powered": False})],
        current_step=4,
    )

    assert outcome.committed is False
    assert any("duplicates object" in error for error in outcome.errors)
    assert scene.get_object_state("灯")["powered"] is False


def test_world_edit_rejects_non_replayable_state_values():
    scene = _scene()
    before = scene.model_dump()

    outcome = HostWorldEditTransaction().apply(
        scene,
        [("灯", {"invalid": {"unordered"}})],
        current_step=4,
    )

    assert outcome.committed is False
    assert any("strict JSON state values" in error for error in outcome.errors)
    assert scene.model_dump() == before


def test_host_world_edit_ledger_is_deterministic_for_replay():
    first = _scene()
    second = _scene()
    edits = [("灯", {"powered": True})]

    first_result = HostWorldEditTransaction().apply(first, edits, current_step=9)
    second_result = HostWorldEditTransaction().apply(second, edits, current_step=9)

    assert first_result == second_result
    assert first.model_dump() == second.model_dump()


def test_host_world_edit_becomes_local_event_and_reactivates_dependent_goal():
    scene = _scene()
    outcome = HostWorldEditTransaction().apply(
        scene,
        [("灯", {"powered": True})],
        current_step=4,
    )
    gm = Entity("WorldHost")
    gm.add_component(scene)
    entities = {
        "WorldHost": gm,
        "甲": create_agent("甲", "调查者", "谨慎", [], agent_runtime="llm"),
        "乙": create_agent("乙", "远方居民", "平静", [], agent_runtime="llm"),
    }
    goals = entities["甲"].get_component("GoalState")
    adopted, error = goals.adopt_agent_goal(
        title="确认灯已经亮起",
        description="检查大厅的照明",
        source_kind="world_event",
        source_ref="earlier-darkness",
        priority=0.7,
        step=1,
        completion_conditions=[
            {
                "scope": "world_object",
                "target": "灯",
                "path": "powered",
                "operator": "eq",
                "value": True,
            }
        ],
    )
    assert adopted is not None and error == ""
    controller = entities["甲"].get_component("AgentController")
    controller.repeated_goal_action_count = 4
    context = {
        "clock": SimpleNamespace(current_step=4),
        "host_object_state_changes": outcome.changes,
        "simulation_result": {},
    }

    WorldEventSystem().update(entities, context)

    event_id = "object-state:host-world-edit:4:0:灯"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    assert fact.kind == "object_state_changed"
    assert [(impact.target, impact.path) for impact in fact.impacts] == [
        ("灯", "powered")
    ]
    assert entities["甲"].get_component("Cognition").knows_event(event_id)
    assert not entities["乙"].get_component("Cognition").knows_event(event_id)
    assert controller.repeated_goal_action_count == 0
    assert context["goal_reactivations"][0]["match_basis"] == "state_dependency"
    assert context["simulation_result"]["host_object_state_changes"] == (
        outcome.changes
    )


def test_hidden_host_world_edit_changes_truth_without_leaking_observation():
    scene = _scene()
    outcome = HostWorldEditTransaction().apply(
        scene,
        [("暗格机关", {"condition": "松开"})],
        current_step=5,
    )
    gm = Entity("WorldHost")
    gm.add_component(scene)
    entities = {
        "WorldHost": gm,
        "甲": create_agent("甲", "调查者", "谨慎", [], agent_runtime="llm"),
        "乙": create_agent("乙", "远方居民", "平静", [], agent_runtime="llm"),
    }
    context = {
        "clock": SimpleNamespace(current_step=5),
        "host_object_state_changes": outcome.changes,
        "simulation_result": {},
    }

    WorldEventSystem().update(entities, context)
    visible = RenderingSystem()._build_visible_simulation(
        context["simulation_result"],
        {"location": "大厅"},
    )

    event_id = "object-state:host-world-edit:5:0:暗格机关"
    assert f"WorldEvent:{event_id}" in entities
    assert not entities["甲"].get_component("Cognition").knows_event(event_id)
    assert visible["host_object_state_changes"] == []


def test_runner_publishes_committed_host_world_edit_without_raw_patch_leakage():
    scene = _scene()
    gm = Entity("WorldHost")
    gm.add_component(scene)
    runner = Runner(random_seed="host-edit-runner")
    runner.add_entity(gm)
    for name in scene.actor_states:
        actor = create_agent(
            name,
            "观察者",
            "谨慎",
            [],
        agent_runtime="llm")
        actor.get_component("AgentController").autonomous = False
        runner.add_entity(actor)
        runner.agent_registry.register(actor, object())

    context = runner.run_step(world_edits=[("灯", {"powered": True})])

    assert context["world_edit_transaction"] == {"committed": True, "errors": []}
    assert context["host_object_state_changes"][0]["paths"] == ["powered"]
    assert "WorldEvent:object-state:host-world-edit:0:0:灯" in runner.entities
    assert "powered" not in context["host_object_state_changes"][0]  # no raw values
