from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.goal_state import GoalState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.evaluation import EpisodeRunner
from src.story_engine.systems.input import InputSystem


def test_a_committed_action_traces_back_to_the_reason_its_character_gave():
    context = {
        "clock": type("Clock", (), {"current_step": 7})(),
        "state_transaction": {"committed": True},
        "agent_motive_refs": {
            "甲": [
                {"kind": "goal", "ref": "seek-redress"},
                {"kind": "sentiment", "ref": "乙:betrayed"},
            ]
        },
    }
    actions = [
        {
            "actor": "甲",
            "action_kind": "communicate",
            "action_target": "乙",
            "result": "甲向乙提出追责。",
        }
    ]

    handoffs = EpisodeRunner._motive_causal_handoffs(context, actions)

    assert handoffs == [
        "resolved_action:step:7:actor:甲<-goal:甲:seek-redress",
        "resolved_action:step:7:actor:甲<-sentiment:甲:乙:betrayed",
    ]


def test_a_reason_is_ignored_for_anyone_whose_action_did_not_resolve():
    context = {
        "clock": type("Clock", (), {"current_step": 3})(),
        "state_transaction": {"committed": True},
        "agent_motive_refs": {"乙": [{"kind": "goal", "ref": "stay-hidden"}]},
    }

    handoffs = EpisodeRunner._motive_causal_handoffs(
        context, [{"actor": "甲", "action_kind": "wait"}]
    )

    assert handoffs == []


def test_nothing_is_attributed_when_the_step_never_committed():
    context = {
        "clock": type("Clock", (), {"current_step": 4})(),
        "state_transaction": {"committed": False},
        "agent_motive_refs": {"甲": [{"kind": "goal", "ref": "seek-redress"}]},
    }

    handoffs = EpisodeRunner._motive_causal_handoffs(
        context, [{"actor": "甲", "action_kind": "wait"}]
    )

    assert handoffs == []


def test_a_slow_action_keeps_the_reason_it_was_started_for():
    context = {
        "clock": type("Clock", (), {"current_step": 9})(),
        "state_transaction": {"committed": True},
        "completed_action_motive_refs": {
            "甲": {
                "event_id": "action:1",
                "motive_refs": [{"kind": "goal", "ref": "reach-hall"}],
            }
        },
        # She has since turned her attention elsewhere; the walk that just
        # finished was not started for this.
        "agent_motive_refs": {"甲": [{"kind": "sentiment", "ref": "乙:angry"}]},
    }

    handoffs = EpisodeRunner._motive_causal_handoffs(
        context,
        [{"actor": "甲", "action_kind": "move", "action_target": "走廊"}],
    )

    assert handoffs == ["resolved_action:step:9:actor:甲<-goal:甲:reach-hall"]


def _deciding_entity(*, goal_ids=(), motive_refs=()):
    entity = Entity("甲")
    entity.add_component(AgentController(runtime="hermes"))
    if goal_ids:
        entity.add_component(
            GoalState(
                goals={
                    goal_id: {
                        "goal_id": goal_id,
                        "title": goal_id,
                        "status": "active",
                    }
                    for goal_id in goal_ids
                }
            )
        )

    class _Runtime:
        def decide(self, _entity, _perception):
            return AgentDecision(
                action="向乙提出追责。",
                metadata={
                    "subject_runtime": True,
                    "motive_refs": [dict(item) for item in motive_refs],
                },
            )

    registry = AgentRegistry()
    registry.register(entity, _Runtime())
    return entity, registry


def _run_input(entity, registry):
    class SimulationControl(Component):
        pass

    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"大厅": {}},
            actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
        )
    )
    context = {
        "agent_registry": registry,
        "overrides": {},
        "clock": type("Clock", (), {"current_step": 1})(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }
    InputSystem().update({"WorldHost": gm, "甲": entity}, context)
    return context


def test_a_motive_is_recorded_only_when_she_actually_holds_that_goal():
    entity, registry = _deciding_entity(
        goal_ids=("seek-redress",),
        motive_refs=({"kind": "goal", "ref": "seek-redress"},),
    )

    context = _run_input(entity, registry)

    assert context["agent_motive_refs"] == {
        "甲": [{"kind": "goal", "ref": "seek-redress"}]
    }
    assert context.get("agent_motive_ref_rejections", []) == []
    assert EpisodeRunner._goal_engaged_actors(context) == ["甲"]


def test_a_goal_she_does_not_hold_is_rejected_rather_than_believed():
    entity, registry = _deciding_entity(
        goal_ids=("seek-redress",),
        motive_refs=({"kind": "goal", "ref": "avenge-my-brother"},),
    )

    context = _run_input(entity, registry)

    assert context.get("agent_motive_refs", {}) == {}
    assert context["agent_motive_ref_rejections"] == [
        {"actor": "甲", "kind": "goal", "ref": "avenge-my-brother"}
    ]
    assert EpisodeRunner._goal_engaged_actors(context) == []


def test_an_unknown_motive_kind_cannot_smuggle_in_a_new_audit_category():
    entity, registry = _deciding_entity(
        motive_refs=({"kind": "destiny", "ref": "the-prophecy"},),
    )

    context = _run_input(entity, registry)

    assert context.get("agent_motive_refs", {}) == {}
    assert context["agent_motive_ref_rejections"] == [
        {"actor": "甲", "kind": "destiny", "ref": "the-prophecy"}
    ]
