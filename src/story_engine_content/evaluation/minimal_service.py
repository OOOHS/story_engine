"""Evaluation content for agreement, service, escrow, breach and repair."""

import hashlib

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.core.component import Component
from src.story_engine.components.host_rule_simulation import HostRuleSimulationControl
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


CLIENT = "委托人"
COURIER = "承运人"
WORKSHOP = "驿站"
PAYMENT = "托管报酬"
PARCEL = "委托包裹"
COMPENSATION = "补偿凭证"
SERVICE_ID = "escrow_delivery"
COMPENSATION_ID = "breach_compensation"
OBLIGATION_ID = "deliver_parcel"


def _agreement(perception, agreement_id):
    for item in (
        list(perception.private_agreements.get("pending", []))
        + list(perception.private_agreements.get("recent_history", []))
    ):
        if not isinstance(item, dict):
            continue
        current_id = str(
            item.get("agreement_id") or item.get("contract_id") or ""
        )
        if current_id == agreement_id:
            return item
    return None


class ServicePolicyRuntime:
    """A content-owned runtime that commits to one action per turn.

    Both parties treat the paperwork as settled business: when the option in
    front of them is a formal move on the agreement -- propose, accept, offer
    compensation -- they take it, because that is what someone doing business
    does. Actually performing is not paperwork. The courier is described as
    wavering between his reputation, his payment and his own convenience, so
    whether he hands the parcel over now or stalls another round is a real
    judgement call, and that is where breach, late delivery and repair become
    reachable instead of scripted.
    """

    def __init__(self, seed=0):
        self._seed = seed

    def _choose(self, options, actor, step):
        digest = hashlib.sha256(
            f"{self._seed}|{actor}|{step}".encode("utf-8")
        ).hexdigest()
        return options[int(digest, 16) % len(options)]

    @staticmethod
    def _is_formal_move(action):
        return bool(action.agreement_operation)

    def decide(self, entity, perception):
        main = _agreement(perception, SERVICE_ID)
        repair = _agreement(perception, COMPENSATION_ID)
        if entity.name == CLIENT:
            candidates = self._client_candidates(main, repair)
        else:
            candidates = self._courier_candidates(main, repair)
        if self._is_formal_move(candidates[0]):
            action = candidates[0]
        else:
            action = self._choose(
                candidates,
                entity.name,
                int(getattr(perception, "step", 0) or 0),
            )
        return AgentDecision(
            action=action.detail,
            action_spec=action,
            thought="权衡报酬、期限、关系和违约后果。",
            metadata={
                "subject_runtime": True,
                "motive_refs": [
                    {
                        "kind": "goal",
                        "ref": (
                            "receive_parcel"
                            if entity.name == CLIENT
                            else "earn_payment"
                        ),
                    }
                ],
            },
        )

    @staticmethod
    def _client_candidates(main, repair):
        if main is None:
            return [
                AgentAction(
                    "communicate",
                    f"提出托管委托：完成{PARCEL}交付后释放{PAYMENT}。",
                    COURIER,
                    agreement_operation="propose",
                    agreement_template_id="service_offer",
                ),
                AgentAction("observe", f"观察{COURIER}是否有可靠履约的准备。", COURIER),
                AgentAction("wait", "暂缓提出委托，继续评估风险。"),
            ]
        status = str(main.get("status", ""))
        performance = str(main.get("performance_status", "none"))
        if status == "pending":
            return [
                AgentAction("communicate", "提醒承运人明确接受或拒绝托管委托。", COURIER),
                AgentAction("observe", "观察承运人面对条款时的反应。", COURIER),
                AgentAction("wait", "等待托管报价的正式回应。"),
            ]
        if performance == "pending":
            return [
                AgentAction("communicate", f"提醒{COURIER}按时交付{PARCEL}。", COURIER),
                AgentAction("observe", f"检查{PARCEL}是否已经准备交付。", PARCEL),
                AgentAction("wait", "等待服务期限推进。"),
            ]
        if performance == "breached":
            if repair and str(repair.get("status", "")) == "pending" and repair.get(
                "awaiting_actor"
            ):
                return [
                    AgentAction(
                        "communicate",
                        "接受承运人提出的补偿凭证。",
                        COURIER,
                        agreement_operation="accept",
                        agreement_id=COMPENSATION_ID,
                    ),
                    AgentAction(
                        "communicate",
                        "拒绝不足以弥补违约的补偿。",
                        COURIER,
                        agreement_operation="reject",
                        agreement_id=COMPENSATION_ID,
                    ),
                    AgentAction("wait", "暂不回应补偿报价。"),
                ]
            return [
                AgentAction("communicate", "要求承运人为逾期未交付提出明确补偿。", COURIER),
                AgentAction("communicate", "指责承运人违背了已经接受的责任。", COURIER),
                AgentAction("wait", "保留追责，等待对方先提出修复方案。"),
            ]
        return [
            AgentAction("communicate", "确认包裹已经交付，并评价这次合作。", COURIER),
            AgentAction("observe", f"检查收到的{PARCEL}是否完好。", PARCEL),
            AgentAction("wait", "让已经完成的交易暂时告一段落。"),
        ]

    @staticmethod
    def _courier_candidates(main, repair):
        if main is None:
            return [
                AgentAction("communicate", "询问委托的报酬、期限和托管条件。", CLIENT),
                AgentAction("observe", f"检查{PARCEL}是否适合立即交付。", PARCEL),
                AgentAction("wait", "等待委托人提出明确条款。"),
            ]
        status = str(main.get("status", ""))
        performance = str(main.get("performance_status", "none"))
        if status == "pending" and main.get("awaiting_actor"):
            return [
                AgentAction(
                    "communicate",
                    f"接受托管委托，承诺交付{PARCEL}并获得{PAYMENT}。",
                    CLIENT,
                    agreement_operation="accept",
                    agreement_id=SERVICE_ID,
                ),
                AgentAction("communicate", "请求调整当前托管委托，但暂不拒绝报价。", CLIENT),
                AgentAction("wait", "暂不接受报价，继续权衡期限。"),
            ]
        if performance == "pending":
            return [
                AgentAction(
                    "interact",
                    f"立即把{PARCEL}交给{CLIENT}，完成委托并获得{PAYMENT}。",
                    PARCEL,
                    delivery_recipient=CLIENT,
                ),
                AgentAction("communicate", "请求延后交付，但不宣称责任已经解除。", CLIENT),
                AgentAction("wait", "冒险拖延一轮，承担可能违约的后果。"),
            ]
        if performance == "breached":
            if repair is None:
                return [
                    AgentAction(
                        "communicate",
                        f"为违约提出把{COMPENSATION}交给{CLIENT}作为补偿。",
                        CLIENT,
                        agreement_operation="propose",
                        agreement_template_id="compensation_offer",
                    ),
                    AgentAction("communicate", "承认逾期，但暂时只作口头道歉。", CLIENT),
                    AgentAction("wait", "暂不提出补偿，承受关系恶化。"),
                ]
            return [
                AgentAction("communicate", "请委托人考虑已经提出的补偿方案。", CLIENT),
                AgentAction("wait", "等待补偿方案的回应。"),
            ]
        return [
            AgentAction("communicate", "确认履约完成并感谢委托人的信任。", CLIENT),
            AgentAction("wait", "完成委托后暂时休息。"),
        ]


class NarrativeRenderer(Component):
    def render(self, payload):
        facts = [
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ]
        return "；".join(facts) or "驿站里的委托暂时没有新变化。"


def build_minimal_service_scenario() -> ScenarioConfig:
    payment_owner = lambda owner: {
        "scope": "world_object",
        "target": PAYMENT,
        "path": "owner",
        "operator": "eq",
        "value": owner,
    }
    parcel_owner = lambda owner: {
        "scope": "world_object",
        "target": PARCEL,
        "path": "owner",
        "operator": "eq",
        "value": owner,
    }
    return ScenarioConfig(
        name="最小托管委托与履约",
        default_agent_runtime="service-policy",
        description="两名 Agent 自主协商托管服务、履约或承担违约后果。",
        environment="一座驿站，委托人带着报酬，承运人持有待交付包裹。",
        initial_state="双方尚未达成协议，报酬和包裹仍由各自持有。",
        initial_world_objects={
            WORKSHOP: {},
            PAYMENT: {
                "is_location": False,
                "kind": "payment",
                "owner": CLIENT,
                "hidden": False,
                "portable": True,
            },
            PARCEL: {
                "is_location": False,
                "kind": "parcel",
                "owner": COURIER,
                "hidden": False,
                "portable": True,
            },
            COMPENSATION: {
                "is_location": False,
                "kind": "token",
                "owner": COURIER,
                "hidden": False,
                "portable": True,
            },
        },
        initial_actor_states={
            CLIENT: {"location": WORKSHOP},
            COURIER: {"location": WORKSHOP},
        },
        agreement_offer_templates=[
            {
                "template_id": "service_offer",
                "agreement_id": SERVICE_ID,
                "proposer": CLIENT,
                "parties": [CLIENT, COURIER],
                "title": "托管报酬换取按时交付",
                "summary": "承运人按时交付包裹后获得托管报酬",
                "expires_after_steps": 8,
                "services": [
                    {
                        "actor": COURIER,
                        "creditor": CLIENT,
                        "obligation_id": OBLIGATION_ID,
                        "title": "按时交付包裹",
                        "summary": f"把{PARCEL}交给{CLIENT}",
                        "due_after_steps": 2,
                        "grace_steps": 0,
                        "wake_before_steps": 1,
                        "delegation_policy": "bilateral",
                        "completion_conditions": [parcel_owner(CLIENT)],
                    }
                ],
                "escrows": [
                    {
                        "transfer": {"from": CLIENT, "object_id": PAYMENT},
                        "release_to": COURIER,
                        "refund_to": CLIENT,
                        "release_on_service": OBLIGATION_ID,
                        "refund_on": ["breached", "cancelled"],
                    }
                ],
            },
            {
                "template_id": "compensation_offer",
                "agreement_id": COMPENSATION_ID,
                "proposer": COURIER,
                "parties": [COURIER, CLIENT],
                "title": "违约补偿",
                "summary": "承运人转交补偿凭证以修复部分损害",
                "expires_after_steps": 8,
                "transfers": [
                    {"from": COURIER, "to": CLIENT, "object_id": COMPENSATION}
                ],
            },
        ],
        characters=[
            CharacterConfig(
                name=CLIENT,
                role="需要可靠交付的委托人",
                personality="审慎，重视履约，也接受明确补偿",
                goals=[f"收到{PARCEL}"],
                goal_specs=[
                    {
                        "goal_id": "receive_parcel",
                        "title": f"收到{PARCEL}",
                        "priority": 0.9,
                        "completion_conditions": [parcel_owner(CLIENT)],
                        "failure_conditions": [],
                    }
                ],
                initial_traits=[
                    {
                        "trait_id": "contract_minded",
                        "intensity": 0.9,
                    }
                ],
                is_player=True,
                agent_runtime="service-policy",
            ),
            CharacterConfig(
                name=COURIER,
                role="决定是否承担限时交付的承运人",
                personality="在信誉、报酬和省力之间摇摆",
                goals=[f"获得{PAYMENT}"],
                goal_specs=[
                    {
                        "goal_id": "earn_payment",
                        "title": f"获得{PAYMENT}",
                        "priority": 0.9,
                        "completion_conditions": [payment_owner(COURIER)],
                        "failure_conditions": [
                            parcel_owner(CLIENT),
                            payment_owner(CLIENT),
                            {
                                "scope": "world_object",
                                "target": COMPENSATION,
                                "path": "owner",
                                "operator": "eq",
                                "value": CLIENT,
                            },
                        ],
                    }
                ],
                initial_traits=[
                    {
                        "trait_id": "mixed_reliability",
                        "intensity": 0.8,
                    }
                ],
                risk_tolerance=0.5,
                agent_runtime="service-policy",
            ),
        ],
    )


def create_minimal_service_session(seed):
    scenario = build_minimal_service_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "service-policy": lambda entity, config: ServicePolicyRuntime(seed)
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
