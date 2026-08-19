from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.narrative import ConflictDirector
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.scenarios.config import (
    ConflictConfig,
    ConflictTemplateConfig,
    ScenarioConfig,
)


class Clock:
    current_step = 4


def _scenario():
    return ScenarioConfig(
        name="冲突测试",
        default_agent_runtime="llm",
        description="测试冲突节奏。",
        environment="大厅",
        initial_state="两人意见不合。",
        conflict=ConflictConfig(
            intensity=0.9,
            max_quiet_turns=1,
            early_pressure_window_end=3,
            repetition_window=2,
        ),
        conflict_templates=[
            ConflictTemplateConfig(
                template_id="early",
                instruction="早期压力",
                phases=["arrival"],
            ),
            ConflictTemplateConfig(
                template_id="late",
                instruction="后期压力",
                phases=["night"],
                min_step=2,
            ),
        ],
    )


def test_conflict_director_selects_templates_from_phase_and_step():
    selected = ConflictDirector().select_templates(
        _scenario().conflict_templates,
        phase="night",
        current_step=4,
    )

    assert [item["template_id"] for item in selected] == ["late"]


def test_conflict_director_records_quiet_and_visible_turns_deterministically():
    director = ConflictDirector()
    scene = SceneState(
        scene_flags={
            "quiet_turns_since_conflict": 0,
            "visible_conflict_count": 0,
            "recent_conflict_template_ids": [],
        }
    )
    context = {"clock": Clock(), "conflict": {"repetition_window": 2}}

    director.record_result(
        scene,
        context,
        {"conflict_level": "none", "resolved_actions": [], "conflict_flags": []},
    )
    assert scene.get_scene_flag("quiet_turns_since_conflict") == 1

    director.record_result(
        scene,
        context,
        {
            "conflict_level": "medium",
            "resolved_actions": [],
            "conflict_flags": ["confrontation"],
            "applied_conflict_templates": ["first", "second", "third"],
        },
    )
    assert scene.get_scene_flag("quiet_turns_since_conflict") == 0
    assert scene.get_scene_flag("visible_conflict_count") == 1
    assert scene.get_scene_flag("recent_conflict_template_ids") == ["second", "third"]


def test_conflict_packet_combines_director_storylet_and_transition_pressure():
    scenario = _scenario()
    scene = SceneState(
        scene_flags={
            "day_phase": "night",
            "quiet_turns_since_conflict": 2,
            "visible_conflict_count": 0,
        }
    )
    packet = ConflictDirector().build_packet(
        scene_state=scene,
        scenario=scenario,
        current_step=4,
        reaction_context={
            "requires_reaction": True,
            "action_pressure": "high",
            "hostile_watchers": ["对手"],
        },
        storylet_packet={
            "priority_storylet_ids": ["reveal"],
            "priority_tags": ["secret"],
            "preferred_template_ids": ["late"],
        },
        timeline_packet={
            "transition_pressure": {"requires_human_backlash": True}
        },
        director_packet={"directive": "raise_pressure"},
    )

    assert packet["mode"] == "advisory_pressure"
    assert packet["visible_conflict_opportunity"] is True
    assert packet["pressure_state"] == "acute"
    assert "require_visible_conflict" not in packet
    assert packet["storylet_template_ids"] == ["late"]
    assert packet["active_templates"][0]["template_id"] == "late"


def test_conflict_opportunity_does_not_mutate_a_quiet_result_or_cast_an_npc():
    result = {
        "resolved_actions": [
            {
                "actor": "玩家",
                "intent": "等待",
                "outcome": "success",
                "location": "大厅",
                "result": "暂时没有采取进一步行动。",
                "visibility": "public",
            }
        ],
        "conflict_level": "none",
        "conflict_flags": [],
        "tension_delta": 0.0,
    }
    packet = ConflictDirector().build_packet(
        scene_state=SceneState(
            scene_flags={
                "quiet_turns_since_conflict": 3,
                "visible_conflict_count": 0,
            }
        ),
        scenario=_scenario(),
        current_step=4,
        reaction_context={"hostile_watchers": ["对手"]},
        storylet_packet={},
        timeline_packet={},
        director_packet={"directive": "raise_pressure"},
    )

    assert packet["visible_conflict_opportunity"] is True
    assert [item["actor"] for item in result["resolved_actions"]] == ["玩家"]
    assert result["conflict_level"] == "none"
    assert "unrealized_directives" not in result
    visible = RenderingSystem()._build_visible_simulation(
        result,
        {"location": "大厅", "visible_world": {}},
        visible_locations=["大厅"],
    )
    assert "unrealized_directives" not in visible
