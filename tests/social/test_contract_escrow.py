from copy import deepcopy

from src.story_engine.components.contract_state import ContractState
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.social import AgreementRegistry
from src.story_engine.systems.agreements import AgreementSystem
from src.story_engine.systems.rendering import RenderingSystem


def _scene(*, with_provider_stack=False):
    coin = {
        "is_location": False,
        "kind": "currency",
        "hidden": False,
        "portable": True,
        "stack_key": "currency:copper",
        "affordances": [],
    }
    world_objects = {
        "工坊": {},
        "甲的报酬": {
            "is_location": False,
            "kind": "payment",
            "owner": "甲",
            "location": None,
            "hidden": False,
            "portable": True,
        },
        "甲的铜币": {
            **coin,
            "owner": "甲",
            "location": None,
            "quantity": 5,
        },
        "乙的交付物": {
            "is_location": False,
            "kind": "deliverable",
            "owner": "乙",
            "location": None,
            "hidden": False,
            "portable": True,
        },
    }
    if with_provider_stack:
        world_objects["乙的铜币"] = {
            **coin,
            "owner": "乙",
            "location": None,
            "quantity": 3,
        }
    return SceneState(
        world_objects=world_objects,
        actor_states={
            "甲": {"location": "工坊"},
            "乙": {"location": "工坊"},
        },
    )


def _action(actor):
    return {
        "actor": actor,
        "outcome": "success",
        "location": "工坊",
        "visibility": "public",
        "result": "明确确认带条件托管的服务契约",
    }


def _result(*, actions=None, contract_updates=None):
    return {
        "resolved_actions": actions or [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": [],
        "contract_updates": contract_updates or [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def _offer(*, object_id="甲的报酬", quantity=None, escrows=None):
    transfer = {"from": "甲", "object_id": object_id}
    if quantity is not None:
        transfer["quantity"] = quantity
    return {
        "operation": "propose",
        "contract_id": "escrow_delivery",
        "actor": "甲",
        "parties": ["甲", "乙"],
        "title": "完成交付后释放报酬",
        "expires_step": 3,
        "transfers": [],
        "delegations": [],
        "services": [
            {
                "actor": "乙",
                "creditor": "甲",
                "obligation_id": "deliver_for_escrow",
                "title": "交付物品",
                "due_after_steps": 1,
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
        "escrows": escrows
        if escrows is not None
        else [
            {
                "transfer": transfer,
                "release_to": "乙",
                "refund_to": "甲",
                "release_on_service": "deliver_for_escrow",
                "refund_on": ["breached", "cancelled"],
            }
        ],
        "reason": "甲提出由权威履约状态控制报酬去向",
    }


def _accept():
    return {
        "operation": "accept",
        "contract_id": "escrow_delivery",
        "actor": "乙",
        "reason": "乙接受条件托管条款",
    }


def _commit(scene, contracts, obligations, result, step, actors):
    return WorldStateTransaction().commit(
        scene,
        DramaState(),
        result,
        obligation_states=obligations,
        contract_state=contracts,
        current_step=step,
        proposal_actors=set(actors),
    )


def _settled(*, object_id="甲的报酬", quantity=None, with_provider_stack=False):
    scene = _scene(with_provider_stack=with_provider_stack)
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    proposed = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[_offer(
            object_id=object_id,
            quantity=quantity,
        )]),
        0,
        {"甲"},
    )
    acceptance_result = _result(
        actions=[_action("乙")], contract_updates=[_accept()]
    )
    accepted = _commit(
        scene,
        contracts,
        obligations,
        acceptance_result,
        1,
        {"乙"},
    )
    return scene, contracts, obligations, proposed, accepted, acceptance_result


def _run_contract_system(scene, contracts, obligations, step):
    gm = Entity("GameMaster")
    gm.add_component(scene)
    entities = {"GameMaster": gm}
    for actor, state in obligations.items():
        entity = Entity(actor)
        entity.add_component(state)
        entities[actor] = entity
    registry = AgreementRegistry()
    registry.apply_book(contracts, entities)
    context = {
        "clock": type("Clock", (), {"current_step": step})(),
        "agreement_registry": registry,
    }
    AgreementSystem().update(entities, context)
    contracts.restore_from(registry.to_book())
    context["contract_transitions"] = context["agreement_transitions"]
    context["contract_escrow_errors"] = context["agreement_escrow_errors"]
    return context


def test_final_acceptance_moves_asset_into_engine_custody_not_provider_inventory():
    scene, contracts, obligations, proposed, accepted, result = _settled()

    assert proposed.committed is True
    assert accepted.committed is True
    assert "甲的报酬" not in scene.world_objects
    assert obligations["乙"].obligations["deliver_for_escrow"].status == "scheduled"
    record = contracts.contracts["escrow_delivery"]
    assert record.status == "settled"
    assert len(record.escrow_lots) == 1
    lot = record.escrow_lots[0]
    assert lot["status"] == "held"
    assert lot["object_state"]["owner"] is None
    assert result["contract_escrow_deposits"][0]["contract_id"] == "escrow_delivery"


def test_non_empty_container_cannot_enter_custody_and_acceptance_rolls_back():
    scene = _scene()
    scene.world_objects["甲的报酬"].update(
        {
            "is_container": True,
            "container_capacity": 2,
            "container_open": False,
            "container_opaque": True,
        }
    )
    scene.world_objects["箱中凭据"] = {
        "is_location": False,
        "kind": "document",
        "owner": None,
        "location": None,
        "container": "甲的报酬",
        "hidden": False,
        "portable": True,
    }
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    proposed = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[_offer()]),
        0,
        {"甲"},
    )
    before = deepcopy(scene.get_snapshot())

    accepted = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("乙")], contract_updates=[_accept()]),
        1,
        {"乙"},
    )

    assert proposed.committed is True
    assert accepted.committed is False
    assert any("cannot custody non-empty container" in error for error in accepted.errors)
    assert scene.get_snapshot() == before
    assert contracts.contracts["escrow_delivery"].status == "pending"
    assert contracts.contracts["escrow_delivery"].accepted_by == ["甲"]
    assert obligations["乙"].obligations == {}


def test_fulfilled_service_atomically_releases_whole_object_to_provider():
    scene, contracts, obligations, _, accepted, _ = _settled()
    assert accepted.committed
    scene.get_object_state("乙的交付物").update({"owner": "甲", "location": None})
    obligations["乙"].advance_to(2, scene_state=scene)

    context = _run_contract_system(scene, contracts, obligations, 2)

    assert scene.get_object_state("甲的报酬")["owner"] == "乙"
    lot = contracts.contracts["escrow_delivery"].escrow_lots[0]
    assert lot["status"] == "released"
    assert lot["resolved_step"] == 2
    assert any(
        item.get("escrow_status") == "released"
        for item in context["contract_transitions"]
    )
    assert context["contract_escrow_errors"] == []


def test_breached_service_refunds_whole_object_to_original_payer():
    scene, contracts, obligations, _, accepted, _ = _settled()
    assert accepted.committed
    obligations["乙"].advance_to(3, scene_state=scene)

    context = _run_contract_system(scene, contracts, obligations, 3)

    assert contracts.contracts["escrow_delivery"].performance_status == "breached"
    assert scene.get_object_state("甲的报酬")["owner"] == "甲"
    assert contracts.contracts["escrow_delivery"].escrow_lots[0]["status"] == "refunded"
    assert any(
        item.get("escrow_status") == "refunded"
        for item in context["contract_transitions"]
    )


def test_partial_stack_escrow_reuses_shared_split_merge_semantics():
    scene, contracts, obligations, _, accepted, _ = _settled(
        object_id="甲的铜币",
        quantity=2,
        with_provider_stack=True,
    )
    assert accepted.committed
    assert scene.get_object_state("甲的铜币")["quantity"] == 3
    assert scene.get_object_state("乙的铜币")["quantity"] == 3
    scene.get_object_state("乙的交付物").update({"owner": "甲", "location": None})
    obligations["乙"].advance_to(2, scene_state=scene)

    _run_contract_system(scene, contracts, obligations, 2)

    assert scene.get_object_state("甲的铜币")["quantity"] == 3
    assert scene.get_object_state("乙的铜币")["quantity"] == 5
    assert scene.get_scene_flag("dynamic_world_object_names", []) == []


def test_partial_stack_refund_merges_back_into_payer_source_stack():
    scene, contracts, obligations, _, accepted, _ = _settled(
        object_id="甲的铜币",
        quantity=2,
    )
    assert accepted.committed
    assert scene.get_object_state("甲的铜币")["quantity"] == 3
    obligations["乙"].advance_to(3, scene_state=scene)

    _run_contract_system(scene, contracts, obligations, 3)

    assert scene.get_object_state("甲的铜币")["quantity"] == 5
    assert contracts.contracts["escrow_delivery"].escrow_lots[0]["status"] == "refunded"
    assert scene.get_scene_flag("dynamic_world_object_names", []) == []


def test_escrow_release_failure_keeps_scene_and_lot_atomic_for_retry():
    scene, contracts, obligations, _, accepted, _ = _settled(
        object_id="甲的铜币",
        quantity=2,
    )
    assert accepted.committed
    scene.update_scene_flags({"max_dynamic_world_objects": 0})
    scene.get_object_state("乙的交付物").update({"owner": "甲", "location": None})
    obligations["乙"].advance_to(2, scene_state=scene)
    before = deepcopy(scene.get_snapshot())

    context = _run_contract_system(scene, contracts, obligations, 2)

    assert scene.get_snapshot() == before
    assert contracts.contracts["escrow_delivery"].escrow_lots[0]["status"] == "held"
    assert contracts.contracts["escrow_delivery"].performance_status == "fulfilled"
    assert any(
        "exceeds max_dynamic_world_objects" in error
        for error in context["contract_escrow_errors"]
    )


def test_service_creation_failure_rolls_back_custody_and_contract_acceptance():
    scene = _scene()
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    assert _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[_offer()]),
        0,
        {"甲"},
    ).committed
    obligations["乙"] = ObligationState.from_initial(
        [{"obligation_id": "deliver_for_escrow", "title": "既有责任", "due_step": 9}]
    )
    before = deepcopy(scene.get_snapshot())

    outcome = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("乙")], contract_updates=[_accept()]),
        1,
        {"乙"},
    )

    assert outcome.committed is False
    assert scene.get_snapshot() == before
    assert contracts.contracts["escrow_delivery"].status == "pending"
    assert contracts.contracts["escrow_delivery"].escrow_lots == []


def test_escrow_terms_require_same_contract_service_and_cannot_double_spend():
    for mutate, expected in (
        (
            lambda offer: offer["escrows"][0].update(
                {"release_on_service": "unknown_service"}
            ),
            "reference a service",
        ),
        (
            lambda offer: offer.update(
                {
                    "transfers": [
                        {"from": "甲", "to": "乙", "object_id": "甲的报酬"}
                    ]
                }
            ),
            "both immediate transfer and escrow",
        ),
    ):
        scene = _scene()
        contracts = ContractState()
        obligations = {"甲": ObligationState(), "乙": ObligationState()}
        offer = _offer()
        mutate(offer)

        outcome = _commit(
            scene,
            contracts,
            obligations,
            _result(actions=[_action("甲")], contract_updates=[offer]),
            0,
            {"甲"},
        )

        assert outcome.committed is False
        assert contracts.contracts == {}
        assert any(expected in error for error in outcome.errors)


def test_model_cannot_forge_engine_owned_escrow_deposit():
    scene = _scene()
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    result = _result()
    result["contract_escrow_deposits"] = [
        {
            "custody_id": "forged",
            "contract_id": "missing",
            "transfer": {"from": "甲", "object_id": "甲的报酬", "quantity": 1},
        }
    ]

    outcome = _commit(scene, contracts, obligations, result, 0, set())

    assert outcome.committed is True
    assert scene.get_object_state("甲的报酬")["owner"] == "甲"
    assert result["contract_escrow_deposits"] == []


def test_custody_lots_are_private_to_parties_and_removed_from_rendering_payload():
    scene, contracts, _, _, accepted, result = _settled()
    assert accepted.committed

    assert contracts.get_private_snapshot("甲", 1)["recent_history"][0][
        "escrow_lots"
    ][0]["status"] == "held"
    assert contracts.get_private_snapshot("旁观者", 1)["recent_history"] == []
    visible = RenderingSystem()._build_visible_simulation(
        result,
        {"location": "工坊", "visible_world": {}},
        visible_locations=["工坊"],
    )

    assert visible["contract_escrow_deposits"] == []
    assert "甲的报酬" not in scene.world_objects


def test_acceptance_checkpoint_restores_scene_and_escrow_state_together():
    scene, contracts, obligations, _, accepted, _ = _settled()
    assert accepted.committed and accepted.checkpoint is not None
    assert "甲的报酬" not in scene.world_objects

    accepted.checkpoint.restore()

    assert scene.get_object_state("甲的报酬")["owner"] == "甲"
    record = contracts.contracts["escrow_delivery"]
    assert record.status == "pending"
    assert record.accepted_by == ["甲"]
    assert record.escrow_lots == []


def test_contract_history_pruning_never_discards_held_custody_assets():
    scene, contracts, obligations, _, accepted, _ = _settled()
    assert accepted.committed
    contracts.max_contracts = 1
    second = _offer(object_id="甲的铜币", quantity=1)
    second["contract_id"] = "second_offer"

    outcome = _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[second]),
        2,
        {"甲"},
    )

    assert outcome.committed is False
    assert "escrow_delivery" in contracts.contracts
    assert contracts.contracts["escrow_delivery"].escrow_lots[0]["status"] == "held"
    assert any("exceeds max_contracts" in error for error in outcome.errors)


def test_escrow_term_order_does_not_change_normalized_contract_state():
    extra = {
        "transfer": {"from": "甲", "object_id": "甲的铜币", "quantity": 2},
        "release_to": "乙",
        "refund_to": "甲",
        "release_on_service": "deliver_for_escrow",
        "refund_on": ["cancelled", "breached"],
    }
    base = _offer()["escrows"][0]
    snapshots = []
    for ordered in ([base, extra], [extra, base]):
        scene = _scene()
        contracts = ContractState()
        obligations = {"甲": ObligationState(), "乙": ObligationState()}
        outcome = _commit(
            scene,
            contracts,
            obligations,
            _result(
                actions=[_action("甲")],
                contract_updates=[_offer(escrows=deepcopy(ordered))],
            ),
            0,
            {"甲"},
        )
        assert outcome.committed
        snapshots.append(contracts.model_dump())

    assert snapshots[0] == snapshots[1]


def test_counteroffer_can_replace_terms_with_service_backed_escrow_atomically():
    scene = _scene()
    contracts = ContractState()
    obligations = {"甲": ObligationState(), "乙": ObligationState()}
    original = {
        "operation": "propose",
        "contract_id": "plain_payment",
        "actor": "甲",
        "parties": ["甲", "乙"],
        "title": "立即付款",
        "expires_step": 3,
        "transfers": [
            {"from": "甲", "to": "乙", "object_id": "甲的报酬"}
        ],
        "delegations": [],
        "services": [],
        "escrows": [],
        "reason": "甲先提出立即付款方案",
    }
    assert _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("甲")], contract_updates=[original]),
        0,
        {"甲"},
    ).committed
    counter = _offer()
    counter.update(
        {
            "operation": "counter",
            "contract_id": "plain_payment",
            "new_contract_id": "escrow_delivery",
            "actor": "乙",
            "expires_step": 4,
            "reason": "乙要求改成完成交付后释放报酬",
        }
    )
    assert _commit(
        scene,
        contracts,
        obligations,
        _result(actions=[_action("乙")], contract_updates=[counter]),
        1,
        {"乙"},
    ).committed

    accepted = _commit(
        scene,
        contracts,
        obligations,
        _result(
            actions=[_action("甲")],
            contract_updates=[
                {
                    "operation": "accept",
                    "contract_id": "escrow_delivery",
                    "actor": "甲",
                    "reason": "甲接受条件托管反报价",
                }
            ],
        ),
        2,
        {"甲"},
    )

    assert accepted.committed is True
    assert contracts.contracts["plain_payment"].status == "countered"
    replacement = contracts.contracts["escrow_delivery"]
    assert replacement.countered_from == "plain_payment"
    assert replacement.status == "settled"
    assert replacement.escrow_lots[0]["status"] == "held"
    assert "甲的报酬" not in scene.world_objects
    assert "deliver_for_escrow" in obligations["乙"].obligations
