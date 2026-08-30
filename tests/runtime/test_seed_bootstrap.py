import pytest

from src.story_engine.session import (
    ScenarioSeedError,
    SeedDraft,
    bind_play_profile,
    compile_seed_report,
    compile_scenario_seed,
    create_session_from_seed,
)
from src.story_engine.agents import default_offline_runtime_factories
from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.common.action_target import bind_action_target


def test_text_seed_compiles_only_explicit_facts():
    scenario = compile_scenario_seed(
        """
        标题：夜班档案室
        地点：档案室 -> 走廊
        角色：林夏 | 调查员 | 谨慎、执着 | 找到账本 | 玩家 | 地点=档案室
        角色：老周 | 保管人 | 多疑 | 守住账本 | 地点=档案室
        物品：旧账本 | document | 档案室 | 隐藏
        初始状态：雨夜里，林夏刚被请进档案室。
        规则：普通现实；未知传闻不能直接成为事实。
        """
    )

    assert scenario.name == "夜班档案室"
    assert scenario.player_character_name == "林夏"
    assert set(scenario.initial_actor_states) == {"林夏", "老周"}
    assert scenario.initial_actor_states["老周"]["location"] == "档案室"
    assert scenario.initial_world_objects["旧账本"]["is_location"] is False
    assert scenario.initial_world_objects["旧账本"]["location"] == "档案室"
    assert scenario.initial_world_objects["档案室"]["connected_to"] == ["走廊"]
    assert scenario.initial_world_objects["走廊"]["connected_to"] == ["档案室"]
    assert scenario.metadata["seed_compiler"] == "deterministic-v1"


def test_mapping_seed_rejects_unknown_top_level_keys():
    with pytest.raises(ScenarioSeedError, match="invalid at"):
        compile_scenario_seed(
            {
                "premise": "一个房间。",
                "totally_unmodelled_field": "不要静默丢弃",
            }
        )


def test_seed_report_exposes_non_authoritative_warning():
    report = compile_seed_report("你是守夜人，独自在塔楼值班。")
    assert report.scenario.player_character_name == "守夜人"
    assert report.unresolved == ()
    assert report.warnings
    assert report.to_dict()["scenario"]["metadata"]["seed_format"] == "text"


def test_offline_profile_runs_a_real_first_turn_without_model_services():
    source = """
    标题：空屋
    地点：客厅
    角色：玩家 | 来客 | 警觉 | 查看房间 | 玩家 | 地点=客厅
    角色：守门人 | 看守 | 克制 | 留意来客 | 地点=客厅
    初始状态：门在身后合上，屋里只有两个人。
    """
    scenario = bind_play_profile(compile_scenario_seed(source), "offline")
    session = create_session_from_seed(
        scenario,
        agent_runtime_factories=default_offline_runtime_factories(),
        random_seed="seed-smoke",
    )
    try:
        context = session.run_step(overrides={"玩家": "环顾客厅。"})
        assert context["step_committed"] is True
        assert context["simulation_result"]["resolved_actions"]
        assert session.step_count == 1
    finally:
        session.close()


def test_seed_draft_is_extra_forbid():
    with pytest.raises(Exception):
        SeedDraft.model_validate({"premise": "x", "unknown": "y"})


def test_natural_language_target_binds_only_unique_visible_targets():
    perception = AgentPerception(
        actor_name="玩家",
        step=0,
        world_view={
            "visible_world": {
                "旧钥匙": {"aliases": ["钥匙"]},
                "客厅": {"is_location": True},
            },
            "visible_actors": ["玩家", "守门人"],
        },
    )
    bound = bind_action_target(
        AgentAction("interact", "拿起旧钥匙。"),
        actor_name="玩家",
        perception=perception,
    )
    assert bound.status == "bound"
    assert bound.action.target == "旧钥匙"

    ambiguous = bind_action_target(
        AgentAction("interact", "拿起钥匙。"),
        actor_name="玩家",
        perception=AgentPerception(
            actor_name="玩家",
            step=0,
            world_view={
                "visible_world": {
                    "钥匙甲": {"aliases": ["钥匙"]},
                    "钥匙乙": {"aliases": ["钥匙"]},
                },
            },
        ),
    )
    assert ambiguous.status == "ambiguous"
    assert ambiguous.action.target == ""
