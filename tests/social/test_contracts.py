from copy import deepcopy

from pydantic import Field

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.scheduler import AgentScheduler
from src.story_engine.components.contract_state import ContractState
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.environment.agreement_offers import AgreementOfferEngine
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import (
    PlotEntityConfig,
    PlotRuleConfig,
    ScenarioConfig,
    StateCondition,
)
from src.story_engine.social import AgreementRegistry, SocialRelationRegistry
from src.story_engine.systems.agreements import AgreementSystem
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return deepcopy(self.scripted_result)


def _scene():
    return SceneState(
        world_objects={
            "集市": {},
            "远处": {},
            "甲的令牌": {
                "is_location": False,
                "kind": "token",
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "乙的钥匙": {
                "is_location": False,
                "kind": "key",
                "owner": "乙",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "秘密戒指": {
                "is_location": False,
                "kind": "ring",
                "owner": "甲",
                "location": None,
                "hidden": True,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "集市"},
            "乙": {"location": "集市"},
            "委托人": {"location": "集市"},
            "旁观者": {"location": "集市"},
        },
    )


def _action(actor, text="明确回应契约"):
    return {
        "actor": actor,
        "outcome": "success",
        "location": "集市",
        "visibility": "public",
        "result": text,
    }


def _result(actions=None, updates=None):
    return {
        "resolved_actions": actions or [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": [],
        "contract_updates": updates or [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def _offer(
    contract_id="token_for_key",
    *,
    expires_step=3,
    transfers=None,
    delegations=None,
    parties=None,
):
    return {
        "operation": "propose",
        "contract_id": contract_id,
        "actor": "甲",
        "parties": list(parties or ["甲", "乙"]),
        "title": "用令牌换钥匙",
        "summary": "甲交出令牌，乙交出钥匙",
        "expires_step": expires_step,
        "transfers": transfers
        if transfers is not None
        else [
            {"from": "甲", "to": "乙", "object_id": "甲的令牌"},
            {"from": "乙", "to": "甲", "object_id": "乙的钥匙"},
        ],
        "delegations": delegations or [],
        "reason": "甲当面提出了完整报价",
    }


def _response(operation, actor, contract_id="token_for_key"):
    return {
        "operation": operation,
        "contract_id": contract_id,
        "actor": actor,
        "reason": f"{actor}明确{operation}该报价",
    }


def _counter(
    actor,
    *,
    old_id="token_for_key",
    new_id="token_for_key_v2",
    expires_step=4,
    transfers=None,
    parties=None,
):
    return {
        "operation": "counter",
        "contract_id": old_id,
        "new_contract_id": new_id,
        "actor": actor,
        "parties": list(parties or ["甲", "乙"]),
        "title": "修改后的令牌交易",
        "summary": "以新的完整条款替换旧报价",
        "expires_step": expires_step,
        "transfers": transfers
        if transfers is not None
        else [{"from": "甲", "to": "乙", "object_id": "甲的令牌"}],
        "delegations": [],
        "services": [],
        "escrows": [],
        "reason": f"{actor}明确提出了完整反报价",
    }


def _commit(
    scene,
    contracts,
    result,
    *,
    step,
    proposal_actors,
    obligations=None,
):
    return WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        obligation_states=obligations or {},
        current_step=step,
        proposal_actors=set(proposal_actors),
        contract_state=contracts,
    )


def test_offer_persists_without_moving_assets_then_later_acceptance_settles():
    scene = _scene()
    contracts = ContractState()
    forged_offer = {
        **_offer(),
        "source_kind": "scenario",
        "source_ref": "forged-origin",
    }
    proposal = _result(
        actions=[_action("甲", "甲提出报价")],
        updates=[forged_offer],
    )

    proposed = _commit(
        scene,
        contracts,
        proposal,
        step=0,
        proposal_actors={"甲"},
    )

    assert proposed.committed is True
    record = contracts.contracts["token_for_key"]
    assert record.status == "pending"
    assert record.accepted_by == ["甲"]
    assert record.source_kind == "resolved_action"
    assert record.source_ref == "step:0:actor:甲"
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"
    assert proposal["contract_settlements"] == []

    acceptance = _result(
        actions=[_action("乙", "乙在下一回合明确接受")],
        updates=[_response("accept", "乙")],
    )
    accepted = _commit(
        scene,
        contracts,
        acceptance,
        step=1,
        proposal_actors={"乙"},
    )

    assert accepted.committed is True
    assert contracts.contracts["token_for_key"].status == "settled"
    assert (
        contracts.contracts["token_for_key"].resolution_source_kind
        == "resolved_action"
    )
    assert (
        contracts.contracts["token_for_key"].resolution_source_ref
        == "step:1:actor:乙"
    )
    assert scene.get_object_state("甲的令牌")["owner"] == "乙"
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"
    assert acceptance["contract_settlements"][0]["contract_id"] == "token_for_key"
    assert acceptance["exchanges"][0]["contract_id"] == "token_for_key"


def test_final_acceptance_rechecks_assets_and_does_not_persist_on_failure():
    scene = _scene()
    contracts = ContractState()
    proposal = _result(actions=[_action("甲")], updates=[_offer()])
    assert _commit(
        scene, contracts, proposal, step=0, proposal_actors={"甲"}
    ).committed

    # A different authoritative event spends the promised token before the
    # recipient accepts the old offer.
    scene.get_object_state("甲的令牌")["owner"] = "旁观者"
    before = deepcopy(scene.get_snapshot())
    acceptance = _result(
        actions=[_action("乙")],
        updates=[_response("accept", "乙")],
    )
    outcome = _commit(
        scene,
        contracts,
        acceptance,
        step=1,
        proposal_actors={"乙"},
    )

    assert outcome.committed is False
    assert scene.get_snapshot() == before
    assert contracts.contracts["token_for_key"].status == "pending"
    assert contracts.contracts["token_for_key"].accepted_by == ["甲"]
    assert any("does not own object" in error for error in outcome.errors)


def test_party_can_counter_with_complete_replacement_terms_then_other_party_accepts():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed

    counter_result = _result(
        actions=[_action("乙", "乙拒绝旧价格并提出新条件")],
        updates=[_counter("乙")],
    )
    countered = _commit(
        scene,
        contracts,
        counter_result,
        step=1,
        proposal_actors={"乙"},
    )

    assert countered.committed is True
    old = contracts.contracts["token_for_key"]
    replacement = contracts.contracts["token_for_key_v2"]
    assert old.status == "countered"
    assert old.superseded_by == "token_for_key_v2"
    assert replacement.status == "pending"
    assert replacement.countered_from == "token_for_key"
    assert replacement.proposer == "乙"
    assert replacement.accepted_by == ["乙"]
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"

    acceptance = _result(
        actions=[_action("甲", "甲接受乙的反报价")],
        updates=[_response("accept", "甲", "token_for_key_v2")],
    )
    accepted = _commit(
        scene,
        contracts,
        acceptance,
        step=2,
        proposal_actors={"甲"},
    )

    assert accepted.committed is True
    assert contracts.contracts["token_for_key_v2"].status == "settled"
    assert scene.get_object_state("甲的令牌")["owner"] == "乙"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"


def test_invalid_counteroffer_keeps_original_offer_pending_and_creates_nothing():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    invalid = _counter(
        "乙",
        transfers=[
            {"from": "甲", "to": "乙", "object_id": "秘密戒指"}
        ],
    )

    outcome = _commit(
        scene,
        contracts,
        _result(actions=[_action("乙")], updates=[invalid]),
        step=1,
        proposal_actors={"乙"},
    )

    assert outcome.committed is False
    assert set(contracts.contracts) == {"token_for_key"}
    assert contracts.contracts["token_for_key"].status == "pending"
    assert contracts.contracts["token_for_key"].superseded_by == ""
    assert any("must be disclosed" in error for error in outcome.errors)


def test_counteroffer_cannot_silently_change_the_negotiating_parties():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed

    outcome = _commit(
        scene,
        contracts,
        _result(
            actions=[_action("乙")],
            updates=[_counter("乙", parties=["甲", "乙", "委托人"])],
        ),
        step=1,
        proposal_actors={"乙"},
    )

    assert outcome.committed is False
    assert contracts.contracts["token_for_key"].status == "pending"
    assert "token_for_key_v2" not in contracts.contracts
    assert any("cannot add or remove" in error for error in outcome.errors)


def test_counteroffers_form_a_traceable_chain_without_moving_assets():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("乙")], updates=[_counter("乙")]),
        step=1,
        proposal_actors={"乙"},
    ).committed
    second_counter = _counter(
        "甲",
        old_id="token_for_key_v2",
        new_id="token_for_key_v3",
        expires_step=5,
        transfers=[
            {"from": "甲", "to": "乙", "object_id": "甲的令牌"},
            {"from": "乙", "to": "甲", "object_id": "乙的钥匙"},
        ],
    )

    outcome = _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[second_counter]),
        step=2,
        proposal_actors={"甲"},
    )

    assert outcome.committed is True
    assert contracts.contracts["token_for_key"].superseded_by == "token_for_key_v2"
    assert contracts.contracts["token_for_key_v2"].countered_from == "token_for_key"
    assert contracts.contracts["token_for_key_v2"].superseded_by == "token_for_key_v3"
    assert contracts.contracts["token_for_key_v3"].countered_from == "token_for_key_v2"
    assert contracts.contracts["token_for_key_v3"].accepted_by == ["甲"]
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"


def test_three_party_counteroffer_requires_every_non_proposer_acceptance():
    scene = _scene()
    contracts = ContractState()
    parties = ["甲", "乙", "委托人"]
    assert _commit(
        scene,
        contracts,
        _result(
            actions=[_action("甲")],
            updates=[
                _offer(
                    parties=parties,
                    transfers=[
                        {"from": "甲", "to": "乙", "object_id": "甲的令牌"}
                    ],
                )
            ],
        ),
        step=0,
        proposal_actors={"甲"},
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(
            actions=[_action("乙")],
            updates=[_counter("乙", parties=parties)],
        ),
        step=1,
        proposal_actors={"乙"},
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(
            actions=[_action("甲")],
            updates=[_response("accept", "甲", "token_for_key_v2")],
        ),
        step=2,
        proposal_actors={"甲"},
    ).committed

    pending = contracts.contracts["token_for_key_v2"]
    assert pending.status == "pending"
    assert set(pending.accepted_by) == {"甲", "乙"}
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"

    final = _commit(
        scene,
        contracts,
        _result(
            actions=[_action("委托人")],
            updates=[_response("accept", "委托人", "token_for_key_v2")],
        ),
        step=3,
        proposal_actors={"委托人"},
    )

    assert final.committed is True
    assert contracts.contracts["token_for_key_v2"].status == "settled"
    assert scene.get_object_state("甲的令牌")["owner"] == "乙"


def test_counteroffer_lineage_is_private_to_contract_parties():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("乙")], updates=[_counter("乙")]),
        step=1,
        proposal_actors={"乙"},
    ).committed

    party_view = contracts.get_private_snapshot("甲", 1)
    observer_view = contracts.get_private_snapshot("旁观者", 1)

    assert party_view["pending"][0]["agreement_id"] == "token_for_key_v2"
    assert party_view["pending"][0]["countered_from"] == "token_for_key"
    assert party_view["recent_history"][0]["superseded_by"] == "token_for_key_v2"
    assert observer_view["pending"] == []
    assert observer_view["recent_history"] == []


def test_history_pruning_preserves_all_ancestors_of_a_pending_counteroffer():
    scene = _scene()
    contracts = ContractState(max_contracts=3)
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("乙")], updates=[_counter("乙")]),
        step=1,
        proposal_actors={"乙"},
    ).committed
    third = _counter(
        "甲",
        old_id="token_for_key_v2",
        new_id="token_for_key_v3",
        expires_step=5,
    )
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[third]),
        step=2,
        proposal_actors={"甲"},
    ).committed

    unrelated = _offer("unrelated_offer", expires_step=6)
    outcome = _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[unrelated]),
        step=3,
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert set(contracts.contracts) == {
        "token_for_key",
        "token_for_key_v2",
        "token_for_key_v3",
    }
    assert any("exceeds max_contracts" in error for error in outcome.errors)


def test_parties_must_be_together_again_when_accepting_and_settling():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    scene.actor_states["乙"]["location"] = "远处"
    acceptance = _result(
        actions=[{**_action("乙"), "location": "远处"}],
        updates=[_response("accept", "乙")],
    )

    outcome = _commit(
        scene,
        contracts,
        acceptance,
        step=1,
        proposal_actors={"乙"},
    )

    assert outcome.committed is False
    assert contracts.contracts["token_for_key"].status == "pending"
    assert any("contract parties co-located" in error for error in outcome.errors)


def test_offer_cannot_publish_hidden_or_unowned_terms():
    for transfers, error_text in (
        ([{"from": "甲", "to": "乙", "object_id": "秘密戒指"}], "disclosed"),
        ([{"from": "甲", "to": "乙", "object_id": "乙的钥匙"}], "does not own"),
    ):
        scene = _scene()
        contracts = ContractState()
        result = _result(
            actions=[_action("甲")],
            updates=[_offer(transfers=transfers)],
        )

        outcome = _commit(
            scene,
            contracts,
            result,
            step=0,
            proposal_actors={"甲"},
        )

        assert outcome.committed is False
        assert contracts.contracts == {}
        assert any(error_text in error for error in outcome.errors)


def test_reject_and_withdraw_are_terminal_without_settlement():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer("reject_me")]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    rejected = _commit(
        scene,
        contracts,
        _result(
            actions=[_action("乙")],
            updates=[_response("reject", "乙", "reject_me")],
        ),
        step=1,
        proposal_actors={"乙"},
    )
    assert rejected.committed is True
    assert contracts.contracts["reject_me"].status == "rejected"

    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer("withdraw_me")]),
        step=1,
        proposal_actors={"甲"},
    ).committed
    withdrawn = _commit(
        scene,
        contracts,
        _result(
            actions=[_action("甲")],
            updates=[_response("withdraw", "甲", "withdraw_me")],
        ),
        step=2,
        proposal_actors={"甲"},
    )
    assert withdrawn.committed is True
    assert contracts.contracts["withdraw_me"].status == "withdrawn"
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"


def test_contract_expires_deterministically_without_model_update():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer(expires_step=1)]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    registry = AgreementRegistry()
    registry.apply_book(contracts)
    context = {
        "clock": type("Clock", (), {"current_step": 2})(),
        "agreement_registry": registry,
    }

    AgreementSystem().update({}, context)
    contracts.restore_from(registry.to_book())

    assert contracts.contracts["token_for_key"].status == "expired"
    assert contracts.contracts["token_for_key"].resolution_source_kind == "clock"
    assert contracts.contracts["token_for_key"].resolution_source_ref == "step:2"
    assert context["agreement_transitions"] == [
        {"contract_id": "token_for_key", "status": "expired"}
    ]


def _delegable_obligations(policy="bilateral"):
    return {
        "甲": ObligationState.from_initial(
            [
                {
                    "obligation_id": "deliver",
                    "title": "去远处送货",
                    "creditor": "委托人" if policy == "creditor_consent" else None,
                    "due_step": 5,
                    "delegation_policy": policy,
                    "completion_conditions": [
                        {
                            "scope": "actor",
                            "target": "甲",
                            "path": "location",
                            "operator": "eq",
                            "value": "远处",
                        }
                    ],
                }
            ]
        ),
        "乙": ObligationState(),
        "委托人": ObligationState(),
    }


def test_cross_turn_contract_can_settle_payment_and_delegation_together():
    scene = _scene()
    contracts = ContractState()
    obligations = _delegable_obligations()
    offer = _offer(
        "paid_delegation",
        transfers=[{"from": "乙", "to": "甲", "object_id": "乙的钥匙"}],
        delegations=[
            {"actor": "甲", "delegate": "乙", "obligation_id": "deliver"}
        ],
    )
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[offer]),
        step=0,
        proposal_actors={"甲"},
        obligations=obligations,
    ).committed

    acceptance = _result(
        actions=[_action("乙")],
        updates=[_response("accept", "乙", "paid_delegation")],
    )
    outcome = _commit(
        scene,
        contracts,
        acceptance,
        step=1,
        proposal_actors={"乙"},
        obligations=obligations,
    )

    assert outcome.committed is True
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"
    assert obligations["甲"].obligations["deliver"].status == "delegated"
    assert obligations["乙"].obligations["deliver"].delegated_from == "甲"
    assert contracts.contracts["paid_delegation"].status == "settled"


def test_three_party_creditor_acceptance_can_complete_delegation_contract():
    scene = _scene()
    contracts = ContractState()
    obligations = _delegable_obligations("creditor_consent")
    offer = _offer(
        "creditor_contract",
        parties=["甲", "乙", "委托人"],
        transfers=[],
        delegations=[
            {"actor": "甲", "delegate": "乙", "obligation_id": "deliver"}
        ],
    )
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[offer]),
        step=0,
        proposal_actors={"甲"},
        obligations=obligations,
    ).committed
    assert _commit(
        scene,
        contracts,
        _result(
            actions=[_action("乙")],
            updates=[_response("accept", "乙", "creditor_contract")],
        ),
        step=1,
        proposal_actors={"乙"},
        obligations=obligations,
    ).committed

    final = _commit(
        scene,
        contracts,
        _result(
            actions=[_action("委托人")],
            updates=[_response("accept", "委托人", "creditor_contract")],
        ),
        step=2,
        proposal_actors={"委托人"},
        obligations=obligations,
    )

    assert final.committed is True
    assert contracts.contracts["creditor_contract"].accepted_by == [
        "乙",
        "委托人",
        "甲",
    ]
    assert obligations["乙"].obligations["deliver"].creditor == "委托人"


def test_contract_checkpoint_restores_offer_and_settled_assets():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer()]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    acceptance = _result(
        actions=[_action("乙")],
        updates=[_response("accept", "乙")],
    )
    outcome = _commit(
        scene,
        contracts,
        acceptance,
        step=1,
        proposal_actors={"乙"},
    )
    outcome.checkpoint.restore()

    assert contracts.contracts["token_for_key"].status == "pending"
    assert contracts.contracts["token_for_key"].accepted_by == ["甲"]
    assert scene.get_object_state("甲的令牌")["owner"] == "甲"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"


def test_contract_terms_are_private_to_parties_and_removed_from_rendering():
    scene = _scene()
    contracts = ContractState()
    proposal = _result(actions=[_action("甲")], updates=[_offer()])
    assert _commit(
        scene,
        contracts,
        proposal,
        step=0,
        proposal_actors={"甲"},
    ).committed
    alice = create_agent("甲", "商人", "谨慎", [])
    observer = create_agent("旁观者", "路人", "平静", [])
    input_system = InputSystem()
    agreement_registry = AgreementRegistry()
    agreement_registry.apply_book(contracts)

    alice_view = input_system.build_agent_perception(
        alice,
        scene,
        [],
        {
            "clock": type("Clock", (), {"current_step": 1})(),
            "agreement_registry": agreement_registry,
        },
    )
    observer_view = input_system.build_agent_perception(
        observer,
        scene,
        [],
        {
            "clock": type("Clock", (), {"current_step": 1})(),
            "agreement_registry": agreement_registry,
        },
    )
    visible = RenderingSystem()._build_visible_simulation(
        proposal,
        scene.get_view_pov("旁观者"),
        visible_locations=["集市"],
    )

    assert alice_view.private_agreements["pending"][0]["agreement_id"] == "token_for_key"
    assert observer_view.private_agreements["pending"] == []
    assert "contracts" not in alice_view.world_view
    assert visible["contract_updates"] == []
    assert visible["contract_settlements"] == []


def test_near_expiry_offer_wakes_background_party_but_not_dormant():
    scene = _scene()
    contracts = ContractState()
    assert _commit(
        scene,
        contracts,
        _result(actions=[_action("甲")], updates=[_offer(expires_step=2)]),
        step=0,
        proposal_actors={"甲"},
    ).committed
    background = create_agent(
        "乙",
        "商人",
        "谨慎",
        [],
        activation_policy="background",
        background_interval=99,
    )
    dormant = create_agent(
        "甲",
        "商人",
        "谨慎",
        [],
        activation_policy="dormant",
    )
    scheduler = AgentScheduler()

    activation = scheduler.activation_for(
        background,
        step=1,
        actor_location="集市",
        player_location="远处",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
        agreement_registry=contracts,
    )
    dormant_activation = scheduler.activation_for(
        dormant,
        step=1,
        actor_location="集市",
        player_location="远处",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
        agreement_registry=contracts,
    )

    assert activation.active is True
    assert activation.reason == "agreement_due:token_for_key"
    assert dormant_activation.active is False
    assert dormant_activation.reason == "policy_dormant"


def test_simulation_system_persists_offer_across_steps_and_settles_later_acceptance():
    scene = _scene()
    relation_registry = SocialRelationRegistry()
    agreement_registry = AgreementRegistry(relation_registry)
    plots = PlotState.from_configs(
        [
            PlotEntityConfig(
                plot_id="contract_key",
                title="契约钥匙",
                description="甲通过跨回合契约取得钥匙",
                max_clock=2,
            )
        ]
    )
    scenario = ScenarioConfig(
        name="契约因果",
        description="跨回合接受推动剧情",
        environment="集市",
        initial_state="报价尚未接受",
        plot_rules=[
            PlotRuleConfig(
                rule_id="contract_delivers_key",
                plot_id="contract_key",
                conditions=[
                    StateCondition(
                        scope="world_object",
                        target="乙的钥匙",
                        path="owner",
                        operator="eq",
                        value="甲",
                    )
                ],
                advance=1,
            )
        ],
    )
    control = SimulationControl(
        scripted_result=_result(actions=[_action("甲")], updates=[_offer()]),
        scenario=scenario,
    )
    gm = Entity("GameMaster")
    gm.add_component(control)
    gm.add_component(scene)
    gm.add_component(plots)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm, "甲": Entity("甲"), "乙": Entity("乙")}

    proposal = AgentAction(
        "communicate",
        "提出令牌换钥匙",
        "乙",
        agreement_operation="propose",
        agreement_give_refs=("甲的令牌",),
        agreement_request_refs=("乙的钥匙",),
    )
    first_context = {
        "clock": type("Clock", (), {"current_step": 0})(),
        "intents": [{
            "actor": "甲",
            "intent": proposal.detail,
            "action_kind": "communicate",
            "action_target": "乙",
            "action_agreement_operation": "propose",
            "action_agreement_id": AgreementOfferEngine.asset_offer_id(
                "甲", "乙", ["甲的令牌"], ["乙的钥匙"], 0
            ),
            "action_agreement_template_id": "",
            "action_agreement_give_refs": ["甲的令牌"],
            "action_agreement_request_refs": ["乙的钥匙"],
        }],
        "agreement_registry": agreement_registry,
        "relation_registry": relation_registry,
    }
    SimulationSystem().update(entities, first_context)

    assert first_context["state_transaction"]["committed"] is True
    agreement_id = first_context["agreement_action_traces"][0]["agreement_id"]
    assert agreement_registry.to_book().agreements[agreement_id].status == "pending"
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"

    control.scripted_result = _result(
        actions=[_action("乙")],
        updates=[_response("accept", "乙")],
    )
    second_context = {
        "clock": type("Clock", (), {"current_step": 1})(),
        "intents": [{
            "actor": "乙", "intent": "接受令牌换钥匙",
            "action_kind": "communicate", "action_target": "甲",
            "action_agreement_operation": "accept",
            "action_agreement_id": agreement_id,
        }],
        "agreement_registry": agreement_registry,
        "relation_registry": relation_registry,
    }
    SimulationSystem().update(entities, second_context)

    assert second_context["state_transaction"]["committed"] is True
    assert agreement_registry.to_book().agreements[agreement_id].status == "settled"
    assert scene.get_object_state("甲的令牌")["owner"] == "乙"
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"
    assert plots.plots["contract_key"]["clock"] == 1


def test_model_cannot_forge_contract_settlement_or_authorization_fields():
    scene = _scene()
    contracts = ContractState()
    result = _result()
    result["contract_settlements"] = [
        {"contract_id": "forged", "parties": ["甲", "乙"]}
    ]
    result["contract_authorizations"] = {
        "forged": {"actors": ["甲", "乙"], "location": "集市"}
    }

    outcome = _commit(
        scene,
        contracts,
        result,
        step=0,
        proposal_actors=set(),
    )

    assert outcome.committed is True
    assert result["contract_settlements"] == []
    assert result["contract_authorizations"] == {}
