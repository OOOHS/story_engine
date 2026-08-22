from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.narrative import CausalPlotEngine
from src.story_engine.scenarios.config import (
    PlotEntityConfig,
    PlotRuleConfig,
    ScenarioConfig,
    StateCondition,
)


def _bundle():
    scene = SceneState(
        world_objects={"村庄": {}, "遗迹": {}},
        actor_states={"探索者": {"location": "村庄"}},
    )
    plots = PlotState.from_configs(
        [
            PlotEntityConfig(
                plot_id="ruin_secret",
                title="遗迹秘密",
                description="遗迹中藏着秘密。",
                max_clock=3,
            )
        ]
    )
    scenario = ScenarioConfig(
        name="因果规则",
        default_agent_runtime="llm",
        description="探索者进入遗迹后剧情推进。",
        environment="村庄与遗迹",
        initial_state="探索者尚未进入遗迹。",
        plot_entities=[],
        plot_rules=[
            PlotRuleConfig(
                rule_id="enter_ruin",
                plot_id="ruin_secret",
                conditions=[
                    StateCondition(
                        scope="actor",
                        target="探索者",
                        path="location",
                        operator="eq",
                        value="遗迹",
                    )
                ],
                advance=1,
                one_shot=True,
                reason="探索者实际进入遗迹。",
            )
        ],
    )
    return scene, plots, scenario


def test_committed_actor_location_derives_plot_advance_without_llm_clock_guess():
    scene, plots, scenario = _bundle()
    scene.update_actor_state("探索者", {"location": "遗迹"})
    result = {}

    settled = CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert settled["plot_updates"][0]["rule_id"] == "enter_ruin"
    assert plots.plots["ruin_secret"]["clock"] == 1
    assert scene.get_scene_flag("consumed_plot_rules") == ["enter_ruin"]


def test_one_shot_causal_plot_rule_does_not_repeat_on_later_turns():
    scene, plots, scenario = _bundle()
    scene.update_actor_state("探索者", {"location": "遗迹"})
    scene.update_scene_flags({"consumed_plot_rules": ["enter_ruin"]})
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result.get("plot_updates") is None
    assert plots.plots["ruin_secret"]["clock"] == 0


def test_unmet_causal_conditions_leave_plot_unchanged():
    scene, plots, scenario = _bundle()
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result.get("causal_plot_rules") is None
    assert result.get("plot_updates") is None
    assert plots.plots["ruin_secret"]["clock"] == 0


def test_causal_rules_use_priority_and_respect_per_turn_trigger_limit():
    scene, plots, scenario = _bundle()
    scene.update_actor_state("探索者", {"location": "遗迹"})
    scenario.causal_plot_max_triggers_per_turn = 1
    scenario.plot_rules = [
        PlotRuleConfig(
            rule_id="low_priority",
            plot_id="ruin_secret",
            conditions=[],
            advance=1,
            priority=1,
        ),
        PlotRuleConfig(
            rule_id="high_priority",
            plot_id="ruin_secret",
            conditions=[],
            advance=1,
            priority=10,
        ),
    ]
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result["causal_plot_rules"] == ["high_priority"]
    assert result["causal_plot_suppressed_rules"] == ["low_priority"]
    assert scene.get_scene_flag("consumed_plot_rules") == ["high_priority"]


def test_causal_rules_do_not_partially_apply_rule_beyond_advance_budget():
    scene, plots, scenario = _bundle()
    scenario.causal_plot_max_total_advance = 1
    scenario.plot_rules = [
        PlotRuleConfig(
            rule_id="too_large",
            plot_id="ruin_secret",
            conditions=[],
            advance=2,
            priority=10,
        ),
        PlotRuleConfig(
            rule_id="fits_budget",
            plot_id="ruin_secret",
            conditions=[],
            advance=1,
            priority=1,
        ),
    ]
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result["causal_plot_rules"] == ["fits_budget"]
    assert result["causal_plot_suppressed_rules"] == ["too_large"]
    assert result["plot_updates"][0]["advance"] == 1


def test_repeatable_causal_rule_does_not_pollute_one_shot_ledger():
    scene, plots, scenario = _bundle()
    scenario.plot_rules = [
        PlotRuleConfig(
            rule_id="repeatable_pressure",
            plot_id="ruin_secret",
            conditions=[],
            advance=1,
            one_shot=False,
        )
    ]
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result["causal_plot_rules"] == ["repeatable_pressure"]
    assert scene.get_scene_flag("consumed_plot_rules") == []


def test_object_lifecycle_candidate_can_trigger_state_causal_rule():
    scene, plots, scenario = _bundle()
    scene.world_objects["遗迹钥匙"] = {
        "is_location": False,
        "kind": "key",
        "location": "村庄",
        "owner": "探索者",
        "hidden": False,
        "portable": True,
    }
    scenario.plot_rules = [
        PlotRuleConfig(
            rule_id="take_ruin_key",
            plot_id="ruin_secret",
            conditions=[
                StateCondition(
                    scope="world_object",
                    target="遗迹钥匙",
                    path="owner",
                    operator="eq",
                    value="探索者",
                )
            ],
            advance=1,
            reason="探索者真正取得了钥匙",
        )
    ]
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result["causal_plot_rules"] == ["take_ruin_key"]
    assert plots.plots["ruin_secret"]["clock"] == 1


def test_prepared_character_body_participates_in_committed_plot_causality():
    scene, plots, scenario = _bundle()
    scene.actor_states["信使"] = {"location": "村庄"}
    scenario.plot_rules = [
        PlotRuleConfig(
            rule_id="messenger_arrives",
            plot_id="ruin_secret",
            conditions=[
                StateCondition(
                    scope="actor",
                    target="信使",
                    path="location",
                    operator="eq",
                    value="村庄",
                )
            ],
            advance=1,
            reason="信使的身体确实进入村庄",
        )
    ]
    result = {}

    CausalPlotEngine().settle(
        scene_state=scene, plot_state=plots, scenario=scenario, result=result
    )

    assert result["causal_plot_rules"] == ["messenger_arrives"]
    assert plots.plots["ruin_secret"]["clock"] == 1
