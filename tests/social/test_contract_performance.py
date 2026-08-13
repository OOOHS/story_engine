from src.story_engine.components.contract_state import ContractState
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction


def _scene():
    return SceneState(
        world_objects={
            "工坊": {},
            "甲的报酬": {
                "is_location": False,
                "kind": "payment",
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "乙的交付物": {
                "is_location": False,
                "kind": "deliverable",
                "owner": "乙",
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "工坊"},
            "乙": {"location": "工坊"},
            "丙": {"location": "工坊"},
            "旁观者": {"location": "工坊"},
        },
    )


def _action(actor, text="明确回应服务契约"):
    return {
        "actor": actor,
        "outcome": "success",
        "location": "工坊",
        "visibility": "public",
        "result": text,
    }


def _result(actions=None, contract_updates=None, obligation_updates=None, lifecycle=None):
    return {
        "resolved_actions": actions or [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": lifecycle or [],
        "exchanges": [],
        "contract_updates": contract_updates or [],
        "drive_updates": [],
        "obligation_updates": obligation_updates or [],
        "tension_delta": 0,
    }


def _service_offer(due_after_steps=2):
    return {
        "operation": "propose",
        "contract_id": "pay_then_deliver",
        "actor": "甲",
        "parties": ["甲", "乙"],
        "title": "先付款，后交付",
        "summary": "甲先给报酬，乙随后交付物品",
        "expires_step": 3,
        "transfers": [
            {"from": "甲", "to": "乙", "object_id": "甲的报酬"}
        ],
        "delegations": [],
        "services": [
            {
                "actor": "乙",
                "creditor": "甲",
                "obligation_id": "deliver_after_payment",
                "title": "收款后交付物品",
                "summary": "把乙的交付物交给甲",
                "due_after_steps": due_after_steps,
                "grace_steps": 0,
                "wake_before_steps": 1,
                "delegation_policy": "bilateral",
                "completion_conditions": [
                    {
                        "scope": "world_object",
                        "target": "乙的交付物",
                        "path": "owner",
                        "operator": "eq",
                        "value": "甲",
                    }
                ],
            }
        ],
        "reason": "甲当面提出先付款后交付的条款",
    }


def _accept():
    return {
        "operation": "accept",
        "contract_id": "pay_then_deliver",
        "actor": "乙",
        "reason": "乙明确接受先付款后交付",
    }


def _commit(scene, contracts, obligations, result, step, proposals):
    return WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=obligations,
        contract_state=contracts,
        current_step=step,
        proposal_actors=set(proposals),
    )


def _propose_and_accept(due_after_steps=2, accept_step=1):
    scene = _scene()
    contracts = ContractState()
    obligations = {
        "甲": ObligationState(),
        "乙": ObligationState(),
        "丙": ObligationState(),
    }
    proposed = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[_service_offer(due_after_steps)]),
        0,
        {"甲"},
    )
    accepted = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("乙")], contract_updates=[_accept()]),
        accept_step,
        {"乙"},
    )
    return scene, contracts, obligations, proposed, accepted


def test_settlement_pays_now_and_creates_future_service_obligation():
    scene, contracts, obligations, proposed, accepted = _propose_and_accept(
        due_after_steps=3,
        accept_step=2,
    )

    assert proposed.committed is True
    assert accepted.committed is True
    assert scene.get_object_state("甲的报酬")["owner"] == "乙"
    service = obligations["乙"].obligations["deliver_after_payment"]
    assert service.status == "scheduled"
    assert service.creditor == "甲"
    assert service.due_step == 5
    assert service.source_kind == "agreement"
    assert service.source_ref == "pay_then_deliver"
    record = contracts.contracts["pay_then_deliver"]
    assert record.status == "settled"
    assert record.performance_status == "pending"
    assert record.performance_obligations == [
        {"actor": "乙", "obligation_id": "deliver_after_payment"}
    ]
    assert contracts.get_private_snapshot("甲", 2)["counterparty_performance"]["乙"][
        "pending"
    ] == 1


def test_authoritative_service_fulfillment_updates_contract_performance():
    scene, contracts, obligations, _, accepted = _propose_and_accept()
    assert accepted.committed
    scene.get_object_state("乙的交付物").update({"owner": "甲", "location": None})

    obligation_transitions = obligations["乙"].advance_to(2, scene_state=scene)
    contract_transitions = contracts.refresh_performance(obligations)

    assert obligation_transitions == [
        {"obligation_id": "deliver_after_payment", "status": "fulfilled"}
    ]
    assert contract_transitions == [
        {"contract_id": "pay_then_deliver", "performance_status": "fulfilled"}
    ]
    assert contracts.contracts["pay_then_deliver"].performance_status == "fulfilled"


def test_service_breach_is_derived_from_obligation_deadline_not_model_claim():
    scene, contracts, obligations, _, accepted = _propose_and_accept(
        due_after_steps=1,
        accept_step=1,
    )
    assert accepted.committed

    obligation_transitions = obligations["乙"].advance_to(3, scene_state=scene)
    contract_transitions = contracts.refresh_performance(obligations)

    assert obligation_transitions == [
        {"obligation_id": "deliver_after_payment", "status": "breached"}
    ]
    assert contract_transitions[0]["performance_status"] == "breached"
    assert contracts.contracts["pay_then_deliver"].performance_status == "breached"
    assert scene.get_object_state("甲的报酬")["owner"] == "乙"


def test_cancelled_service_has_distinct_performance_from_breach():
    scene, contracts, obligations, _, accepted = _propose_and_accept()
    assert accepted.committed
    cancellation = _result(
        actions=[_action("乙", "乙与甲同场明确解除服务")],
        obligation_updates=[
            {
                "operation": "cancel",
                "actor": "乙",
                "source": "乙",
                "obligation_id": "deliver_after_payment",
                "reason": "双方同意解除交付责任",
            }
        ],
    )

    outcome = _commit(
        scene,
        contracts,
        obligations,
        cancellation,
        2,
        {"乙"},
    )
    transitions = contracts.refresh_performance(obligations)

    assert outcome.committed is True
    assert transitions[0]["performance_status"] == "cancelled"
    assert contracts.contracts["pay_then_deliver"].performance_status == "cancelled"


def test_performance_follows_service_obligation_delegation_chain():
    scene, contracts, obligations, _, accepted = _propose_and_accept()
    assert accepted.committed
    delegation = _result(
        actions=[_action("乙", "乙请求丙接手"), _action("丙", "丙同意接手")],
        obligation_updates=[
            {
                "operation": "delegate",
                "actor": "乙",
                "source": "乙",
                "obligation_id": "deliver_after_payment",
                "delegate": "丙",
                "accepted_by": "丙",
                "reason": "乙与丙当面完成责任转交",
            }
        ],
    )
    delegated = _commit(
        scene,
        contracts,
        obligations,
        delegation,
        2,
        {"乙", "丙"},
    )
    scene.get_object_state("乙的交付物").update({"owner": "甲", "location": None})
    obligations["丙"].advance_to(2, scene_state=scene)
    transitions = contracts.refresh_performance(obligations)

    assert delegated.committed is True
    assert obligations["乙"].obligations["deliver_after_payment"].status == "delegated"
    assert obligations["丙"].obligations["deliver_after_payment"].status == "fulfilled"
    assert transitions[0]["performance_status"] == "fulfilled"
    link = contracts.contracts["pay_then_deliver"].performance_obligations[0]
    assert link["current_actor"] == "丙"


def test_private_counterparty_record_uses_only_participated_contract_history():
    scene, contracts, obligations, _, accepted = _propose_and_accept()
    assert accepted.committed
    obligations["乙"].advance_to(4, scene_state=scene)
    contracts.refresh_performance(obligations)

    alice_view = contracts.get_private_snapshot("甲", 4)
    bob_view = contracts.get_private_snapshot("乙", 4)
    observer_view = contracts.get_private_snapshot("旁观者", 4)

    assert alice_view["counterparty_performance"]["乙"]["breached"] == 1
    assert bob_view["counterparty_performance"] == {}
    assert bob_view["own_performance"]["breached"] == 1
    assert observer_view["counterparty_performance"] == {}
    assert observer_view["recent_history"] == []


def test_service_creation_failure_rolls_back_upfront_payment_and_acceptance():
    scene = _scene()
    contracts = ContractState()
    obligations = {
        "甲": ObligationState(),
        "乙": ObligationState(),
        "丙": ObligationState(),
    }
    assert _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[_service_offer()]),
        0,
        {"甲"},
    ).committed
    # The obligation id becomes occupied after the offer but before acceptance.
    obligations["乙"].obligations["deliver_after_payment"] = (
        ObligationState.from_initial(
            [
                {
                    "obligation_id": "deliver_after_payment",
                    "title": "另一项既有责任",
                    "due_step": 10,
                }
            ]
        ).obligations["deliver_after_payment"]
    )
    acceptance = _result(
        actions=[_action("乙")],
        contract_updates=[_accept()],
    )

    outcome = _commit(
        scene,
        contracts,
        obligations,
        acceptance,
        1,
        {"乙"},
    )

    assert outcome.committed is False
    assert scene.get_object_state("甲的报酬")["owner"] == "甲"
    assert contracts.contracts["pay_then_deliver"].status == "pending"
    assert contracts.contracts["pay_then_deliver"].accepted_by == ["甲"]
    assert any("obligation already exists" in error for error in outcome.errors)


def test_service_terms_cannot_reveal_completion_object_to_unaware_party():
    scene = _scene()
    scene.get_object_state("乙的交付物")["hidden"] = True
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    proposal = _result(
        actions=[_action("甲")],
        contract_updates=[_service_offer()],
    )

    outcome = _commit(
        scene,
        contracts,
        obligations,
        proposal,
        0,
        {"甲"},
    )

    assert outcome.committed is False
    assert contracts.contracts == {}
    assert any("not visible" in error for error in outcome.errors)
