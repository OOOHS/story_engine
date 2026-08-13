from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.knowledge import ClaimRegistry
from src.story_engine.simulation import ClaimCommunicationResolver
from src.story_engine.systems.claim_knowledge import ClaimKnowledgeSystem
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _perception():
    return AgentPerception(
        actor_name="甲",
        step=1,
        world_view={
            "visible_actors": ["甲", "乙"],
            "visible_objects": ["账册"],
            "visible_world": {"账册": {"owner": "甲"}},
        },
        private_knowledge={
            "claims": [
                {
                    "claim_id": "ledger_owner",
                    "stance": "supports",
                    "evidence_refs": ["账册"],
                }
            ]
        },
    )


def test_input_allows_lie_but_requires_known_claim_and_presentable_evidence():
    action = AgentAction(
        "communicate",
        "向乙否认账册内容",
        "乙",
        claim_id="ledger_owner",
        claim_stance="rejects",
        evidence_refs=("账册",),
    )

    reference = InputSystem._validated_claim_communication_reference(
        action, _perception()
    )

    assert reference == {
        "claim_id": "ledger_owner",
        "claim_stance": "rejects",
        "evidence_refs": ("账册",),
    }
    assert InputSystem._validated_claim_communication_reference(
        AgentAction(
            "communicate",
            "编造另一命题",
            "乙",
            claim_id="invented",
            claim_stance="supports",
        ),
        _perception(),
    ) == {}
    assert InputSystem._validated_claim_communication_reference(
        AgentAction(
            "communicate",
            "引用不存在的证据",
            "乙",
            claim_id="ledger_owner",
            evidence_refs=("密信",),
        ),
        _perception(),
    ) == {}


def test_host_replaces_model_claim_transfer_with_validated_proposal():
    result = {
        "resolved_actions": [
            {"actor": "甲", "outcome": "success", "result": "甲向乙表态。"}
        ],
        "knowledge_updates": [
            {
                "source": "甲",
                "target": "乙",
                "claim_id": "forged",
                "asserted_stance": "supports",
            },
            {
                "source": "甲",
                "target": "乙",
                "event_id": "event:kept",
            },
        ],
    }
    intent = {
        "actor": "甲",
        "action_kind": "communicate",
        "action_target": "乙",
        "action_claim_id": "ledger_owner",
        "action_claim_stance": "rejects",
        "action_evidence_refs": ["账册"],
    }

    resolution = ClaimCommunicationResolver().resolve(result, intents=[intent])

    assert resolution.result["knowledge_updates"] == [
        {"source": "甲", "target": "乙", "event_id": "event:kept"},
        {
            "source": "甲",
            "target": "乙",
            "claim_id": "ledger_owner",
            "asserted_stance": "rejects",
            "cited_evidence": ["账册"],
            "reason": "Agent 在沟通 proposal 中明确表达了自己知道的 Claim",
        },
    ]
    assert resolution.traces[0]["status"] == (
        "host_claim_communication_materialized"
    )


def test_failed_communication_does_not_transfer_claim():
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "blocked"}],
        "knowledge_updates": [{"claim_id": "forged"}],
    }

    resolution = ClaimCommunicationResolver().resolve(
        result,
        intents=[
            {
                "actor": "甲",
                "action_kind": "communicate",
                "action_target": "乙",
                "action_claim_id": "ledger_owner",
                "action_claim_stance": "supports",
                "action_evidence_refs": [],
            }
        ],
    )

    assert resolution.result["knowledge_updates"] == []


def test_claim_reference_crosses_simulation_and_private_knowledge_boundary():
    scene = SceneState(
        world_objects={
            "房间": {},
            "账册": {
                "is_location": False,
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    gm.add_component(HostRuleSimulationControl(llm_config={}))
    first = Entity("甲")
    first.add_component(
        KnowledgeState.from_initial(
            [
                {
                    "claim_id": "ledger_owner",
                    "stance": "supports",
                    "confidence": 0.9,
                    "basis": "observed",
                    "evidence_refs": ["账册"],
                }
            ]
        )
    )
    second = Entity("乙")
    second.add_component(KnowledgeState())
    entities = {"GameMaster": gm, "甲": first, "乙": second}
    registry = ClaimRegistry()
    registry.seed(
        [
            {
                "claim_id": "ledger_owner",
                "statement": "乙秘密控制账册。",
                "visibility": "secret",
                "subjects": ["乙"],
                "supporting_evidence": ["账册"],
            }
        ],
        scene_state=scene,
        world_entities=entities,
    )
    context = {
        "intents": [
            {
                "actor": "甲",
                "intent": "向乙展示账册，但公开否认其中的指控。",
                "action_kind": "communicate",
                "action_target": "乙",
                "action_claim_id": "ledger_owner",
                "action_claim_stance": "rejects",
                "action_evidence_refs": ["账册"],
                "location": "房间",
            }
        ],
        "claim_registry": registry,
    }

    SimulationSystem().update(entities, context)
    assert context["state_transaction"]["committed"] is True
    ClaimKnowledgeSystem().update(entities, context)

    learned = second.get_component("KnowledgeState").claims["ledger_owner"]
    assert learned.stance == "rejects"
    assert learned.basis == "reported"
    assert learned.source == "甲"
    assert learned.evidence_refs == ["账册"]
    assert context["claim_communication_traces"][0]["status"] == (
        "host_claim_communication_materialized"
    )
