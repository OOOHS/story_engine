"""A story-independent seed where stale knowledge grows a recovery goal."""

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.core.component import Component
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


ACTOR = "旅人"
START = "村口"
STALE_BRIDGE = "东桥"
DETOUR = "南路"
DESTINATION = "城镇"
SEED_GOAL = "reach-town"


class NavigationRecoveryRuntime:
    def decide(self, entity, perception):
        location = str(perception.world_view.get("location") or "")
        problems = list(perception.private_navigation.get("active", []) or [])
        active_goals = list(perception.private_goals.get("active", []) or [])
        recovery = next(
            (
                goal for goal in active_goals
                if goal.get("source_kind") == "navigation_problem"
            ),
            None,
        )
        if location == DESTINATION:
            return AgentDecision(
                action="抵达城镇后停下来整理行装。",
                action_spec=AgentAction("wait", "抵达城镇后停下来整理行装。"),
            )
        if recovery:
            return AgentDecision(
                action=f"沿已知替代路线继续前往{DESTINATION}。",
                action_spec=AgentAction(
                    "move", f"沿已知替代路线继续前往{DESTINATION}。", DESTINATION
                ),
            )
        if problems:
            problem = problems[0]
            return AgentDecision(
                action=f"放弃东桥，改走南路前往{DESTINATION}。",
                thought="旧路线已经失效，但我的地图上还有另一条路。",
                action_spec=AgentAction(
                    "move", f"放弃东桥，先改走{DETOUR}。", DETOUR
                ),
                metadata={
                    "goal_requests": [{
                        "operation": "adopt",
                        "title": f"绕路抵达{DESTINATION}",
                        "source_kind": "navigation_problem",
                        "source_ref": problem["problem_id"],
                        "reason": "道路中断后，我仍决定寻找已知替代路线抵达目的地。",
                        "resolution_kind": "reach_location",
                        "resolution_target": DESTINATION,
                    }]
                },
            )
        return AgentDecision(
            action=f"按记忆中的路线前往{DESTINATION}。",
            action_spec=AgentAction(
                "move", f"按记忆中的路线前往{DESTINATION}。", DESTINATION
            ),
        )


class NarrativeRenderer(Component):
    def render(self, payload):
        return "；".join(
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ) or "道路暂时安静。"


def build_minimal_navigation_recovery_scenario() -> ScenarioConfig:
    return ScenarioConfig(
        name="最小过时地图恢复",
        default_agent_runtime="navigation-recovery",
        # This harness swaps the GM to HostRuleSimulationControl after
        # session creation for deterministic evaluation; the director must
        # not sneak a live LLM call into an otherwise LLM-free host.
        narrative_director_enabled=False,
        description="角色遭遇过时路线后，自主形成绕路目标并继续行动。",
        environment="村口通往城镇的东桥已经断开，南路仍可通行。",
        initial_state="旅人仍记得东桥，也知道一条较远的南路。",
        initial_world_objects={
            START: {"connected_to": [DETOUR]},
            STALE_BRIDGE: {"connected_to": [DESTINATION]},
            DETOUR: {"connected_to": [START, DESTINATION]},
            DESTINATION: {"connected_to": [STALE_BRIDGE, DETOUR]},
        },
        initial_actor_states={ACTOR: {"location": START}},
        characters=[
            CharacterConfig(
                name=ACTOR,
                role="携带旧地图赶路的旅人",
                personality="遇到障碍时会根据自己知道的信息调整计划",
                goals=[f"抵达{DESTINATION}"],
                goal_specs=[{
                    "goal_id": SEED_GOAL,
                    "title": f"抵达{DESTINATION}",
                    "priority": 0.8,
                    "completion_conditions": [{
                        "scope": "actor",
                        "target": ACTOR,
                        "path": "location",
                        "operator": "eq",
                        "value": DESTINATION,
                    }],
                }],
                is_player=True,
                agent_runtime="navigation-recovery",
                initial_known_locations=[STALE_BRIDGE, DETOUR, DESTINATION],
            )
        ],
    )


def create_minimal_navigation_recovery_session(seed):
    scenario = build_minimal_navigation_recovery_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "navigation-recovery": lambda entity, config: (
                NavigationRecoveryRuntime()
            )
        },
    )
    knowledge = session.entities[ACTOR].get_component("KnowledgeState")
    knowledge.learn_reported_route(
        START,
        STALE_BRIDGE,
        reporter="旧地图",
        step=0,
    )
    gm = session.entities["WorldHost"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
