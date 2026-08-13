import json
from types import SimpleNamespace

from src.story_engine.components.goal_state import GoalState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.navigation_state import (
    NavigationProblem,
    NavigationState,
)
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.agents.types import AgentDecision
from src.story_engine.evaluation import (
    EpisodeClosureEvaluator,
    EpisodeClosurePolicy,
    EpisodeRunner,
)
from src.story_engine_content.evaluation.minimal_investigation import (
    create_minimal_investigation_session,
)
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


class _Entity:
    def __init__(self, **components):
        self.name = str(components.pop("name", "actor"))
        self.components = components

    def get_component(self, name):
        return self.components.get(name)


class _AgreementRegistry:
    def __init__(self, agreements=None):
        self.agreements = agreements or {}

    def to_book(self):
        return SimpleNamespace(agreements=self.agreements)


class _ActionQueue:
    def __init__(self, pending=None):
        self.pending = pending or []

    def snapshot(self):
        return {"pending": list(self.pending)}


def _closure_session(*, goal_state=None, obligation_state=None, cognition=None,
                     controller=None, navigation_state=None,
                     agreements=None, pending_actions=None, plot_state=None,
                     scene_state=None, drive_state=None):
    entity = _Entity(
        GoalState=goal_state,
        ObligationState=obligation_state,
        PlotState=plot_state,
        Cognition=cognition,
        AgentController=controller,
        NavigationState=navigation_state,
        SceneState=scene_state,
        DriveState=drive_state,
    )
    runner = SimpleNamespace(
        entities={"actor": entity},
        agreement_registry=_AgreementRegistry(agreements),
        action_queue=_ActionQueue(pending_actions),
    )
    return SimpleNamespace(runner=runner)


def test_closure_evaluator_reports_authoritative_blockers():
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "g",
                "title": "完成任务",
                "completion_conditions": [{"scope": "scene", "path": "done"}],
            }
        ]
    )
    obligations = ObligationState.from_initial(
        [{"obligation_id": "o", "title": "履约", "due_step": 5}]
    )
    agreements = {
        "proposal": SimpleNamespace(status="pending", performance_status="none"),
        "service": SimpleNamespace(status="settled", performance_status="pending"),
    }
    plots = PlotState(plots={"p": {"clock": 1, "max_clock": 3}})
    session = _closure_session(
        goal_state=goals,
        obligation_state=obligations,
        agreements=agreements,
        pending_actions=[{"event_id": "future"}],
        plot_state=plots,
    )

    status = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(require_resolved_plots=True),
    )

    assert status.eligible is False
    assert set(status.blockers) == {
        "active_verifiable_goals",
        "active_obligations",
        "pending_agreements",
        "pending_agreement_performance",
        "pending_actions",
        "unresolved_plots",
    }


def test_emergent_seed_can_close_without_an_authored_goal_anchor_by_default():
    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=GoalState.from_initial(["只用于策略的目标"])),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is True
    assert status.blockers == ()


def test_task_style_episode_can_explicitly_require_a_goal_anchor():
    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=GoalState.from_initial(["只用于策略的目标"])),
        EpisodeClosurePolicy(require_goal_anchor=True),
    )

    assert status.eligible is False
    assert status.blockers == ("no_verifiable_goal_anchor",)


def _critical_need_closure_session(*, relief=True, dormant=False):
    affordances = []
    if relief:
        affordances.append(
            {
                "id": "eat",
                "consumes": True,
                "need_effects": {"hunger": -0.5},
            }
        )
    scene = SceneState(
        world_objects={
            "厨房": {},
            "面包": {
                "is_location": False,
                "location": "厨房",
                "affordances": affordances,
            },
        },
        actor_states={"actor": {"location": "厨房"}},
    )
    drive = DriveState.from_initial([
        {
            "name": "hunger",
            "pressure": 0.9,
            "critical_threshold": 0.8,
        }
    ])
    controller = AgentController(
        activation_policy="dormant" if dormant else "background",
        decision_count=1,
    )
    return _closure_session(
        controller=controller,
        drive_state=drive,
        scene_state=scene,
    )


def test_actionable_critical_need_blocks_natural_closure():
    session = _critical_need_closure_session()

    status = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(),
    )
    relaxed = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(require_no_actionable_critical_needs=False),
    )

    assert status.eligible is False
    assert status.blockers == ("actionable_critical_needs",)
    assert status.details["actionable_critical_need_count"] == 1
    assert relaxed.eligible is True


def test_unactionable_or_dormant_critical_need_is_diagnostic_not_blocking():
    unavailable = EpisodeClosureEvaluator().evaluate(
        _critical_need_closure_session(relief=False),
        EpisodeClosurePolicy(),
    )
    dormant = EpisodeClosureEvaluator().evaluate(
        _critical_need_closure_session(dormant=True),
        EpisodeClosurePolicy(),
    )

    assert unavailable.eligible is True
    assert unavailable.details["unactionable_critical_need_count"] == 1
    assert dormant.eligible is True
    assert dormant.details["dormant_actionable_critical_need_count"] == 1


def test_material_change_requires_a_quiet_step_before_natural_closure():
    session = _closure_session()

    changing = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(),
        material_change_kinds=("scene", "relationships"),
    )
    chapter_cut = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(require_stable_material_state=False),
        material_change_kinds=("scene",),
    )

    assert changing.eligible is False
    assert changing.blockers == ("material_state_changed",)
    assert changing.details["material_change_kinds"] == [
        "scene",
        "relationships",
    ]
    assert chapter_cut.eligible is True


def test_pure_world_seed_reaches_stable_closure_without_authored_goal_rules():
    class WaitRuntime:
        def decide(self, entity, perception):
            del entity, perception
            return AgentDecision(action="暂时等待新的变化。")

    scenario = ScenarioConfig(
        name="无任务锚点世界种子",
        description="角色只有自然语言动机，没有手工完成条件。",
        environment="安静房间",
        initial_state="暂时没有新的事件。",
        initial_world_objects={"安静房间": {}},
        initial_actor_states={"甲": {"location": "安静房间"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="旅人",
                personality="耐心",
                goals=["等待值得回应的事情"],
                is_player=True,
                agent_runtime="test",
            )
        ],
    )
    session = create_session(
        scenario,
        random_seed="emergent-closure",
        agent_runtime_factories={"test": lambda entity, config: WaitRuntime()},
    )

    report = EpisodeRunner().run(
        session,
        steps=5,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    assert report.closure_reached is True
    assert report.termination_reason == "closure_reached"
    assert report.final_closure_status["blockers"] == ()


def test_episode_cannot_close_while_agent_ignores_visible_critical_relief():
    class IgnoreFoodRuntime:
        def decide(self, entity, perception):
            del entity, perception
            return AgentDecision(action="继续等待，不处理饥饿。")

    scenario = ScenarioConfig(
        name="临界需求闭合审计",
        description="角色持续忽略眼前可用的食物。",
        environment="厨房",
        initial_state="旅人非常饥饿，面包就在眼前。",
        initial_world_objects={
            "厨房": {},
            "面包": {
                "is_location": False,
                "location": "厨房",
                "affordances": [
                    {
                        "id": "eat",
                        "consumes": True,
                        "need_effects": {"hunger": -0.5},
                    }
                ],
            },
        },
        initial_actor_states={"甲": {"location": "厨房"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="饥饿的旅人",
                personality="固执",
                goals=[],
                initial_needs=[
                    {
                        "name": "hunger",
                        "pressure": 0.9,
                        "critical_threshold": 0.8,
                    }
                ],
                is_player=True,
                agent_runtime="ignore-food",
            )
        ],
    )
    session = create_session(
        scenario,
        random_seed="critical-need-open",
        agent_runtime_factories={
            "ignore-food": lambda entity, config: IgnoreFoodRuntime()
        },
    )

    report = EpisodeRunner().run(
        session,
        steps=4,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    assert report.closure_reached is False
    assert report.metrics["actionable_critical_need_blocked_steps"] == 4
    assert report.metrics["terminal_actionable_critical_need_count"] == 1
    assert "actionable_critical_needs_at_step_limit" in report.quality_flags
    assert report.final_closure_status["blockers"] == (
        "actionable_critical_needs",
    )


def test_unexercised_autonomous_agent_blocks_closure_until_first_decision():
    controller = AgentController(activation_policy="background")
    session = _closure_session(controller=controller)

    before = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(),
    )

    assert before.eligible is False
    assert before.blockers == ("unexercised_autonomous_agents",)
    assert before.details["unexercised_autonomous_agent_count"] == 1

    controller.record_decision(3)
    after = EpisodeClosureEvaluator().evaluate(
        session,
        EpisodeClosurePolicy(),
    )

    assert after.eligible is True
    assert after.details["unexercised_autonomous_agent_count"] == 0


def test_closure_waits_for_staggered_offscreen_agents_to_participate():
    calls = {"玩家": 0, "C": 0}

    class CountingWaitRuntime:
        def decide(self, entity, perception):
            del perception
            calls[entity.name] += 1
            return AgentDecision(action="暂时等待新的变化。")

    scenario = ScenarioConfig(
        name="离屏角色首次参与",
        description="故事不能在错峰背景角色第一次决策前结束。",
        environment="玩家在房间，守卫在远处城门。",
        initial_state="两地暂时都很安静。",
        initial_world_objects={"房间": {}, "城门": {}},
        initial_actor_states={
            "玩家": {"location": "房间"},
            "C": {"location": "城门"},
        },
        characters=[
            CharacterConfig(
                name="玩家",
                role="旅人",
                personality="耐心",
                goals=[],
                is_player=True,
                agent_runtime="test",
            ),
            CharacterConfig(
                name="C",
                role="守卫",
                personality="尽职",
                goals=[],
                agent_runtime="test",
                activation_policy="background",
                background_interval=4,
            ),
        ],
    )
    session = create_session(
        scenario,
        random_seed="offscreen-first-turn",
        agent_runtime_factories={
            "test": lambda entity, config: CountingWaitRuntime()
        },
    )

    report = EpisodeRunner().run(
        session,
        steps=8,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    assert report.closure_reached is True
    assert len(report.steps) == 5
    assert calls["C"] == 1
    assert session.entities["C"].get_component("AgentController").decision_count == 1


def test_active_agent_grown_goal_blocks_episode_closure():
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成初始目标",
                "status": "achieved",
                "completion_conditions": [{"scope": "scene", "path": "done"}],
            }
        ]
    )
    goals.goals["seed"].status = "achieved"
    transition, error = goals.adopt_agent_goal(
        title="处理初始目标留下的后果",
        description="事情并未真正结束",
        source_kind="resolved_goal",
        source_ref="seed",
        priority=0.65,
        step=3,
    )
    assert transition and not error

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is False
    assert status.blockers == ("active_agent_goals",)


def test_unprocessed_world_event_blocks_episode_closure():
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成初始目标",
                "status": "achieved",
                "completion_conditions": [{"scope": "scene", "path": "done"}],
            }
        ]
    )
    goals.goals["seed"].status = "achieved"
    cognition = Cognition()
    cognition.record_world_event(
        event_id="agreement:service:performance:breached",
        statement="护送协议已经违约。",
        step=4,
        location="",
        witness_mode="self",
    )

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, cognition=cognition),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is False
    assert status.blockers == ("pending_world_events",)
    assert status.details["pending_world_event_count"] == 1


def test_unprocessed_event_response_blocks_episode_closure():
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成初始目标",
                "status": "achieved",
                "completion_conditions": [{"scope": "scene", "path": "done"}],
            }
        ]
    )
    goals.goals["seed"].status = "achieved"
    cognition = Cognition(
        pending_event_responses=[
            "event-response:agreement:service:breached:甲->乙:apologize"
        ]
    )

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, cognition=cognition),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is False
    assert status.blockers == ("pending_event_responses",)
    assert status.details["pending_event_response_count"] == 1


def test_dormant_pending_attention_is_preserved_but_does_not_block_closure():
    goals = GoalState.from_initial(
        structured=[
            {
                "goal_id": "seed",
                "title": "完成初始目标",
                "completion_conditions": [{"scope": "scene", "path": "done"}],
            }
        ]
    )
    goals.goals["seed"].status = "achieved"
    cognition = Cognition(
        pending_world_events=["storm"],
        pending_event_responses=["apology"],
    )
    controller = AgentController(activation_policy="dormant")

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(
            goal_state=goals,
            cognition=cognition,
            controller=controller,
        ),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is True
    assert status.blockers == ()
    assert status.details["pending_world_event_count"] == 0
    assert status.details["pending_event_response_count"] == 0
    assert status.details["dormant_pending_world_event_count"] == 1
    assert status.details["dormant_pending_event_response_count"] == 1
    assert cognition.pending_world_events == ["storm"]
    assert cognition.pending_event_responses == ["apology"]


def _navigation_state():
    state = NavigationState()
    state.record(
        NavigationProblem(
            problem_id="navigation:blocked-road",
            route_source="村口",
            route_target="断桥",
            destination="城镇",
            discovered_at="村口",
            discovered_step=4,
            reason="断桥已经无法通行。",
        )
    )
    return state


def test_active_navigation_problem_blocks_episode_closure():
    goals = GoalState.from_initial(
        structured=[{
            "goal_id": "seed",
            "title": "完成初始目标",
            "completion_conditions": [{"scope": "scene", "path": "done"}],
        }]
    )
    goals.goals["seed"].status = "achieved"

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, navigation_state=_navigation_state()),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is False
    assert status.blockers == ("active_navigation_problems",)
    assert status.details["active_navigation_problem_count"] == 1


def test_dormant_navigation_problem_is_preserved_but_does_not_block_closure():
    goals = GoalState.from_initial(
        structured=[{
            "goal_id": "seed",
            "title": "完成初始目标",
            "completion_conditions": [{"scope": "scene", "path": "done"}],
        }]
    )
    goals.goals["seed"].status = "achieved"
    navigation = _navigation_state()

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(
            goal_state=goals,
            navigation_state=navigation,
            controller=AgentController(activation_policy="dormant"),
        ),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is True
    assert status.details["active_navigation_problem_count"] == 0
    assert status.details["dormant_navigation_problem_count"] == 1
    assert navigation.problems["navigation:blocked-road"].status == "active"


def test_scheduled_timeline_commitment_blocks_premature_episode_closure():
    goals = GoalState.from_initial(
        structured=[{
            "goal_id": "seed",
            "title": "已经完成的眼前目标",
            "completion_conditions": [{"scope": "scene", "path": "done"}],
        }]
    )
    goals.goals["seed"].status = "achieved"
    scene = SceneState(
        scene_flags={
            "upcoming_commitments": [{
                "commitment_id": "future-ceremony",
                "title": "稍后举行的仪式",
                "due_step": 6,
                "status": "scheduled",
            }]
        }
    )

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, scene_state=scene),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is False
    assert status.blockers == ("active_timeline_commitments",)
    assert status.details["active_timeline_commitment_count"] == 1

    chapter_cut = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, scene_state=scene),
        EpisodeClosurePolicy(require_no_active_timeline_commitments=False),
    )
    assert chapter_cut.eligible is True


def test_resolved_timeline_commitment_no_longer_blocks_closure():
    goals = GoalState.from_initial(
        structured=[{
            "goal_id": "seed",
            "title": "已经完成的眼前目标",
            "completion_conditions": [{"scope": "scene", "path": "done"}],
        }]
    )
    goals.goals["seed"].status = "achieved"
    scene = SceneState(
        scene_flags={
            "upcoming_commitments": [{
                "commitment_id": "finished-ceremony",
                "title": "已经结束的仪式",
                "due_step": 2,
                "status": "resolved",
            }]
        }
    )

    status = EpisodeClosureEvaluator().evaluate(
        _closure_session(goal_state=goals, scene_state=scene),
        EpisodeClosurePolicy(),
    )

    assert status.eligible is True
    assert status.details["active_timeline_commitment_count"] == 0


def test_active_investigation_does_not_count_as_stable_closure(tmp_path):
    report = EpisodeRunner().run(
        create_minimal_investigation_session(0),
        steps=10,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    assert report.closure_reached is False
    assert report.termination_reason == "step_limit"
    assert len(report.steps) == 10
    assert report.metrics["steps_to_closure"] is None
    assert report.steps[-1].closure_eligible is False
    assert "material_state_changed" in report.steps[-1].closure_blockers
    assert report.steps[-1].material_change_kinds
    assert report.final_closure_status["eligible"] is False
    assert "unclosed_episode" in report.quality_flags
    assert "materially_active_at_step_limit" in report.quality_flags
    assert report.metrics["material_stability_blocked_steps"] > 0
    assert report.metrics["terminal_material_change_count"] == 1

    target = report.write_json(tmp_path / "episode.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["closure_policy"]["stable_steps"] == 2
    assert payload["closure_reached"] is False


def test_episode_without_closure_policy_runs_requested_steps():
    report = EpisodeRunner().run(
        create_minimal_investigation_session(0),
        steps=3,
    )

    assert len(report.steps) == 3
    assert report.requested_steps == 3
    assert report.closure_reached is False
    assert report.termination_reason == "step_limit"
    assert report.closure_policy == {}
    assert "unclosed_episode" not in report.quality_flags
