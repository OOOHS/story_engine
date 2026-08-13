"""A story-independent route delivery grown from world-derived capabilities."""

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.host_rule_simulation import HostRuleSimulationControl
from src.story_engine.core.component import Component
from src.story_engine.environment.physical_affordances import PhysicalAffordanceEngine
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


CLIENT = "商人"
COURIER = "旅脚夫"
MARKET = "市集"
BRIDGE = "石桥"
WAREHOUSE = "河岸仓库"
PARCEL = "封蜡货箱"
PAYMENT = "银币袋"


class DeliveryRouteRuntime:
    def decide(self, entity, perception):
        pending = list(perception.private_agreements.get("pending", []) or [])
        active_obligations = list(
            perception.private_obligations.get("active", []) or []
        )
        if entity.name == CLIENT:
            if not pending and not perception.private_agreements.get("recent_history"):
                return AgentDecision(
                    action="提出运送货箱的有偿委托。",
                    action_spec=AgentAction(
                        "communicate",
                        f"请把{PARCEL}送到{WAREHOUSE}，完成后取得{PAYMENT}。",
                        COURIER,
                        agreement_operation="propose",
                        agreement_service_object=PARCEL,
                        agreement_service_destination=WAREHOUSE,
                        agreement_payment_ref=PAYMENT,
                        agreement_deadline="flexible",
                    ),
                )
            return AgentDecision(
                action="等待承运结果。",
                action_spec=AgentAction("wait", "等待承运结果。"),
            )

        if pending and pending[0].get("awaiting_actor"):
            agreement_id = str(pending[0]["agreement_id"])
            return AgentDecision(
                action="接受运送委托。",
                action_spec=AgentAction(
                    "communicate",
                    "接受运送货箱到河岸仓库的委托。",
                    CLIENT,
                    agreement_operation="accept",
                    agreement_id=agreement_id,
                ),
            )
        if active_obligations:
            location = str(perception.world_view.get("location") or "")
            if location != WAREHOUSE:
                routes = perception.private_knowledge.get("map", {}).get(
                    "known_routes", {}
                )
                queue = [[location]]
                visited = {location}
                route = []
                while queue:
                    candidate = queue.pop(0)
                    if candidate[-1] == WAREHOUSE:
                        route = candidate
                        break
                    for neighbor in routes.get(candidate[-1], []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(candidate + [neighbor])
                next_location = route[1]
                return AgentDecision(
                    action=f"沿已知道路携带{PARCEL}前往{next_location}。",
                    action_spec=AgentAction(
                        "move",
                        f"沿已知道路携带{PARCEL}前往{next_location}。",
                        next_location,
                    ),
                )
            return AgentDecision(
                action=f"在{WAREHOUSE}放下{PARCEL}。",
                action_spec=AgentAction(
                    "interact",
                    f"在{WAREHOUSE}放下{PARCEL}。",
                    PARCEL,
                    PhysicalAffordanceEngine.DROP,
                ),
            )
        return AgentDecision(
            action="等待委托。",
            action_spec=AgentAction("wait", "等待委托。"),
        )


class NarrativeRenderer(Component):
    def render(self, payload):
        return "；".join(
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get("resolved_actions", [])
            if isinstance(item, dict) and item.get("result")
        ) or "道路上的局势暂时平静。"


def build_minimal_delivery_route_scenario() -> ScenarioConfig:
    return ScenarioConfig(
        name="无模板跨地点委托",
        description="角色从可见世界能力自然形成并履行跨地点有偿委托。",
        environment="市集与河岸仓库之间有一条公开道路。",
        initial_state="旅脚夫持有货箱，商人持有报酬，双方尚未订约。",
        initial_world_objects={
            MARKET: {"connected_to": [BRIDGE]},
            BRIDGE: {"connected_to": [MARKET, WAREHOUSE]},
            WAREHOUSE: {"connected_to": [BRIDGE]},
            PARCEL: {
                "is_location": False,
                "owner": COURIER,
                "location": None,
                "hidden": False,
                "portable": True,
            },
            PAYMENT: {
                "is_location": False,
                "owner": CLIENT,
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        initial_actor_states={
            CLIENT: {"location": MARKET},
            COURIER: {"location": MARKET},
        },
        characters=[
            CharacterConfig(
                name=CLIENT,
                role="需要把货箱运到仓库的商人",
                personality="愿意为明确完成的工作支付报酬",
                goals=[],
                is_player=True,
                agent_runtime="delivery-route",
                initial_known_locations=[BRIDGE, WAREHOUSE],
            ),
            CharacterConfig(
                name=COURIER,
                role="靠运送货物谋生的旅脚夫",
                personality="接受清楚且有报酬的短途工作",
                goals=[],
                agent_runtime="delivery-route",
                initial_known_locations=[BRIDGE, WAREHOUSE],
            ),
        ],
    )


def create_minimal_delivery_route_session(seed):
    scenario = build_minimal_delivery_route_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "delivery-route": lambda entity, config: DeliveryRouteRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
