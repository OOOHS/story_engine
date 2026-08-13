"""A story-agnostic integration seed for investigation and social leverage.

This is evaluation content, not core engine logic. It deliberately uses no
Storylets or character-name checks outside its own content-owned runtimes and
semantic test resolver.
"""

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.core.component import Component
from src.story_engine.environment.physical_affordances import (
    PhysicalAffordanceEngine,
)
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


INVESTIGATOR = "调查者"
KEEPER = "保管人"
ROOM = "档案室"
LEDGER = "密封账册"
CLAIM_ID = "ledger_implicates_keeper"


class InvestigationPolicyRuntime:
    """Open candidates only; CharacterPolicy owns the seeded choice."""

    def decide(self, entity, perception):
        if entity.name == INVESTIGATOR:
            candidates = (
                AgentAction(
                    "observe",
                    f"仔细检查{LEDGER}的签押、封口和夹页。",
                    LEDGER,
                ),
                AgentAction(
                    "interact",
                    f"趁当前有机会把{LEDGER}拿到自己手中。",
                    LEDGER,
                    PhysicalAffordanceEngine.TAKE,
                ),
                AgentAction(
                    "communicate",
                    f"询问{KEEPER}为何一直回避谈论{LEDGER}。",
                    KEEPER,
                ),
                AgentAction("wait", "暂时观察局势，等待对方先暴露意图。"),
            )
        else:
            candidates = (
                AgentAction(
                    "interact",
                    f"把{LEDGER}收到自己手中，避免它继续暴露。",
                    LEDGER,
                    PhysicalAffordanceEngine.TAKE,
                ),
                AgentAction(
                    "communicate",
                    f"向{INVESTIGATOR}否认{LEDGER}与自己存在特殊联系。",
                    INVESTIGATOR,
                    claim_id=CLAIM_ID,
                    claim_stance="rejects",
                ),
                AgentAction(
                    "communicate",
                    f"试探{INVESTIGATOR}是否已经注意到{LEDGER}上的异常。",
                    INVESTIGATOR,
                ),
                AgentAction("wait", "保持镇定，不主动暴露更多信息。"),
            )
        return AgentDecision(
            action=candidates[0].detail,
            candidates=candidates,
            thought="根据自己的目标权衡调查、控制证据与交流。",
        )


class NarrativeRenderer(Component):
    def render(self, payload):
        facts = [
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ]
        return "；".join(facts) or "档案室里的局面暂时没有改变。"


def build_minimal_investigation_scenario() -> ScenarioConfig:
    owner_condition = lambda actor: {
        "scope": "world_object",
        "target": LEDGER,
        "path": "owner",
        "operator": "eq",
        "value": actor,
    }
    return ScenarioConfig(
        name="最小调查与证据争夺",
        description="两名角色围绕一项秘密 Claim 和唯一证据自主行动。",
        environment="一间安静的档案室，桌上放着一本可以被调查和拿取的密封账册。",
        initial_state="调查者刚到场，保管人知道账册会把自己与一笔隐秘交易联系起来。",
        initial_world_objects={
            ROOM: {},
            LEDGER: {
                "is_location": False,
                "kind": "document",
                "location": ROOM,
                "owner": None,
                "hidden": False,
                "portable": True,
            },
        },
        initial_actor_states={
            INVESTIGATOR: {"location": ROOM},
            KEEPER: {"location": ROOM},
        },
        claims=[
            {
                "claim_id": CLAIM_ID,
                "statement": "保管人曾通过这本账册参与一笔未公开的交易。",
                "initial_truth": "true",
                "visibility": "secret",
                "subjects": [KEEPER],
                "supporting_evidence": [LEDGER],
                "tags": ["investigation", "leverage"],
            }
        ],
        characters=[
            CharacterConfig(
                name=INVESTIGATOR,
                role="外来调查者",
                personality="好奇、谨慎，但不愿空手而归",
                goals=[f"取得{LEDGER}"],
                goal_specs=[
                    {
                        "goal_id": "secure_evidence",
                        "title": f"取得{LEDGER}",
                        "priority": 0.9,
                        "completion_conditions": [owner_condition(INVESTIGATOR)],
                        "failure_conditions": [owner_condition(KEEPER)],
                    }
                ],
                initial_traits=[
                    {
                        "trait_id": "curious",
                        "intensity": 0.9,
                        "policy_weights": {
                            "information": 0.55,
                            "interact": 0.35,
                            "cautious": 0.2,
                            "risk": 0.2,
                        },
                    }
                ],
                risk_tolerance=0.45,
                is_player=True,
                agent_runtime="investigation-policy",
                agent_config={"policy": {"temperature": 1.3}},
            ),
            CharacterConfig(
                name=KEEPER,
                role="档案保管人",
                personality="防备、善于否认，优先控制证据",
                goals=[f"保住{LEDGER}"],
                goal_specs=[
                    {
                        "goal_id": "retain_evidence",
                        "title": f"保住{LEDGER}",
                        "priority": 0.9,
                        "completion_conditions": [owner_condition(KEEPER)],
                        "failure_conditions": [owner_condition(INVESTIGATOR)],
                    }
                ],
                initial_traits=[
                    {
                        "trait_id": "guarded",
                        "intensity": 0.9,
                        "policy_weights": {
                            "interact": 0.2,
                            "deception": 0.55,
                            "confront": 0.2,
                            "information": 0.1,
                        },
                    }
                ],
                initial_claim_knowledge=[
                    {
                        "claim_id": CLAIM_ID,
                        "stance": "supports",
                        "confidence": 0.95,
                        "basis": "observed",
                        "source": "private_record",
                        "evidence_refs": [LEDGER],
                    }
                ],
                risk_tolerance=0.35,
                agent_runtime="investigation-policy",
                agent_config={"policy": {"temperature": 1.3}},
            ),
        ],
    )


def create_minimal_investigation_session(seed):
    scenario = build_minimal_investigation_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "investigation-policy": (
                lambda entity, config: InvestigationPolicyRuntime()
            )
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
