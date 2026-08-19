from types import SimpleNamespace

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.policy import CharacterPolicy
from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.goal_state import GoalState
from src.story_engine.components.host_rule_narrative import HostRuleNarrativeRenderer
from src.story_engine.components.host_rule_simulation import HostRuleSimulationControl
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.identity import Identity
from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.navigation_state import (
    NavigationProblem,
    NavigationState,
)
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.relationship import RelationshipBit
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.world_event import WorldEventFact
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.physical_affordances import PhysicalAffordanceEngine
from src.story_engine.evaluation import EpisodeClosurePolicy, EpisodeRunner
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session
from src.story_engine.knowledge import ClaimRegistry
from src.story_engine.social import (
    AgreementBook,
    AgreementRecord,
    AgreementRegistry,
    SocialRelationRegistry,
)
from src.story_engine.systems.goals import GoalSystem
from src.story_engine.systems.input import InputSystem


def test_event_response_can_ground_a_private_follow_up_goal():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    cognition = Cognition()
    response_id = "event-response:door-opened:乙->甲:apologize"
    cognition.record_event_response(
        response_id=response_id,
        event_id="door-opened",
        source="乙",
        response_kind="apologize",
        statement="乙为擅自开门道歉。",
        step=2,
        location="大厅",
    )
    cognition.acknowledge_event_responses([response_id])
    actor.add_component(goals)
    actor.add_component(cognition)
    context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "决定是否接受乙的道歉",
                "source_kind": "event_response",
                "source_ref": response_id,
                "reason": "乙的道歉要求甲重新决定双方关系",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.source_kind == "event_response"
    assert goal.source_ref == response_id
    assert context["goal_errors"] == []


def test_event_response_goal_source_cannot_use_another_characters_private_response():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("丙")
    actor.add_component(GoalState.from_initial([]))
    actor.add_component(Cognition())
    context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "丙",
                "operation": "adopt",
                "title": "利用甲收到的道歉",
                "source_kind": "event_response",
                "source_ref": "event-response:door-opened:乙->甲:apologize",
                "reason": "试图引用未进入自己认知的回应",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "丙": actor}, context)

    assert actor.get_component("GoalState").goals == {}
    assert context["goal_errors"] == [
        "丙:agent goal source is not present in the actor's private state"
    ]


def _condition(*, path, value, scope="scene", target=None, operator="eq"):
    return {
        "scope": scope,
        "target": target,
        "path": path,
        "operator": operator,
        "value": value,
    }


def test_plain_language_goal_guides_behavior_without_self_certifying_completion():
    state = GoalState.from_initial(["找到失踪的钥匙"])
    scene = SceneState(scene_flags={"key_found": True})

    transitions, errors = state.advance_to(step=3, scene_state=scene)

    assert transitions == []
    assert errors == []
    assert state.goals["goal-1"].status == "active"
    assert state.get_private_snapshot()["active"][0]["title"] == "找到失踪的钥匙"


def test_goal_resolves_only_when_authoritative_completion_or_failure_locks_match():
    achieved = GoalState.from_initial(
        [],
        [
            {
                "goal_id": "recover_key",
                "title": "取回钥匙",
                "completion_conditions": [
                    _condition(
                        scope="world_object",
                        target="钥匙",
                        path="owner",
                        value="甲",
                    )
                ],
                "failure_conditions": [
                    _condition(path="scene_flags.key_destroyed", value=True)
                ],
            }
        ],
    )
    scene = SceneState(world_objects={"钥匙": {"is_location": False, "owner": "甲"}})

    transitions, errors = achieved.advance_to(step=4, scene_state=scene)

    assert errors == []
    assert transitions[0]["status"] == "achieved"
    assert achieved.goals["recover_key"].resolved_step == 4

    failed = GoalState.from_initial(
        [],
        [
            {
                "goal_id": "protect_letter",
                "title": "保护信件",
                "failure_conditions": [
                    _condition(path="scene_flags.letter_burned", value=True)
                ],
            }
        ],
    )
    failure_scene = SceneState(scene_flags={"letter_burned": True})

    transitions, errors = failed.advance_to(step=5, scene_state=failure_scene)

    assert errors == []
    assert transitions[0]["status"] == "failed"


def test_conflicting_goal_locks_are_reported_without_arbitrary_resolution():
    state = GoalState.from_initial(
        [],
        [
            {
                "goal_id": "contradiction",
                "title": "矛盾目标",
                "completion_conditions": [
                    _condition(path="scene_flags.done", value=True)
                ],
                "failure_conditions": [
                    _condition(path="scene_flags.done", value=True)
                ],
            }
        ],
    )

    transitions, errors = state.advance_to(
        step=2,
        scene_state=SceneState(scene_flags={"done": True}),
    )

    assert transitions == []
    assert errors == ["goal conditions are simultaneously true: contradiction"]
    assert state.goals["contradiction"].status == "active"


def test_semantic_resolver_cannot_write_goal_status_directly():
    gm = Entity("GameMaster")
    scene = SceneState(scene_flags={"door_open": False})
    gm.add_component(scene)
    gm.add_component(PlotState())
    actor = Entity("甲")
    state = GoalState.from_initial(
        [],
        [
            {
                "goal_id": "open_door",
                "title": "打开门",
                "completion_conditions": [
                    _condition(path="scene_flags.door_open", value=True)
                ],
            }
        ],
    )
    actor.add_component(state)
    context = {
        "clock": SimpleNamespace(current_step=1),
        "simulation_result": {
            "goal_updates": [
                {"actor": "甲", "goal_id": "open_door", "status": "achieved"}
            ]
        },
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert context["goal_transitions"] == []
    assert state.goals["open_door"].status == "active"

    scene.update_scene_flags({"door_open": True})
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert context["goal_transitions"][0]["status"] == "achieved"


def test_agents_receive_goal_lifecycle_without_exact_host_locks():
    state = GoalState.from_initial(
        [],
        [
            {
                "goal_id": "escape",
                "title": "离开房间",
                "priority": 0.9,
                "completion_conditions": [
                    _condition(scope="actor", target="甲", path="location", value="街道")
                ],
            }
        ],
    )

    snapshot = state.get_private_snapshot()

    assert snapshot["active"][0]["has_completion_evidence_rule"] is True
    assert "completion_conditions" not in snapshot["active"][0]


def test_host_policy_stops_scoring_a_resolved_goal():
    entity = Entity("甲")
    entity.add_component(
        Identity(
            name="甲",
            role="访客",
            personality="专注",
            goals=["寻找钥匙"],
        )
    )
    state = GoalState.from_initial(["寻找钥匙"])
    entity.add_component(state)
    policy = CharacterPolicy()
    candidate = policy._candidate(
        "test",
        AgentAction("observe", "仔细寻找钥匙。", "钥匙"),
        "runtime",
        base_utility=0.0,
    )

    assert policy._goal_score(entity, candidate) > 0

    state.goals["goal-1"].status = "achieved"

    assert policy._goal_score(entity, candidate) == 0


def test_every_created_character_gets_goal_state_even_without_structured_rules():
    entity = create_agent(
        name="甲",
        role="旅人",
        personality="好奇",
        goals=["寻找出路"],
        agent_runtime="llm",
    )

    state = entity.get_component("GoalState")
    assert state is not None
    assert state.goals["goal-1"].title == "寻找出路"


def test_scenario_goal_specs_reach_character_goal_state():
    scenario = ScenarioConfig(
        name="目标种子",
        default_agent_runtime="llm",
        description="验证目标配置。",
        environment="房间",
        initial_state="门尚未打开。",
        initial_world_objects={"房间": {}},
        initial_actor_states={"甲": {"location": "房间"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="访客",
                personality="坚定",
                goals=["离开房间"],
                goal_specs=[
                    {
                        "goal_id": "leave_room",
                        "title": "离开房间",
                        "priority": 0.9,
                        "completion_conditions": [
                            _condition(
                                scope="actor",
                                target="甲",
                                path="location",
                                value="街道",
                            )
                        ],
                    }
                ],
                is_player=True,
                agent_runtime="test",
            )
        ],
    )
    session = create_session(
        scenario,
        agent_runtime_factories={"test": lambda entity, config: object()},
    )

    state = session.entities["甲"].get_component("GoalState")
    assert list(state.goals) == ["leave_room"]
    assert state.goals["leave_room"].priority == 0.9
    assert state.goals["leave_room"].completion_conditions[0]["scope"] == "actor"


def test_agent_can_grow_a_secondary_goal_from_host_resolved_goal():
    class SimulationControl(Component):
        pass

    class SecondaryGoalRuntime:
        def decide(self, entity, perception):
            history = perception.private_goals.get("recent_history", [])
            assert history[0]["goal_id"] == "first_goal"
            return AgentDecision(
                action="整理下一步计划。",
                thought="第一件事结束后，我有了新的追求。",
                metadata={
                    "goal_requests": [
                        {
                            "operation": "adopt",
                            "title": "把得到的线索交给可信的人",
                            "source_kind": "resolved_goal",
                            "source_ref": "first_goal",
                            "reason": "原目标的结果自然产生了后续责任",
                            "priority": 1.0,
                            "actor": "伪造角色",
                            "resolution_kind": "reach_location",
                            "resolution_target": "走廊",
                            "completion_conditions": [
                                _condition(path="scene_flags.forged", value=True)
                            ],
                            "_host_perception": {
                                "location": "走廊",
                                "visible_world": {},
                            },
                        }
                    ]
                },
            )

    scene = SceneState(
        world_objects={
            "房间": {"connected_to": ["走廊"]},
            "走廊": {"connected_to": ["房间"]},
        },
        actor_states={"甲": {"location": "房间"}},
        scene_flags={"first_done": True},
    )
    actor = create_agent(
        name="甲",
        role="调查者",
        personality="负责",
        goals=[],
        goal_specs=[
            {
                "goal_id": "first_goal",
                "title": "取得线索",
                "completion_conditions": [
                    _condition(path="scene_flags.first_done", value=True)
                ],
            }
        ],
        agent_runtime="test",
    )
    state = actor.get_component("GoalState")
    state.advance_to(step=3, scene_state=scene)
    gm = Entity("GameMaster")
    marker = SimulationControl()
    gm.add_component(marker)
    gm.add_component(scene)
    registry = AgentRegistry()
    registry.register(actor, SecondaryGoalRuntime())
    context = {
        "agent_registry": registry,
        "intents": [],
        "overrides": {},
        "clock": SimpleNamespace(current_step=4),
        "player_name": "甲",
        "allow_auto_player": True,
        "random_seed": 7,
    }

    InputSystem().update({"GameMaster": gm, "甲": actor}, context)
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    adopted = [
        record for record in state.goals.values() if record.origin == "agent"
    ]
    assert len(adopted) == 1
    assert adopted[0].title == "把得到的线索交给可信的人"
    assert adopted[0].priority == 0.65
    assert adopted[0].completion_conditions == [
        _condition(
            scope="actor",
            target="甲",
            path="location",
            value="走廊",
        )
    ]
    assert context["agent_goal_requests"][0]["actor"] == "甲"
    assert context["agent_goal_requests"][0]["_host_perception"]["location"] == "房间"
    assert context["goal_transitions"][-1]["status"] == "adopted"

    scene.update_actor_state("甲", {"location": "走廊"})
    context["clock"] = SimpleNamespace(current_step=5)
    context["agent_goal_requests"] = []
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert adopted[0].status == "achieved"
    assert context["goal_transitions"][-1]["status"] == "achieved"


def test_open_agent_goal_can_be_refined_into_host_verifiable_resolution():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "房间": {},
            "旧地图": {
                "is_location": False,
                "location": "房间",
                "portable": True,
                "hidden": False,
            },
            "钥匙": {
                "is_location": False,
                "location": "房间",
                "portable": True,
                "hidden": False,
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState()
    actor.add_component(goals)
    adopted, error = goals.adopt_agent_goal(
        title="找到进入旧仓库的方法",
        description="地图暗示那里藏着重要东西",
        source_kind="visible_object",
        source_ref="旧地图",
        priority=0.5,
        step=1,
    )
    assert error == ""
    goal_id = adopted["goal_id"]
    assert goals.goals[goal_id].completion_conditions == []

    context = {
        "clock": SimpleNamespace(current_step=2),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "refine",
                "goal_id": goal_id,
                "resolution_kind": "possess_object",
                "resolution_target": "钥匙",
                "_host_perception": {
                    "location": "房间",
                    "visible_actors": ["甲"],
                    "visible_world": {
                        "房间": scene.get_object_state("房间"),
                        "旧地图": scene.get_object_state("旧地图"),
                        "钥匙": scene.get_object_state("钥匙"),
                    },
                    "affordances": [],
                },
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert len(goals.goals) == 1
    goal = goals.goals[goal_id]
    assert context["goal_transitions"] == [
        {
            "actor": "甲",
            "goal_id": goal_id,
            "status": "refined",
            "title": "找到进入旧仓库的方法",
            "created_step": 1,
            "refined_step": 2,
            "reason": "host compiled authoritative resolution rules",
        }
    ]
    assert goal.completion_conditions == [
        {
            "scope": "world_object",
            "target": "钥匙",
            "path": "owner",
            "operator": "eq",
            "value": "甲",
        }
    ]

    scene.update_object_state("钥匙", {"owner": "甲", "location": None})
    GoalSystem().update(
        {"GameMaster": gm, "甲": actor},
        {"clock": SimpleNamespace(current_step=3), "agent_goal_requests": []},
    )

    assert goal.status == "achieved"


def test_goal_refinement_cannot_rewrite_authored_or_already_bound_goal():
    authored = GoalState.from_initial(["保护家园"])
    transition, error = authored.refine_agent_goal(
        goal_id="goal-1",
        step=2,
        completion_conditions=[_condition(path="scene_flags.safe", value=True)],
    )
    assert transition is None
    assert error == "authored goals cannot be refined by an agent"

    agent_goals = GoalState()
    adopted, error = agent_goals.adopt_agent_goal(
        title="取得钥匙",
        description="需要开门",
        source_kind="visible_object",
        source_ref="钥匙",
        priority=0.5,
        step=1,
        completion_conditions=[
            _condition(
                scope="world_object",
                target="钥匙",
                path="owner",
                value="甲",
            )
        ],
    )
    assert error == ""
    transition, error = agent_goals.refine_agent_goal(
        goal_id=adopted["goal_id"],
        step=2,
        completion_conditions=[_condition(path="scene_flags.forged", value=True)],
    )
    assert transition is None
    assert error == "agent goal already has authoritative resolution rules"


def test_use_affordance_goal_resolves_only_after_matching_host_event():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "厨房": {},
            "面包": {
                "is_location": False,
                "location": "厨房",
                "affordances": [
                    {"id": "eat", "consumes": True, "need_effects": {}}
                ],
            },
        },
        actor_states={"甲": {"location": "厨房"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    actor.add_component(goals)
    entities = {"GameMaster": gm, "甲": actor}
    context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "吃掉面包缓解饥饿",
                "source_kind": "visible_object",
                "source_ref": "面包",
                "resolution_kind": "use_affordance",
                "resolution_target": "面包",
                "resolution_affordance": "eat",
                "_host_perception": {
                    "location": "厨房",
                    "visible_actors": [],
                    "visible_world": {"面包": scene.get_object_state("面包")},
                    "affordances": [
                        {"object_id": "面包", "affordance_id": "eat"}
                    ],
                },
            }
        ],
    }

    GoalSystem().update(entities, context)

    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"
    assert goal.completion_conditions == [
        {
            "scope": "affordance_event",
            "target": "面包",
            "path": "metadata.affordance_id",
            "operator": "eq",
            "value": "eat",
            "actor": "甲",
            "min_step": 3,
        }
    ]

    event = Entity("WorldEvent:object:4:0:use:面包")
    event.add_component(
        WorldEventFact(
            event_id="object:4:0:use:面包",
            kind="object_use",
            title="面包被使用",
            statement="甲吃掉了面包。",
            occurred_step=4,
            location="厨房",
            subjects=["甲"],
            objects=["面包"],
            source_type="resolved_action",
            source_ref="step:4:actor:甲",
            metadata={"affordance_id": "eat"},
        )
    )
    entities[event.name] = event
    context.update(
        clock=SimpleNamespace(current_step=4),
        agent_goal_requests=[],
    )

    GoalSystem().update(entities, context)

    assert goal.status == "achieved"
    assert context["goal_transitions"][-1]["status"] == "achieved"


def test_use_affordance_goal_rejects_forged_event_provenance():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    transition, error = goals.adopt_agent_goal(
        title="使用面包",
        description="等待真实使用证据",
        source_kind="visible_object",
        source_ref="面包",
        priority=0.5,
        step=3,
        completion_conditions=[
            {
                "scope": "affordance_event",
                "target": "面包",
                "path": "metadata.affordance_id",
                "operator": "eq",
                "value": "eat",
                "actor": "甲",
                "min_step": 3,
            }
        ],
    )
    actor.add_component(goals)
    forged = Entity("WorldEvent:object:4:0:use:面包")
    forged.add_component(
        WorldEventFact(
            event_id="object:4:0:use:面包",
            kind="object_use",
            title="伪造使用",
            statement="有人声称甲使用了面包。",
            occurred_step=4,
            subjects=["甲"],
            objects=["面包"],
            source_type="resolved_action",
            source_ref="step:4:actor:乙",
            metadata={"affordance_id": "eat"},
        )
    )
    context = {
        "clock": SimpleNamespace(current_step=4),
        "agent_goal_requests": [],
    }

    GoalSystem().update(
        {"GameMaster": gm, "甲": actor, forged.name: forged},
        context,
    )

    assert transition is not None and error == ""
    assert goals.goals[transition["goal_id"]].status == "active"
    assert context["goal_transitions"] == []


def test_use_affordance_goal_rejects_an_unobserved_capability():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "厨房": {},
            "面包": {"is_location": False, "location": "厨房"},
        },
        actor_states={"甲": {"location": "厨房"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    actor.add_component(GoalState.from_initial([]))
    context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "凭空使用面包能力",
                "source_kind": "visible_object",
                "source_ref": "面包",
                "resolution_kind": "use_affordance",
                "resolution_target": "面包",
                "resolution_affordance": "teleport",
                "_host_perception": {
                    "location": "厨房",
                    "visible_actors": [],
                    "visible_world": {"面包": scene.get_object_state("面包")},
                    "affordances": [],
                },
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert actor.get_component("GoalState").goals == {}
    assert context["goal_errors"] == [
        "甲:use_affordance is not available in the actor's perception"
    ]


def test_recurring_drive_need_can_create_a_new_goal_after_prior_resolution():
    state = GoalState.from_initial([])

    first, first_error = state.adopt_agent_goal(
        title="寻找食物",
        description="饥饿需要处理",
        source_kind="drive_need",
        source_ref="hunger",
        priority=0.8,
        step=1,
    )
    duplicate, duplicate_error = state.adopt_agent_goal(
        title="寻找食物",
        description="仍然饥饿",
        source_kind="drive_need",
        source_ref="hunger",
        priority=0.8,
        step=3,
    )

    assert first is not None and first_error == ""
    assert duplicate is None
    assert duplicate_error == "agent goal duplicates an existing goal"

    first_record = state.goals[first["goal_id"]]
    first_record.status = "achieved"
    first_record.resolved_step = 2
    second, second_error = state.adopt_agent_goal(
        title="寻找食物",
        description="新的饥饿周期再次需要处理",
        source_kind="drive_need",
        source_ref="hunger",
        priority=0.7,
        step=4,
    )

    assert second is not None and second_error == ""
    assert second["goal_id"] != first["goal_id"]
    assert len(state.goals) == 2


def test_small_seed_grows_and_closes_an_affordance_goal_end_to_end():
    class EatWhenHungryRuntime:
        def decide(self, entity, perception):
            del entity
            active_agent_goals = [
                item
                for item in perception.private_goals.get("active", [])
                if item.get("origin") == "agent"
            ]
            if active_agent_goals:
                action = AgentAction("wait", "吃完以后稍作休息。")
                return AgentDecision(action=action.detail, action_spec=action)
            eat = next(
                (
                    item
                    for item in perception.affordance_opportunities
                    if item.get("object_id") == "面包"
                    and item.get("affordance_id") == "eat"
                ),
                None,
            )
            if eat:
                action = AgentAction("interact", "吃掉眼前的面包。", "面包", "eat")
                return AgentDecision(
                    action=action.detail,
                    action_spec=action,
                    metadata={
                        "goal_requests": [
                            {
                                "operation": "adopt",
                                "title": "吃掉面包缓解饥饿",
                                "source_kind": "drive_need",
                                "source_ref": "hunger",
                                "reason": "饥饿使眼前的食物成为直接目标",
                                "resolution_kind": "use_affordance",
                                "resolution_target": "面包",
                                "resolution_affordance": "eat",
                            }
                        ]
                    },
                )
            action = AgentAction("wait", "没有食物可用，只能等待。")
            return AgentDecision(action=action.detail, action_spec=action)

    scenario = ScenarioConfig(
        name="最小能力目标生长",
        default_agent_runtime="llm",
        description="饥饿角色看到食物后形成并完成一个目标。",
        environment="只有一间厨房和一块面包。",
        initial_state="旅人感到饥饿。",
        initial_world_objects={
            "厨房": {},
            "面包": {
                "is_location": False,
                "location": "厨房",
                "portable": True,
                "quantity": 1,
                "affordances": [
                    {
                        "id": "eat",
                        "consumes": True,
                        "need_effects": {"hunger": -1.0},
                    }
                ],
            },
        },
        initial_actor_states={"旅人": {"location": "厨房"}},
        characters=[
            CharacterConfig(
                name="旅人",
                role="饥饿的旅人",
                personality="务实",
                goals=[],
                initial_needs=[
                    {
                        "name": "hunger",
                        "pressure": 0.9,
                        "drift_per_turn": 0.0,
                        "critical_threshold": 0.8,
                        "description": "需要食物",
                    }
                ],
                is_player=True,
                agent_runtime="eat-when-hungry",
            )
        ],
    )
    session = create_session(
        scenario,
        random_seed="affordance-growth",
        agent_runtime_factories={
            "eat-when-hungry": lambda entity, config: EatWhenHungryRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=scenario))
    gm.add_component(HostRuleNarrativeRenderer(scenario=scenario))

    report = EpisodeRunner().run(
        session,
        steps=8,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    goals = session.entities["旅人"].get_component("GoalState")
    agent_goals = [
        record for record in goals.goals.values() if record.origin == "agent"
    ]
    events = [
        entity.get_component("WorldEventFact")
        for entity in session.entities.values()
        if entity.get_component("WorldEventFact") is not None
    ]
    assert report.authoritative is True, report.violations
    assert report.closure_reached is True
    assert len(agent_goals) == 1
    assert agent_goals[0].status == "achieved"
    assert agent_goals[0].source_kind == "drive_need"
    assert agent_goals[0].source_ref == "hunger"
    assert "面包" not in session.entities["GameMaster"].get_component(
        "SceneState"
    ).world_objects
    assert any(
        event.kind == "object_use"
        and event.metadata.get("affordance_id") == "eat"
        for event in events
    )
    assert report.metrics["agent_goal_adoption_count"] == 1
    assert report.metrics["goal_achievement_count"] == 1
    assert report.metrics["world_event_creation_count"] >= 1
    assert session.entities["旅人"].get_component(
        "DriveState"
    ).needs["hunger"].pressure == 0.0
    assert any(
        edge.startswith("goal:旅人:")
        and edge.endswith("<-drive_need:旅人:hunger")
        for step in report.steps
        for edge in step.causal_handoffs
    )


def test_possess_object_goal_template_requires_a_visible_portable_object():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "房间": {},
            "钥匙": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "hidden": False,
                "portable": True,
            },
            "暗格里的信": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "hidden": True,
                "portable": True,
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    state = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成前置调查",
                "completion_conditions": [
                    _condition(path="scene_flags.done", value=True)
                ],
            }
        ]
    )
    state.goals["seed"].status = "achieved"
    actor.add_component(state)
    context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "取得钥匙",
                "source_kind": "resolved_goal",
                "source_ref": "seed",
                "resolution_kind": "possess_object",
                "resolution_target": "钥匙",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    adopted = next(record for record in state.goals.values() if record.origin == "agent")
    assert adopted.completion_conditions[0]["target"] == "钥匙"
    assert adopted.completion_conditions[0]["value"] == "甲"

    other = Entity("乙")
    other_state = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成前置调查",
                "completion_conditions": [
                    _condition(path="scene_flags.done", value=True)
                ],
            }
        ]
    )
    other_state.goals["seed"].status = "achieved"
    other.add_component(other_state)
    scene.update_actor_state("乙", {"location": "房间"})
    hidden_context = {
        "clock": SimpleNamespace(current_step=3),
        "agent_goal_requests": [
            {
                "actor": "乙",
                "operation": "adopt",
                "title": "取得暗格里的信",
                "source_kind": "resolved_goal",
                "source_ref": "seed",
                "resolution_kind": "possess_object",
                "resolution_target": "暗格里的信",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "乙": other}, hidden_context)

    assert all(record.origin != "agent" for record in other_state.goals.values())
    assert "not currently visible" in hidden_context["goal_errors"][0]


def test_object_goal_fails_only_after_target_authoritatively_ceases_to_exist():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "房间": {},
            "钥匙": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "portable": True,
                "hidden": False,
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成前置目标",
                "completion_conditions": [
                    _condition(path="scene_flags.done", value=True)
                ],
            }
        ]
    )
    goals.goals["seed"].status = "achieved"
    actor.add_component(goals)
    context = {
        "clock": SimpleNamespace(current_step=1),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "取得钥匙",
                "source_kind": "resolved_goal",
                "source_ref": "seed",
                "resolution_kind": "possess_object",
                "resolution_target": "钥匙",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"
    assert goal.failure_conditions == [
        {
            "scope": "world_object",
            "target": "钥匙",
            "path": "",
            "operator": "not_exists",
            "value": None,
        }
    ]

    scene.world_objects.pop("钥匙")
    GoalSystem().update(
        {"GameMaster": gm, "甲": actor},
        {"clock": SimpleNamespace(current_step=2), "agent_goal_requests": []},
    )

    assert goal.status == "failed"
    assert scene.matches_condition(
        _condition(
            scope="world_object",
            target="钥匙",
            path="",
            operator="not_exists",
            value=None,
        )
    )


def test_agent_goal_source_must_exist_and_authored_goal_cannot_be_abandoned():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("甲")
    state = GoalState.from_initial(["保护家园"])
    actor.add_component(state)
    context = {
        "clock": SimpleNamespace(current_step=2),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "追查并不存在的秘密",
                "source_kind": "claim",
                "source_ref": "fabricated",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert len(state.goals) == 1
    assert "source is not present" in context["goal_errors"][0]

    context["agent_goal_requests"] = [
        {
            "actor": "甲",
            "operation": "abandon",
            "goal_id": "goal-1",
            "reason": "现在不想做了",
        }
    ]
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert state.goals["goal-1"].status == "active"
    assert "authored goals cannot be abandoned" in context["goal_errors"][0]


def test_navigation_problem_can_ground_a_remote_known_location_goal():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "村口": {"connected_to": ["南路"]},
            "南路": {"connected_to": ["村口", "城镇"]},
            "城镇": {"connected_to": ["南路"]},
        },
        actor_states={"旅人": {"location": "村口"}},
    )
    gm.add_component(scene)
    actor = Entity("旅人")
    goals = GoalState()
    actor.add_component(goals)
    actor.add_component(
        KnowledgeState(
            known_locations=["村口", "南路", "城镇"],
            known_routes={"村口": ["南路"], "南路": ["城镇"]},
        )
    )
    navigation = NavigationState()
    navigation.record(
        NavigationProblem(
            problem_id="navigation:blocked-bridge",
            route_source="村口",
            route_target="断桥",
            destination="城镇",
            discovered_at="村口",
            discovered_step=4,
            alternative_path=["村口", "南路", "城镇"],
            reason="断桥已经无法通行。",
        )
    )
    actor.add_component(navigation)
    context = {
        "clock": SimpleNamespace(current_step=5),
        "agent_goal_requests": [{
            "actor": "旅人",
            "operation": "adopt",
            "title": "绕路抵达城镇",
            "reason": "原路线中断，但我仍决定前往城镇。",
            "source_kind": "navigation_problem",
            "source_ref": "navigation:blocked-bridge",
            "resolution_kind": "reach_location",
            "resolution_target": "城镇",
            "_host_perception": {
                "location": "村口",
                "visible_actors": [],
                "visible_world": {
                    "村口": {"is_location": True, "connected_to": ["南路"]},
                    "南路": {"is_location": True},
                },
            },
        }],
    }

    GoalSystem().update({"GameMaster": gm, "旅人": actor}, context)

    adopted = goals.active_records()[0]
    assert adopted.source_kind == "navigation_problem"
    assert adopted.source_ref == "navigation:blocked-bridge"
    assert adopted.completion_conditions == [
        _condition(
            scope="actor",
            target="旅人",
            path="location",
            value="城镇",
        )
    ]
    assert context["goal_errors"] == []


def test_navigation_problem_goal_rejects_unknown_remote_location():
    gm = Entity("GameMaster")
    gm.add_component(
        SceneState(
            world_objects={"村口": {}, "秘密港口": {}},
            actor_states={"旅人": {"location": "村口"}},
        )
    )
    actor = Entity("旅人")
    goals = GoalState()
    actor.add_component(goals)
    actor.add_component(KnowledgeState(known_locations=["村口"]))
    navigation = NavigationState()
    navigation.record(
        NavigationProblem(
            problem_id="navigation:blocked",
            route_source="村口",
            route_target="断桥",
            destination="城镇",
            discovered_at="村口",
            discovered_step=4,
        )
    )
    actor.add_component(navigation)
    context = {
        "clock": SimpleNamespace(current_step=5),
        "agent_goal_requests": [{
            "actor": "旅人",
            "operation": "adopt",
            "title": "前往秘密港口",
            "source_kind": "navigation_problem",
            "source_ref": "navigation:blocked",
            "resolution_kind": "reach_location",
            "resolution_target": "秘密港口",
            "_host_perception": {
                "location": "村口",
                "visible_actors": [],
                "visible_world": {"村口": {"is_location": True}},
            },
        }],
    }

    GoalSystem().update({"GameMaster": gm, "旅人": actor}, context)

    assert goals.goals == {}
    assert "not currently known" in context["goal_errors"][0]


def test_deliver_object_template_resolves_from_authoritative_ownership():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "房间": {},
            "信件": {
                "is_location": False,
                "owner": "甲",
                "location": None,
                "portable": True,
                "hidden": False,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
    )
    gm.add_component(scene)
    actor = Entity("甲")
    state = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "拿到信件",
                "completion_conditions": [
                    _condition(
                        scope="world_object",
                        target="信件",
                        path="owner",
                        value="甲",
                    )
                ],
            }
        ]
    )
    state.goals["seed"].status = "achieved"
    actor.add_component(state)
    context = {
        "clock": SimpleNamespace(current_step=2),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "把信件交给乙",
                "source_kind": "resolved_goal",
                "source_ref": "seed",
                "resolution_kind": "deliver_object",
                "resolution_target": "信件",
                "resolution_recipient": "乙",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in state.goals.values() if record.origin == "agent")
    assert goal.status == "active"
    assert goal.completion_conditions[0]["value"] == "乙"

    scene.get_object_state("信件").update({"owner": "乙", "location": None})
    context.update(
        clock=SimpleNamespace(current_step=3),
        agent_goal_requests=[],
    )
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert goal.status == "achieved"


def test_fulfill_obligation_template_has_host_success_and_failure_locks():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    obligations = ObligationState.from_initial(
        [{"obligation_id": "delivery", "title": "交货", "due_step": 5}]
    )
    actor.add_component(goals)
    actor.add_component(obligations)
    context = {
        "clock": SimpleNamespace(current_step=1),
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "履行交货责任",
                "source_kind": "obligation",
                "source_ref": "delivery",
                "resolution_kind": "fulfill_obligation",
                "resolution_target": "delivery",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.completion_conditions[0]["scope"] == "obligation"
    assert goal.failure_conditions[0]["value"] == [
        "breached",
        "cancelled",
        "delegated",
    ]

    obligations.obligations["delivery"].status = "breached"
    context.update(
        clock=SimpleNamespace(current_step=6),
        agent_goal_requests=[],
    )
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert goal.status == "failed"


def test_settle_agreement_template_reads_only_participant_agreement_state():
    gm = Entity("GameMaster")
    gm.add_component(SceneState())
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    actor.add_component(goals)
    registry = AgreementRegistry()
    book = AgreementBook(
        agreements={
            "deal": AgreementRecord(
                agreement_id="deal",
                proposer="甲",
                parties=["甲", "乙"],
                accepted_by=["甲"],
                status="pending",
                expires_step=5,
            )
        }
    )
    registry.apply_book(book)
    context = {
        "clock": SimpleNamespace(current_step=1),
        "agreement_registry": registry,
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "促成当前协议",
                "source_kind": "agreement",
                "source_ref": "deal",
                "resolution_kind": "settle_agreement",
                "resolution_target": "deal",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"

    settled = registry.to_book()
    settled.agreements["deal"].status = "settled"
    registry.apply_book(settled)
    context.update(
        clock=SimpleNamespace(current_step=2),
        agent_goal_requests=[],
    )
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert goal.status == "achieved"


def test_obtain_evidence_template_requires_linked_evidence_and_real_possession():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={
            "档案室": {},
            "账册": {
                "is_location": False,
                "location": "档案室",
                "owner": None,
                "portable": True,
                "hidden": False,
            },
        },
        actor_states={"甲": {"location": "档案室"}},
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    knowledge = KnowledgeState.from_initial(
        [
            {
                "claim_id": "secret_deal",
                "stance": "uncertain",
                "confidence": 0.5,
                "basis": "reported",
            }
        ]
    )
    actor.add_component(goals)
    actor.add_component(knowledge)
    claim_registry = ClaimRegistry()
    world_entities = {"GameMaster": gm, "甲": actor}
    claim_registry.seed(
        [
            {
                "claim_id": "secret_deal",
                "statement": "某项秘密交易确实发生过。",
                "supporting_evidence": ["账册"],
            }
        ],
        scene_state=scene,
        world_entities=world_entities,
    )
    context = {
        "clock": SimpleNamespace(current_step=1),
        "claim_registry": claim_registry,
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "取得支持秘密交易的证据",
                "source_kind": "claim",
                "source_ref": "secret_deal",
                "resolution_kind": "obtain_evidence",
                "resolution_target": "secret_deal",
                "resolution_evidence": "账册",
            }
        ],
    }

    GoalSystem().update(world_entities, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"
    assert {item["scope"] for item in goal.completion_conditions} == {
        "knowledge",
        "world_object",
    }

    knowledge.learn(
        claim_id="secret_deal",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=2,
        evidence_refs=["账册"],
    )
    scene.get_object_state("账册").update({"owner": "甲", "location": None})
    context.update(
        clock=SimpleNamespace(current_step=2),
        agent_goal_requests=[],
    )
    GoalSystem().update(world_entities, context)

    assert goal.status == "achieved"


def test_become_acquainted_template_resolves_only_after_real_interaction_bit():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "大厅"},
        },
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    actor.add_component(goals)
    relation_registry = SocialRelationRegistry()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "relation_registry": relation_registry,
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "正式认识乙",
                "source_kind": "visible_actor",
                "source_ref": "乙",
                "resolution_kind": "become_acquainted",
                "resolution_target": "乙",
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.status == "active"

    book = relation_registry.to_relationship_book()
    relation = book.ensure("甲", "乙", created_step=2)
    relation.bits["acquainted"] = RelationshipBit(
        bit_id="acquainted",
        roles={"participant_0": "甲", "participant_1": "乙"},
        created_step=2,
        provenance={"source": "observed_interaction"},
    )
    relation_registry.apply_relationship_book(book)
    context.update(
        clock=SimpleNamespace(current_step=2),
        agent_goal_requests=[],
    )
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert goal.status == "achieved"


def test_relationship_goal_uses_host_qualitative_state_not_numeric_threshold():
    gm = Entity("GameMaster")
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "大厅"},
        },
    )
    gm.add_component(scene)
    actor = Entity("甲")
    goals = GoalState.from_initial([])
    actor.add_component(goals)
    relation_registry = SocialRelationRegistry()
    book = relation_registry.to_relationship_book()
    book.ensure("甲", "乙", created_step=0)
    relation_registry.apply_relationship_book(book)
    context = {
        "clock": SimpleNamespace(current_step=1),
        "relation_registry": relation_registry,
        "agent_goal_requests": [
            {
                "actor": "甲",
                "operation": "adopt",
                "title": "逐渐信任乙",
                "source_kind": "relationship",
                "source_ref": "乙",
                "resolution_kind": "reach_relationship_state",
                "resolution_target": "乙",
                "resolution_state": "trusted",
                "trust_threshold": -99,
            }
        ],
    }

    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)
    goal = next(record for record in goals.goals.values() if record.origin == "agent")
    assert goal.completion_conditions == [
        {
            "scope": "relationship",
            "target": "乙",
            "path": "actor_to_target_states",
            "operator": "contains",
            "value": "trusted",
        }
    ]
    assert goal.status == "active"

    updated = relation_registry.to_relationship_book()
    updated.set_track("甲", "乙", "trust", 2.0, updated_step=2)
    relation_registry.apply_relationship_book(updated)
    context.update(
        clock=SimpleNamespace(current_step=2),
        agent_goal_requests=[],
    )
    GoalSystem().update({"GameMaster": gm, "甲": actor}, context)

    assert goal.status == "achieved"

    other = Entity("丙")
    other_goals = GoalState.from_initial([])
    other.add_component(other_goals)
    invalid_context = {
        "clock": SimpleNamespace(current_step=1),
        "relation_registry": relation_registry,
        "agent_goal_requests": [
            {
                "actor": "丙",
                "operation": "adopt",
                "title": "把精确好感刷到指定值",
                "source_kind": "visible_actor",
                "source_ref": "乙",
                "resolution_kind": "reach_relationship_state",
                "resolution_target": "乙",
                "resolution_state": "trust>=2.5",
            }
        ],
    }
    scene.update_actor_state("丙", {"location": "大厅"})
    GoalSystem().update({"GameMaster": gm, "丙": other}, invalid_context)

    assert other_goals.goals == {}
    assert "unsupported qualitative" in invalid_context["goal_errors"][0]
