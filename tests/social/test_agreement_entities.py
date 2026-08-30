from copy import deepcopy

from pydantic import Field

from src.story_engine.agents.types import AgentDecision
from src.story_engine.environment.agreement_offers import AgreementOfferEngine
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session
from src.story_engine.social import AgreementRegistry
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return deepcopy(self.scripted_result)


def _result(actor, updates):
    return {
        "resolved_actions": [
            {
                "actor": actor,
                "intent": "明确讨论协议",
                "outcome": "success",
                "location": "集市",
                "visibility": "public",
                "result": "角色明确表达了协议意见。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": [],
        "agreement_updates": updates,
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def _offer():
    return {
        "operation": "propose",
        "agreement_id": "token_for_key",
        "actor": "甲",
        "parties": ["甲", "乙"],
        "title": "令牌换钥匙",
        "summary": "甲用令牌换取乙的钥匙",
        "expires_step": 4,
        "transfers": [
            {"from": "甲", "to": "乙", "object_id": "甲的令牌"},
            {"from": "乙", "to": "甲", "object_id": "乙的钥匙"},
        ],
        "delegations": [],
        "services": [],
        "escrows": [],
        "reason": "甲明确提出交换条件",
    }


def _world():
    scene = SceneState(
        world_objects={
            "集市": {},
            "甲的令牌": {
                "is_location": False,
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "乙的钥匙": {
                "is_location": False,
                "owner": "乙",
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={"甲": {"location": "集市"}, "乙": {"location": "集市"}},
    )
    control = SimulationControl(scripted_result=_result("甲", [_offer()]))
    gm = Entity("GameMaster")
    gm.add_component(control)
    gm.add_component(scene)
    gm.add_component(DramaState())
    return gm, scene, control


def test_authoritative_offer_is_published_as_social_relation_entity_not_gm_component():
    gm, scene, control = _world()
    registry = AgreementRegistry()
    entities = {"GameMaster": gm, "甲": Entity("甲"), "乙": Entity("乙")}
    agreement_id = AgreementOfferEngine.asset_offer_id(
        "甲", "乙", ["甲的令牌"], ["乙的钥匙"], 0
    )
    context = {
        "clock": type("Clock", (), {"current_step": 0})(),
        "intents": [{
            "actor": "甲", "intent": "提出交换",
            "action_kind": "communicate", "action_target": "乙",
            "action_agreement_operation": "propose",
            "action_agreement_id": agreement_id,
            "action_agreement_template_id": "",
            "action_agreement_give_refs": ["甲的令牌"],
            "action_agreement_request_refs": ["乙的钥匙"],
        }],
        "agreement_registry": registry,
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert gm.get_component("ContractState") is None
    agreement = registry.get(agreement_id)
    assert agreement is entities[f"Agreement:{agreement_id}"]
    relation = agreement.get_component("SocialRelation")
    terms = agreement.get_component("AgreementTerms")
    lifecycle = agreement.get_component("AgreementLifecycle")
    assert relation.relation_kind == "agreement"
    assert relation.participants == ["甲", "乙"]
    assert relation.initiator == "甲"
    assert relation.visibility == "participants"
    assert relation.parent_relation_id == "pair:乙<->甲"
    pair = entities["Relationship:乙<->甲"]
    assert pair.get_component("RelationshipTracks") is not None
    assert pair.get_component("RelationshipBits") is not None
    assert terms.title == "资产报价"
    assert terms.summary == "提出交换"
    assert lifecycle.status == "pending"
    assert lifecycle.accepted_by == ["甲"]
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"

    control.scripted_result = _result(
        "乙",
        [
            {
                "operation": "accept",
                "agreement_id": "token_for_key",
                "actor": "乙",
                "reason": "乙明确接受交换",
            }
        ],
    )
    second = {
        "clock": type("Clock", (), {"current_step": 1})(),
        "intents": [{
            "actor": "乙", "intent": "接受交换",
            "action_kind": "communicate", "action_target": "甲",
            "action_agreement_operation": "accept",
            "action_agreement_id": agreement_id,
        }],
        "agreement_registry": registry,
    }
    SimulationSystem().update(entities, second)

    assert second["state_transaction"]["committed"] is True
    assert registry.get(agreement_id) is agreement
    assert agreement.get_component("AgreementLifecycle").status == "settled"
    assert scene.get_object_state("甲的令牌")["owner"] == "乙"
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"


def test_agreement_entity_is_not_scheduled_as_character_agent():
    gm, _, _ = _world()
    registry = AgreementRegistry()
    book = registry.to_book()
    from src.story_engine.social import AgreementRecord

    book.agreements["a"] = AgreementRecord(
        agreement_id="a",
        proposer="甲",
        parties=["甲", "乙"],
        accepted_by=["甲"],
        expires_step=3,
    )
    entities = {"GameMaster": gm}
    registry.apply_book(book, entities)
    context = {
        "agreement_registry": registry,
        "agent_registry": None,
        "overrides": {},
        "clock": type("Clock", (), {"current_step": 0})(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update(entities, context)

    assert "Agreement:a" not in context["agent_activations"]
    assert context.get("agent_registration_errors", []) == []


def test_new_session_gm_no_longer_owns_contract_state_component():
    scenario = ScenarioConfig(
        name="最小关系世界",
        default_agent_runtime="llm",
        description="测试关系实体边界",
        environment="空房间",
        initial_state="甲独自在场。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "房间"}},
        characters=[
            CharacterConfig(name="甲", role="访客", personality="平静", goals=[], agent_runtime="llm")
        ],
    )

    class _WaitingRuntime:
        def decide(self, _entity, _perception):
            return AgentDecision(action="安静等待。")

    session = create_session(
        scenario,
        agent_runtime_factories={"llm": lambda entity, cfg: _WaitingRuntime()},
    )
    gm = session.entities["GameMaster"]

    assert gm.get_component("ContractState") is None
    assert gm.get_component("RelationshipState") is None
    assert list(session.runner.agreement_registry.entities()) == []
