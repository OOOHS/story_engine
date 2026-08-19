"""A story-independent regression seed for consequences growing new goals."""

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


ACTOR = "旅人"
ROOM = "封闭房间"
CORRIDOR = "外侧走廊"
KEY = "旧钥匙"
SEED_GOAL = "secure_key"


class GoalGrowthRuntime:
    def decide(self, entity, perception):
        active = perception.private_goals.get("active", [])
        history = perception.private_goals.get("recent_history", [])
        agent_history = [item for item in history if item.get("origin") == "agent"]
        active_agent = next(
            (item for item in active if item.get("origin") == "agent"),
            None,
        )
        if active_agent:
            return AgentDecision(
                action=f"带着{KEY}前往{CORRIDOR}。",
                thought="取得钥匙只是第一步，现在应该离开这里。",
                action_spec=AgentAction("move", f"带着{KEY}前往{CORRIDOR}。", CORRIDOR),
            )
        seed_resolution = next(
            (item for item in history if item.get("goal_id") == SEED_GOAL),
            None,
        )
        if seed_resolution and not agent_history:
            return AgentDecision(
                action=f"带着{KEY}前往{CORRIDOR}。",
                thought="取得钥匙后，留在原地已经没有意义。",
                action_spec=AgentAction("move", f"带着{KEY}前往{CORRIDOR}。", CORRIDOR),
                metadata={
                    "goal_requests": [
                        {
                            "operation": "adopt",
                            "title": f"带着{KEY}离开{ROOM}",
                            "source_kind": "resolved_goal",
                            "source_ref": SEED_GOAL,
                            "reason": "取得钥匙自然产生了离开封闭空间的下一步意图",
                            "resolution_kind": "reach_location",
                            "resolution_target": CORRIDOR,
                        }
                    ]
                },
            )
        if active:
            return AgentDecision(
                action=f"伸手取得{KEY}。",
                thought="先拿到能够离开这里的钥匙。",
                action_spec=AgentAction(
                    "interact",
                    f"伸手取得{KEY}。",
                    KEY,
                    PhysicalAffordanceEngine.TAKE,
                ),
            )
        return AgentDecision(
            action="暂时停下来观察。",
            action_spec=AgentAction("wait", "暂时停下来观察。"),
        )


class NarrativeRenderer(Component):
    def render(self, payload):
        return "；".join(
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ) or "局面暂时没有变化。"


def build_minimal_goal_growth_scenario() -> ScenarioConfig:
    return ScenarioConfig(
        name="最小目标生长",
        default_agent_runtime="goal-growth",
        description="一个已完成目标自然产生下一步私人追求。",
        environment="一间有出口的封闭房间。",
        initial_state="旅人需要先取得旧钥匙。",
        initial_world_objects={
            ROOM: {"connected_to": [CORRIDOR]},
            CORRIDOR: {"connected_to": [ROOM]},
            KEY: {
                "is_location": False,
                "kind": "key",
                "location": ROOM,
                "owner": None,
                "hidden": False,
                "portable": True,
            },
        },
        initial_actor_states={ACTOR: {"location": ROOM}},
        characters=[
            CharacterConfig(
                name=ACTOR,
                role="被困的旅人",
                personality="务实，会根据已经发生的结果调整下一步打算",
                goals=[f"取得{KEY}"],
                goal_specs=[
                    {
                        "goal_id": SEED_GOAL,
                        "title": f"取得{KEY}",
                        "priority": 0.9,
                        "completion_conditions": [
                            {
                                "scope": "world_object",
                                "target": KEY,
                                "path": "owner",
                                "operator": "eq",
                                "value": ACTOR,
                            }
                        ],
                    }
                ],
                is_player=True,
                agent_runtime="goal-growth",
            )
        ],
    )


def create_minimal_goal_growth_session(seed):
    scenario = build_minimal_goal_growth_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "goal-growth": lambda entity, config: GoalGrowthRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
