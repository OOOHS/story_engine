from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.character_lifecycle import CharacterLifecycle
from src.story_engine.environment.world_transaction import WorldStateTransaction
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


def test_authoritative_state_change_derives_plot_update_without_llm_clock_guess():
    scene, plots, scenario = _bundle()
    result = {
        "state_updates": {
            "scene": {},
            "world_objects": {},
            "actor_states": {"探索者": {"location": "遗迹"}},
        },
        "plot_updates": [],
        "tension_delta": 0,
    }

    enriched = CausalPlotEngine().enrich_result(scene, plots, scenario, result)
    committed = WorldStateTransaction().commit(
        scene, plots, DramaState(), enriched
    )

    assert committed.committed is True
    assert enriched["plot_updates"][0]["rule_id"] == "enter_ruin"
    assert plots.plots["ruin_secret"]["clock"] == 1
    assert scene.get_scene_flag("consumed_plot_rules") == ["enter_ruin"]


def test_one_shot_causal_plot_rule_does_not_repeat_on_later_turns():
    scene, plots, scenario = _bundle()
    scene.update_actor_state("探索者", {"location": "遗迹"})
    scene.update_scene_flags({"consumed_plot_rules": ["enter_ruin"]})
    result = {
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result["plot_updates"] == []


def test_failed_world_transaction_cannot_commit_derived_plot_progress():
    scene, plots, scenario = _bundle()
    result = {
        "state_updates": {
            "scene": {},
            "world_objects": {"村庄": {"connected_to": ["不存在的道路"]}},
            "actor_states": {"探索者": {"location": "遗迹"}},
        },
        "plot_updates": [],
        "tension_delta": 0,
    }

    enriched = CausalPlotEngine().enrich_result(scene, plots, scenario, result)
    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        DramaState(),
        enriched,
        proposal_actors={"探索者"},
    )

    assert outcome.committed is False
    assert enriched["plot_updates"][0]["rule_id"] == "enter_ruin"
    assert plots.plots["ruin_secret"]["clock"] == 0
    assert scene.get_scene_flag("consumed_plot_rules") is None


def test_unmet_causal_conditions_leave_plot_unchanged():
    scene, plots, scenario = _bundle()
    result = {
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result.get("causal_plot_rules") is None
    assert result["plot_updates"] == []


def test_malformed_plot_update_collection_is_left_for_transaction_rejection():
    scene, plots, scenario = _bundle()
    result = {
        "state_updates": {
            "scene": {},
            "world_objects": {},
            "actor_states": {"探索者": {"location": "遗迹"}},
        },
        "plot_updates": {"plot_id": "ruin_secret", "advance": 99},
    }

    enriched = CausalPlotEngine().enrich_result(scene, plots, scenario, result)
    outcome = WorldStateTransaction().commit(scene, plots, DramaState(), enriched)

    assert enriched["plot_updates"] == {"plot_id": "ruin_secret", "advance": 99}
    assert outcome.committed is False
    assert "plot_updates must be a list" in outcome.errors


def test_causal_rules_use_priority_and_respect_per_turn_trigger_limit():
    scene, plots, scenario = _bundle()
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
    result = {
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result["causal_plot_rules"] == ["high_priority"]
    assert result["causal_plot_suppressed_rules"] == ["low_priority"]
    assert result["state_updates"]["scene"]["consumed_plot_rules"] == ["high_priority"]


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
    result = {
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result["causal_plot_rules"] == ["fits_budget"]
    assert result["causal_plot_suppressed_rules"] == ["too_large"]
    assert result["plot_updates"][0]["advance"] == 1


def test_resolver_cannot_forge_consumed_causal_rule_bookkeeping():
    scene, plots, scenario = _bundle()
    result = {
        "state_updates": {
            "scene": {"consumed_plot_rules": ["enter_ruin", "forged_rule"]},
            "world_objects": {},
            "actor_states": {},
        },
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result["state_updates"]["scene"]["consumed_plot_rules"] == []


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
    result = {
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
    }

    CausalPlotEngine().enrich_result(scene, plots, scenario, result)

    assert result["causal_plot_rules"] == ["repeatable_pressure"]
    assert result["state_updates"]["scene"]["consumed_plot_rules"] == []


def test_object_lifecycle_candidate_can_trigger_state_causal_rule():
    scene, plots, scenario = _bundle()
    scene.world_objects["遗迹钥匙"] = {
        "is_location": False,
        "kind": "key",
        "location": "村庄",
        "owner": None,
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
    result = {
        "resolved_actions": [
            {
                "actor": "探索者",
                "outcome": "success",
                "result": "探索者拿起遗迹钥匙。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "object_lifecycle": [
            {
                "operation": "relocate",
                "object_id": "遗迹钥匙",
                "actor": "探索者",
                "owner": "探索者",
                "reason": "探索者拿起遗迹钥匙",
            }
        ],
        "plot_updates": [],
    }

    enriched = CausalPlotEngine().enrich_result(scene, plots, scenario, result)
    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        DramaState(),
        enriched,
        proposal_actors={"探索者"},
    )

    assert outcome.committed is True
    assert scene.get_object_state("遗迹钥匙")["owner"] == "探索者"
    assert enriched["causal_plot_rules"] == ["take_ruin_key"]
    assert plots.plots["ruin_secret"]["clock"] == 1


def test_prepared_character_body_participates_in_candidate_plot_causality():
    scene, plots, scenario = _bundle()
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
    preparation = CharacterLifecycle().prepare(
        {},
        scene,
        {"name": "信使", "location": "村庄", "goals": ["送达警告"]},
    )
    assert preparation.errors == []
    result = {
        "resolved_actions": [
            {
                "actor": "World",
                "outcome": "success",
                "result": "信使抵达村庄。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "object_lifecycle": [],
        "plot_updates": [],
    }

    enriched = CausalPlotEngine().enrich_result(
        scene,
        plots,
        scenario,
        result,
        character_spawn_plan=preparation.plan,
    )
    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        DramaState(),
        enriched,
        character_spawn_plan=preparation.plan,
    )

    assert outcome.committed is True
    assert scene.get_actor_location("信使") == "村庄"
    assert enriched["causal_plot_rules"] == ["messenger_arrives"]
    assert plots.plots["ruin_secret"]["clock"] == 1
