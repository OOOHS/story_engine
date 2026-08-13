from src.story_engine.agents.scheduler import AgentScheduler
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.motivation import ObligationConflictAnalyzer
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.obligations import ObligationSystem
from src.story_engine.systems.rendering import RenderingSystem


class SimulationControl(Component):
    pass


def _scene(actor_location="起点"):
    return SceneState(
        world_objects={
            "起点": {"connected_to": ["北站"]},
            "北站": {"connected_to": ["起点", "南站"]},
            "南站": {"connected_to": ["北站"]},
            "孤岛": {"connected_to": []},
            "包裹": {
                "is_location": False,
                "kind": "parcel",
                "owner": "信使",
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={
            "信使": {"location": actor_location},
            "北站收件人": {"location": "北站"},
            "南站收件人": {"location": "南站"},
        },
    )


def _location_obligation(obligation_id, title, location, due_step):
    return {
        "obligation_id": obligation_id,
        "title": title,
        "due_step": due_step,
        "wake_before_steps": 1,
        "completion_conditions": [
            {
                "scope": "actor",
                "target": "信使",
                "path": "location",
                "operator": "eq",
                "value": location,
            }
        ],
    }


def _state(*items):
    return ObligationState.from_initial(items)


def _analyze(scene, state, step=0):
    return ObligationConflictAnalyzer().analyze(
        state,
        actor_name="信使",
        scene_state=scene,
        plot_state=PlotState(),
        current_step=step,
    )


def test_deadlines_make_two_reachable_locations_a_hard_conflict():
    scene = _scene()
    state = _state(
        _location_obligation("north", "去北站", "北站", 1),
        _location_obligation("south", "去南站", "南站", 1),
    )

    conflicts = _analyze(scene, state)

    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == "hard"
    assert conflicts[0]["reason_code"] == "deadline_collision"
    assert conflicts[0]["choice_required"] is True
    assert conflicts[0]["feasible_orders"] == []


def test_one_feasible_order_is_reported_without_inventing_a_forced_action():
    scene = _scene()
    state = _state(
        _location_obligation("north", "先去北站", "北站", 1),
        _location_obligation("south", "再去南站", "南站", 3),
    )

    conflicts = _analyze(scene, state)

    assert conflicts[0]["severity"] == "constrained"
    assert conflicts[0]["reason_code"] == "forced_order"
    assert conflicts[0]["choice_required"] is False
    assert [
        item["order"] for item in conflicts[0]["feasible_orders"]
    ] == [["north", "south"]]


def test_sufficient_time_for_both_orders_is_not_called_a_conflict():
    scene = _scene()
    state = _state(
        _location_obligation("north", "去北站", "北站", 3),
        _location_obligation("south", "去南站", "南站", 3),
    )

    assert _analyze(scene, state) == []


def test_conflict_result_is_independent_of_obligation_insertion_order():
    scene = _scene()
    north = _location_obligation("north", "去北站", "北站", 1)
    south = _location_obligation("south", "去南站", "南站", 1)

    forward = _analyze(scene, _state(north, south))
    reverse = _analyze(scene, _state(south, north))

    assert forward == reverse


def test_already_satisfied_or_terminal_obligations_do_not_create_false_conflicts():
    scene = _scene()
    state = _state(
        _location_obligation("home", "留在起点", "起点", 0),
        _location_obligation("south", "去南站", "南站", 1),
    )

    assert _analyze(scene, state) == []
    state.obligations["home"].status = "cancelled"
    assert _analyze(scene, state) == []


def test_disconnected_location_is_an_explicit_route_conflict():
    scene = _scene()
    state = _state(
        _location_obligation("north", "去北站", "北站", 5),
        _location_obligation("island", "去孤岛", "孤岛", 5),
    )

    conflict = _analyze(scene, state)[0]

    assert conflict["severity"] == "hard"
    assert conflict["reason_code"] == "unreachable_route"


def test_object_delivery_locations_participate_in_schedule_conflicts():
    scene = _scene()
    state = _state(
        {
            "obligation_id": "leave_north",
            "title": "把包裹留在北站",
            "due_step": 1,
            "completion_conditions": [
                {
                    "scope": "world_object",
                    "target": "包裹",
                    "path": "location",
                    "operator": "eq",
                    "value": "北站",
                }
            ],
        },
        {
            "obligation_id": "leave_south",
            "title": "把包裹留在南站",
            "due_step": 1,
            "completion_conditions": [
                {
                    "scope": "world_object",
                    "target": "包裹",
                    "path": "location",
                    "operator": "eq",
                    "value": "南站",
                }
            ],
        },
    )

    conflict = _analyze(scene, state)[0]

    assert conflict["required_locations"] == {
        "leave_north": "北站",
        "leave_south": "南站",
    }


def test_only_debtor_receives_private_conflict_analysis():
    scene = _scene()
    messenger = create_agent(
        "信使",
        "信使",
        "谨慎",
        [],
        initial_obligations=[
            _location_obligation("north", "去北站", "北站", 1),
            _location_obligation("south", "去南站", "南站", 1),
        ],
    )
    bystander = create_agent("北站收件人", "收件人", "平静", [])

    messenger_perception = InputSystem().build_agent_perception(
        messenger,
        scene,
        [],
        {"clock": type("Clock", (), {"current_step": 0})()},
    )
    bystander_perception = InputSystem().build_agent_perception(
        bystander,
        scene,
        [],
        {"clock": type("Clock", (), {"current_step": 0})()},
    )

    assert messenger_perception.private_obligations["conflict_count"] == 1
    assert messenger_perception.private_obligations["conflicts"][0]["severity"] == "hard"
    assert bystander_perception.private_obligations["conflict_count"] == 0
    assert "obligations" not in messenger_perception.world_view


def test_near_conflict_wakes_background_agent_but_dormant_policy_still_wins():
    scene = _scene()
    messenger = create_agent(
        "信使",
        "信使",
        "谨慎",
        [],
        activation_policy="background",
        background_interval=99,
        initial_obligations=[
            _location_obligation("north", "去北站", "北站", 1),
            _location_obligation("south", "去南站", "南站", 1),
        ],
    )
    dormant = create_agent(
        "休眠信使",
        "信使",
        "谨慎",
        [],
        activation_policy="dormant",
        initial_obligations=[
            {
                **_location_obligation("north", "去北站", "北站", 1),
                "completion_conditions": [
                    {
                        "scope": "actor",
                        "target": "休眠信使",
                        "path": "location",
                        "operator": "eq",
                        "value": "北站",
                    }
                ],
            },
            {
                **_location_obligation("south", "去南站", "南站", 1),
                "completion_conditions": [
                    {
                        "scope": "actor",
                        "target": "休眠信使",
                        "path": "location",
                        "operator": "eq",
                        "value": "南站",
                    }
                ],
            },
        ],
    )
    scene.actor_states["休眠信使"] = {"location": "起点"}
    scheduler = AgentScheduler()

    activation = scheduler.activation_for(
        messenger,
        step=0,
        actor_location="起点",
        player_location="远方",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
        plot_state=PlotState(),
    )
    dormant_activation = scheduler.activation_for(
        dormant,
        step=0,
        actor_location="起点",
        player_location="远方",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
        plot_state=PlotState(),
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "obligation_conflict:north:south"
    assert dormant_activation.active is False
    assert dormant_activation.reason == "policy_dormant"


def test_far_conflict_does_not_force_background_inference_before_horizon():
    scene = _scene()
    messenger = create_agent(
        "信使",
        "信使",
        "谨慎",
        [],
        activation_policy="background",
        initial_obligations=[
            _location_obligation("north", "去北站", "北站", 20),
            _location_obligation("island", "去孤岛", "孤岛", 20),
        ],
    )

    reason = AgentScheduler()._urgent_obligation_conflict(
        messenger,
        0,
        scene_state=scene,
        plot_state=PlotState(),
    )

    assert reason == ""


def test_model_cannot_rewrite_engine_conflict_wakeup_horizon():
    scene = _scene()
    result = {
        "resolved_actions": [],
        "state_updates": {
            "scene": {
                "obligation_conflict_horizon": 50,
                "agent_goal_wakeup_interval": 1,
                "agent_open_goal_review_interval": 4,
            },
            "world_objects": {},
            "actor_states": {},
        },
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
    )

    assert outcome.committed is False
    assert scene.get_scene_flag("obligation_conflict_horizon") is None
    assert scene.get_scene_flag("agent_goal_wakeup_interval") is None
    assert scene.get_scene_flag("agent_open_goal_review_interval") is None
    assert any("engine-managed flags" in error for error in outcome.errors)


def test_conflict_refresh_disappears_after_authoritative_fulfillment():
    scene = _scene(actor_location="北站")
    state = _state(
        _location_obligation("north", "去北站", "北站", 1),
        _location_obligation("south", "去南站", "南站", 1),
    )
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(scene)
    gm.add_component(PlotState())
    messenger = Entity("信使")
    messenger.add_component(state)
    context = {"clock": type("Clock", (), {"current_step": 0})()}

    ObligationSystem().update({"GameMaster": gm, "信使": messenger}, context)

    assert state.obligations["north"].status == "fulfilled"
    assert context["obligation_conflicts"] == {}


def test_rendering_drops_any_forged_private_conflict_payload():
    scene = _scene()
    result = {
        "resolved_actions": [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "object_lifecycle": [],
        "obligation_conflicts": {
            "信使": [{"obligation_ids": ["north", "south"]}]
        },
    }

    visible = RenderingSystem()._build_visible_simulation(
        result,
        scene.get_view_pov("北站收件人"),
        visible_locations=["北站"],
    )

    assert visible["obligation_conflicts"] == {}
