from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.motivation import ObligationConflictAnalyzer
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return self.scripted_result


def _scene():
    return SceneState(
        world_objects={
            "工坊": {"connected_to": ["北站", "南站"]},
            "北站": {"connected_to": ["工坊"]},
            "南站": {"connected_to": ["工坊"]},
            "公开信": {
                "is_location": False,
                "kind": "letter",
                "location": "工坊",
                "owner": None,
                "hidden": False,
                "portable": True,
            },
            "密信": {
                "is_location": False,
                "kind": "letter",
                "location": "工坊",
                "owner": None,
                "hidden": True,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "工坊"},
            "乙": {"location": "工坊"},
            "委托人": {"location": "工坊"},
        },
    )


def _drive(need="责任"):
    return DriveState.from_initial([{"name": need, "pressure": 0.2}])


def _empty_states():
    return {"甲": ObligationState(), "乙": ObligationState()}


def _base_result(actions=None, updates=None):
    return {
        "resolved_actions": actions or [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "drive_updates": [],
        "obligation_updates": updates or [],
        "tension_delta": 0,
    }


def _action(actor, result="接受安排"):
    return {
        "actor": actor,
        "outcome": "success",
        "location": "工坊",
        "visibility": "public",
        "result": result,
    }


def _create_update(obligation_id, location, due_step=3):
    return {
        "operation": "create",
        "actor": "甲",
        "source": "甲",
        "obligation_id": obligation_id,
        "title": f"前往{location}",
        "summary": f"在期限前抵达{location}",
        "due_step": due_step,
        "reason": "甲当面接受了这项责任",
        "completion_conditions": [
            {
                "scope": "actor",
                "target": "甲",
                "path": "location",
                "operator": "eq",
                "value": location,
            }
        ],
    }


def _initial_obligation(*, due_step=3):
    return ObligationState.from_initial(
        [
            {
                "obligation_id": "deliver",
                "title": "前往北站交付",
                "summary": "把责任带到北站",
                "creditor": "委托人",
                "due_step": due_step,
                "grace_steps": 1,
                "wake_before_steps": 1,
                "pressure_need": "责任",
                "delegation_policy": "bilateral",
                "completion_conditions": [
                    {
                        "scope": "actor",
                        "target": "甲",
                        "path": "location",
                        "operator": "eq",
                        "value": "北站",
                    }
                ],
            }
        ]
    )


def test_dynamic_obligation_can_have_authoritative_completion_condition():
    scene = _scene()
    states = _empty_states()
    result = _base_result(
        actions=[_action("甲")],
        updates=[_create_update("go_north", "北站")],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲"},
    )

    assert outcome.committed is True
    record = states["甲"].obligations["go_north"]
    assert record.completion_conditions[0]["target"] == "甲"
    assert record.completion_conditions[0]["value"] == "北站"

    scene.actor_states["甲"]["location"] = "北站"
    transitions = states["甲"].advance_to(1, scene_state=scene)
    assert transitions == [{"obligation_id": "go_north", "status": "fulfilled"}]


def test_dynamic_condition_can_track_a_currently_visible_object_delivery():
    scene = _scene()
    states = _empty_states()
    update = _create_update("deliver_letter", "北站")
    update["completion_conditions"] = [
        {
            "scope": "world_object",
            "target": "公开信",
            "path": "owner",
            "operator": "eq",
            "value": "乙",
        }
    ]

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _base_result(actions=[_action("甲")], updates=[update]),
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲"},
    )
    scene.get_object_state("公开信").update({"owner": "乙", "location": None})
    transitions = states["甲"].advance_to(1, scene_state=scene)

    assert outcome.committed is True
    assert transitions == [
        {"obligation_id": "deliver_letter", "status": "fulfilled"}
    ]


def test_dynamic_conditions_cannot_reference_hidden_objects_or_plot_state():
    scene = _scene()
    states = _empty_states()
    hidden = _create_update("expose_secret", "北站")
    hidden["completion_conditions"] = [
        {
            "scope": "world_object",
            "target": "密信",
            "path": "owner",
            "operator": "eq",
            "value": "甲",
        }
    ]
    plot = _create_update("advance_plot", "北站")
    plot["completion_conditions"] = [
        {
            "scope": "plot",
            "target": "secret_plot",
            "path": "clock",
            "operator": "gte",
            "value": 1,
        }
    ]

    hidden_outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _base_result(actions=[_action("甲")], updates=[hidden]),
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲"},
    )
    plot_outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _base_result(actions=[_action("甲")], updates=[plot]),
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲"},
    )

    assert hidden_outcome.committed is False
    assert plot_outcome.committed is False
    assert states["甲"].obligations == {}
    assert any("not visible to debtor" in error for error in hidden_outcome.errors)
    assert any("unsupported dynamic scope" in error for error in plot_outcome.errors)


def test_runtime_created_obligations_can_generate_real_conflict():
    scene = _scene()
    states = _empty_states()
    result = _base_result(
        actions=[_action("甲", "甲同时答应了两项差事")],
        updates=[
            _create_update("north", "北站", due_step=1),
            _create_update("south", "南站", due_step=1),
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲"},
    )
    conflicts = ObligationConflictAnalyzer().analyze(
        states["甲"],
        actor_name="甲",
        scene_state=scene,
        current_step=0,
    )

    assert outcome.committed is True
    assert conflicts[0]["severity"] == "hard"
    assert conflicts[0]["obligation_ids"] == ["north", "south"]


def test_delegation_moves_active_duty_with_lineage_and_rewrites_debtor_condition():
    scene = _scene()
    states = {"甲": _initial_obligation(), "乙": ObligationState()}
    drives = {"甲": _drive(), "乙": _drive()}
    result = _base_result(
        actions=[
            _action("甲", "甲请求乙接手北站交付"),
            _action("乙", "乙明确同意接手"),
        ],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "delegate_pressure_need": "责任",
                "reason": "甲与乙当面约定由乙接手",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states=drives,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is True
    old = states["甲"].obligations["deliver"]
    transferred = states["乙"].obligations["deliver"]
    assert old.status == "delegated"
    assert old.delegated_to == "乙"
    assert transferred.status == "scheduled"
    assert transferred.delegated_from == "甲"
    assert transferred.delegation_reason == "甲与乙当面约定由乙接手"
    assert transferred.completion_conditions[0]["target"] == "乙"
    assert transferred.due_step == old.due_step
    assert transferred.grace_steps == old.grace_steps
    assert transferred.creditor == "委托人"

    scene.actor_states["乙"]["location"] = "北站"
    transitions = states["乙"].advance_to(2, scene_state=scene)
    assert transitions == [{"obligation_id": "deliver", "status": "fulfilled"}]
    assert states["甲"].obligations["deliver"].status == "delegated"


def test_delegation_requires_positive_observable_actions_from_both_parties():
    scene = _scene()
    states = {"甲": _initial_obligation(), "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲", "甲单方面宣布转交")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "乙没有作出接受行动",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert states["甲"].obligations["deliver"].status == "scheduled"
    assert states["乙"].obligations == {}
    assert any("resolved action from 乙" in error for error in outcome.errors)


def test_forbidden_obligation_cannot_be_delegated_even_with_bilateral_consent():
    scene = _scene()
    original = _initial_obligation()
    original.obligations["deliver"].delegation_policy = "forbidden"
    states = {"甲": original, "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "双方试图转交不可转交的私人责任",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert states["乙"].obligations == {}
    assert any("forbids delegation" in error for error in outcome.errors)


def test_creditor_consent_policy_requires_creditor_proposal_action_and_approval():
    scene = _scene()
    original = _initial_obligation()
    original.obligations["deliver"].delegation_policy = "creditor_consent"
    states = {"甲": original, "乙": ObligationState()}
    update = {
        "operation": "delegate",
        "actor": "甲",
        "source": "甲",
        "obligation_id": "deliver",
        "delegate": "乙",
        "accepted_by": "乙",
        "reason": "债权人批准乙接手",
    }
    missing = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _base_result(actions=[_action("甲"), _action("乙")], updates=[update]),
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    approved_update = {**update, "approved_by": "委托人"}
    approved = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _base_result(
            actions=[_action("甲"), _action("乙"), _action("委托人", "委托人批准")],
            updates=[approved_update],
        ),
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙", "委托人"},
    )

    assert missing.committed is False
    assert any("approved_by must equal creditor" in error for error in missing.errors)
    assert approved.committed is True
    assert states["乙"].obligations["deliver"].creditor == "委托人"


def test_delegation_cannot_leak_hidden_completion_object_to_new_debtor():
    scene = _scene()
    hidden_duty = ObligationState.from_initial(
        [
            {
                "obligation_id": "hide_secret",
                "title": "处理密信",
                "due_step": 3,
                "delegation_policy": "bilateral",
                "completion_conditions": [
                    {
                        "scope": "world_object",
                        "target": "密信",
                        "path": "owner",
                        "operator": "eq",
                        "value": "委托人",
                    }
                ],
            }
        ]
    )
    states = {"甲": hidden_duty, "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "hide_secret",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "甲没有先向乙展示密信",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert states["乙"].obligations == {}
    assert any("not visible to delegate" in error for error in outcome.errors)


def test_resolver_cannot_invent_delegate_consent_without_agent_proposal():
    scene = _scene()
    states = {"甲": _initial_obligation(), "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲"), _action("乙", "结算器声称乙接受")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "乙本轮没有提交任何 proposal",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert states["乙"].obligations == {}
    assert any("current-turn proposal from 乙" in error for error in outcome.errors)


def test_remote_or_hidden_acceptance_cannot_transfer_private_duty():
    scene = _scene()
    scene.actor_states["乙"]["location"] = "南站"
    states = {"甲": _initial_obligation(), "乙": ObligationState()}
    result = _base_result(
        actions=[
            _action("甲"),
            {
                **_action("乙"),
                "location": "南站",
                "visibility": "hidden",
            },
        ],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "两人并未真正会面",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert states["乙"].obligations == {}
    assert any("co-located" in error for error in outcome.errors)


def test_delegation_checkpoint_restores_both_obligation_components():
    scene = _scene()
    states = {"甲": _initial_obligation(), "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "双方同意转交",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=1,
        proposal_actors={"甲", "乙"},
    )
    outcome.checkpoint.restore()

    assert states["甲"].obligations["deliver"].status == "scheduled"
    assert states["甲"].obligations["deliver"].delegated_to is None
    assert states["乙"].obligations == {}


def test_delegation_can_resolve_one_actors_schedule_conflict():
    scene = _scene()
    states = {
        "甲": ObligationState.from_initial(
            [
                {
                    **_create_update("north", "北站", due_step=1),
                    "title": "去北站",
                },
                {
                    **_create_update("south", "南站", due_step=1),
                    "title": "去南站",
                },
            ]
        ),
        "乙": ObligationState(),
    }
    before = ObligationConflictAnalyzer().analyze(
        states["甲"], actor_name="甲", scene_state=scene, current_step=0
    )
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "south",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "乙接手南站任务",
            }
        ],
    )

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲", "乙"},
    )
    after_alice = ObligationConflictAnalyzer().analyze(
        states["甲"], actor_name="甲", scene_state=scene, current_step=0
    )
    after_bob = ObligationConflictAnalyzer().analyze(
        states["乙"], actor_name="乙", scene_state=scene, current_step=0
    )

    assert before and before[0]["severity"] == "hard"
    assert outcome.committed is True
    assert after_alice == []
    assert after_bob == []
    assert states["乙"].obligations["south"].completion_conditions[0]["target"] == "乙"
    assert states["乙"].obligations["south"].source_kind == "delegated_obligation"
    assert states["乙"].obligations["south"].source_ref == "甲:south"


def test_delegated_history_never_becomes_breached_again():
    scene = _scene()
    states = {"甲": _initial_obligation(due_step=1), "乙": ObligationState()}
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "双方同意转交",
            }
        ],
    )
    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=states,
        current_step=0,
        proposal_actors={"甲", "乙"},
    )

    transitions = states["甲"].advance_to(10, scene_state=scene)

    assert outcome.committed is True
    assert transitions == []
    assert states["甲"].obligations["deliver"].status == "delegated"
    assert states["甲"].get_private_snapshot(10)["recent_history"][0]["status"] == "delegated"


def test_simulation_system_passes_current_agent_proposals_into_delegation_boundary():
    scene = _scene()
    result = _base_result(
        actions=[_action("甲"), _action("乙")],
        updates=[
            {
                "operation": "delegate",
                "actor": "甲",
                "source": "甲",
                "obligation_id": "deliver",
                "delegate": "乙",
                "accepted_by": "乙",
                "reason": "双方各自提出并确认了转交",
            }
        ],
    )
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    alice = Entity("甲")
    alice.add_component(_initial_obligation())
    bob = Entity("乙")
    bob.add_component(ObligationState())
    context = {
        "clock": type("Clock", (), {"current_step": 1})(),
        "intents": [
            {"actor": "甲", "intent": "请求乙接手"},
            {"actor": "乙", "intent": "同意接手"},
        ],
    }

    SimulationSystem().update(
        {"GameMaster": gm, "甲": alice, "乙": bob},
        context,
    )

    assert context["state_transaction"]["committed"] is True
    assert alice.get_component("ObligationState").obligations["deliver"].status == "delegated"
    assert bob.get_component("ObligationState").obligations["deliver"].delegated_from == "甲"
