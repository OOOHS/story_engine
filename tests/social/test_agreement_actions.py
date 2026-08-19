from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.environment.agreement_offers import AgreementOfferEngine
from src.story_engine.environment.contracts import ContractDynamics
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.social import AgreementBook
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.simulation.agreement_actions import AgreementActionResolver
from src.story_engine.systems.input import InputSystem


def _scenario():
    return ScenarioConfig(
        name="offer",
        default_agent_runtime="llm",
        description="offer",
        environment="room",
        initial_state="open",
        initial_actor_states={"甲": {"location": "房间"}, "乙": {"location": "房间"}},
        initial_world_objects={
            "凭证": {"owner": "甲", "portable": True, "hidden": False}
        },
        agreement_offer_templates=[
            {
                "template_id": "host-offer",
                "agreement_id": "agreement-1",
                "proposer": "甲",
                "parties": ["甲", "乙"],
                "title": "交付凭证",
                "summary": "甲将凭证交给乙",
                "transfers": [{"from": "甲", "to": "乙", "object_id": "凭证"}],
                "services": [
                    {
                        "actor": "甲",
                        "completion_conditions": [{"path": "secret.host.condition"}],
                    }
                ],
            }
        ],
    )


class _Scene:
    actor_states = {"甲": {}, "乙": {}}
    world_objects = {"凭证": {}}

    @staticmethod
    def is_location(_object_id):
        return False

    @staticmethod
    def get_actor_location(_actor):
        return "房间"

    @staticmethod
    def get_known_locations():
        return {"房间"}

    @staticmethod
    def get_object_state(object_id):
        return {"owner": "甲", "portable": True, "hidden": False} if object_id == "凭证" else {}


def test_offer_opportunity_is_pov_safe_and_only_visible_to_proposer():
    engine = AgreementOfferEngine()
    opportunities = engine.build_opportunities(
        _scenario(), actor_name="甲", scene_state=_Scene(),
        agreement_registry=None, visible_actors=["甲", "乙"],
        visible_objects=["凭证"],
    )

    template_opportunity = next(
        item for item in opportunities if item.get("template_id") == "host-offer"
    )
    assert template_opportunity == {
        "template_id": "host-offer",
        "agreement_id": "agreement-1",
        "title": "交付凭证",
        "summary": "甲将凭证交给乙",
        "parties": ["甲", "乙"],
        "counterparties": ["乙"],
    }
    assert "completion_conditions" not in str(opportunities)
    other_opportunities = engine.build_opportunities(
        _scenario(), actor_name="乙", scene_state=_Scene(),
        agreement_registry=None, visible_actors=["甲", "乙"],
        visible_objects=["凭证"],
    )
    assert all(item.get("template_id") != "host-offer" for item in other_opportunities)
    assert other_opportunities[0]["request_options"] == ["凭证"]


def test_asset_offer_catalog_does_not_leak_non_visible_objects():
    opportunities = AgreementOfferEngine().build_opportunities(
        ScenarioConfig(
            name="seed",
            default_agent_runtime="llm", description="seed", environment="room",
            initial_state="seed",
        ),
        actor_name="甲", scene_state=_Scene(), agreement_registry=None,
        visible_actors=["甲", "乙"], visible_objects=[],
    )

    asset_offer = next(
        (item for item in opportunities if item.get("opportunity_kind") == "asset_offer"),
        None,
    )
    assert asset_offer is None


def test_input_accepts_only_references_in_private_perception():
    perception = AgentPerception(
        actor_name="乙", step=1,
        private_agreements={"pending": [{
            "agreement_id": "agreement-1", "proposer": "甲",
            "parties": ["甲", "乙"], "awaiting_actor": True,
        }]},
    )
    accepted = InputSystem._validated_agreement_reference(
        AgentAction("communicate", "接受", "甲", agreement_operation="accept", agreement_id="agreement-1"),
        perception,
    )
    unknown = InputSystem._validated_agreement_reference(
        AgentAction("communicate", "接受", "甲", agreement_operation="accept", agreement_id="invented"),
        perception,
    )

    assert accepted["agreement_id"] == "agreement-1"
    assert unknown == {}


def test_host_compiler_replaces_model_terms_with_exact_template_terms():
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "success"}],
        "agreement_updates": [{
            "operation": "propose", "agreement_id": "forged",
            "transfers": [{"object_id": "other"}],
        }],
    }
    intent = {
        "actor": "甲", "intent": "正式提出", "action_kind": "communicate",
        "action_agreement_operation": "propose",
        "action_agreement_id": "agreement-1",
        "action_agreement_template_id": "host-offer",
    }

    resolved = AgreementActionResolver().resolve(
        result, intents=[intent], scenario=_scenario(), current_step=3
    ).result

    assert len(resolved["agreement_updates"]) == 1
    update = resolved["agreement_updates"][0]
    assert update["agreement_id"] == "agreement-1"
    assert update["expires_step"] == 11
    assert update["transfers"] == [
        {"from": "甲", "to": "乙", "object_id": "凭证"}
    ]


def test_world_state_generates_a_freeform_asset_offer_without_authored_template():
    scenario = ScenarioConfig(
        name="market",
        default_agent_runtime="llm", description="market", environment="room",
        initial_state="open", initial_actor_states={"甲": {}, "乙": {}},
        initial_world_objects={},
    )
    perception = AgentPerception(
        actor_name="甲", step=7,
        agreement_opportunities=[{
            "opportunity_kind": "asset_offer", "counterparty": "乙",
            "give_options": ["金币"], "request_options": ["钥匙"],
        }],
    )
    action = AgentAction(
        "communicate", "提出用金币交换钥匙", "乙",
        agreement_operation="propose",
        agreement_give_refs=("金币",),
        agreement_request_refs=("钥匙",),
    )
    reference = InputSystem._validated_agreement_reference(action, perception)
    intent = {
        "actor": "甲", "intent": action.detail, "action_kind": "communicate",
        "action_target": "乙",
        "action_agreement_operation": reference["operation"],
        "action_agreement_id": reference["agreement_id"],
        "action_agreement_template_id": "",
        "action_agreement_give_refs": list(reference["give_refs"]),
        "action_agreement_request_refs": list(reference["request_refs"]),
    }
    result = AgreementActionResolver().resolve(
        {"resolved_actions": [{"actor": "甲", "outcome": "success"}]},
        intents=[intent], scenario=scenario, current_step=7,
    ).result

    update = result["agreement_updates"][0]
    assert update["agreement_id"].startswith("asset-offer:")
    assert update["transfers"] == [
        {"from": "甲", "to": "乙", "object_id": "金币"},
        {"from": "乙", "to": "甲", "object_id": "钥匙"},
    ]


def test_semantic_gm_cannot_create_an_agreement_without_agent_reference():
    forged = {"operation": "propose", "agreement_id": "gm-invented"}
    result = AgreementActionResolver().resolve(
        {"resolved_actions": [], "agreement_updates": [forged]},
        intents=[], scenario=_scenario(), current_step=0,
    ).result

    assert result["agreement_updates"] == []
    assert result["contract_updates"] == []


def test_world_state_generates_delivery_service_with_optional_escrow():
    perception = AgentPerception(
        actor_name="委托人", step=2,
        agreement_opportunities=[{
            "opportunity_kind": "delivery_service_offer",
            "provider": "承运人", "recipient": "委托人",
            "service_object_options": ["包裹"],
            "destination_options": ["仓库"],
            "payment_options": ["报酬"],
            "deadline_options": ["urgent", "soon", "flexible"],
        }],
    )
    action = AgentAction(
        "communicate", "请在三步内把包裹交给我，完成后取得报酬。", "承运人",
        agreement_operation="propose",
        agreement_service_object="包裹",
        agreement_service_destination="仓库",
        agreement_payment_ref="报酬",
        agreement_deadline="soon",
    )
    reference = InputSystem._validated_agreement_reference(action, perception)
    intent = {
        "actor": "委托人", "intent": action.detail,
        "action_kind": "communicate", "action_target": "承运人",
        "action_agreement_operation": reference["operation"],
        "action_agreement_id": reference["agreement_id"],
        "action_agreement_template_id": "",
        "action_agreement_service_object": reference["service_object"],
        "action_agreement_service_destination": reference[
            "service_destination"
        ],
        "action_agreement_payment_ref": reference["payment_ref"],
        "action_agreement_deadline": reference["deadline"],
    }
    compiled = AgreementActionResolver().resolve(
        {"resolved_actions": [{
            "actor": "委托人", "outcome": "success", "location": "驿站",
        }]},
        intents=[intent], scenario=None, current_step=2,
    ).result

    update = compiled["agreement_updates"][0]
    assert update["services"][0]["due_after_steps"] == 3
    assert update["services"][0]["completion_conditions"][0] == {
        "scope": "world_object", "target": "包裹", "path": "location",
        "operator": "eq", "value": "仓库",
    }
    assert update["escrows"][0]["transfer"]["object_id"] == "报酬"

    scene = SceneState(
        world_objects={
            "驿站": {"connected_to": ["仓库"]},
            "仓库": {"connected_to": ["驿站"]},
            "包裹": {
                "is_location": False,
                "owner": "承运人", "location": None,
                "portable": True, "hidden": False,
            },
            "报酬": {
                "is_location": False,
                "owner": "委托人", "location": None,
                "portable": True, "hidden": False,
            },
        },
        actor_states={
            "委托人": {"location": "驿站"},
            "承运人": {"location": "驿站"},
        },
    )
    obligations = {"委托人": ObligationState(), "承运人": ObligationState()}
    proposed = ContractDynamics().resolve(
        AgreementBook(), scene, obligations, compiled,
        current_step=2, proposal_actors={"委托人"},
    )
    assert proposed.errors == []
    agreement_id = reference["agreement_id"]
    accepted = ContractDynamics().resolve(
        proposed.state, scene, obligations,
        {
            "resolved_actions": [{
                "actor": "承运人", "outcome": "success", "location": "驿站",
            }],
            "agreement_updates": [{
                "operation": "accept", "agreement_id": agreement_id,
                "actor": "承运人", "reason": "承运人接受委托",
            }],
        },
        current_step=3, proposal_actors={"承运人"},
    )
    assert accepted.errors == []
    settlement = accepted.result["contract_settlements"][0]
    assert settlement["obligation_updates"][0]["due_step"] == 6
    assert accepted.result["contract_escrow_deposits"][0]["transfer"][
        "object_id"
    ] == "报酬"
