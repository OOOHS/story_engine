from copy import deepcopy

from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.components.relationship_state import RelationshipState
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.persona import Persona
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.situation_state import SituationState
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem
from src.story_engine.scenarios.false_heiress import false_heiress_scenario
from src.story_engine.web.adapter import WebGameAdapter


def test_relationship_state_extracts_and_syncs_actor_relations():
    actor_states = {
        "沈先生": {
            "favor_林见微": 1,
            "malice_林见微": 2,
            "trust_林见微": -1,
        }
    }
    relationships = RelationshipState.from_actor_states(actor_states)

    assert relationships.get_metrics("沈先生", "林见微") == {
        "favor": 1,
        "malice": 2,
        "trust": -1,
    }

    relationships.apply_delta("沈先生", "林见微", favor_delta=1, malice_delta=-1)
    relationships.sync_actor_states(actor_states)

    assert actor_states["沈先生"]["favor_林见微"] == 2
    assert actor_states["沈先生"]["malice_林见微"] == 1


def test_scene_state_view_pov_contains_spatial_facts():
    scene = SceneState(
        world_objects={
            "沈宅客厅": {
                "default_zone": "entry",
                "zones": {
                    "entry": {"label": "入口处"},
                    "sofa": {"label": "长沙发边"},
                },
            }
        },
        actor_states={
            "林见微": {"location": "沈宅客厅", "sub_location": "entry", "stance": "standing"},
            "沈昭宁": {
                "location": "沈宅客厅",
                "sub_location": "sofa",
                "stance": "seated",
                "focus_target": "林见微",
                "side_with": "沈夫人",
            },
        },
    )

    pov = scene.get_view_pov("林见微")

    assert pov["viewer_zone"] == "entry"
    assert any("沈昭宁坐在长沙发边" in fact for fact in pov["visible_spatial_facts"])
    assert any("立场明显偏向沈夫人" in fact for fact in pov["visible_spatial_facts"])


def test_scene_state_apply_updates_sets_default_sub_location():
    scene = SceneState(
        world_objects={
            "餐厅": {
                "default_zone": "door",
                "zones": {
                    "door": {"label": "门边"},
                },
            }
        },
        actor_states={
            "林见微": {"location": "沈宅客厅"},
        },
    )

    scene.apply_updates({"actor_states": {"林见微": {"location": "餐厅"}}})

    assert scene.get_actor_state("林见微")["sub_location"] == "door"


def test_storylet_packet_keeps_structured_beat_data():
    system = SimulationSystem()
    scene = SceneState(scene_flags={"day_phase": "arrival"})
    packet = system._build_storylet_packet(
        scene_state=scene,
        active_storylets=[
            {
                "storylet_id": "opening",
                "priority": 99,
                "tags": ["public"],
                "beat": {
                    "preferred_template_ids": ["white_lotus_needling"],
                    "required_flags": ["white_lotus"],
                },
            }
        ],
        current_step=0,
    )

    assert packet["require_hit"] is True
    assert packet["preferred_template_ids"] == ["white_lotus_needling"]
    assert packet["priority_beats"][0]["required_flags"] == ["white_lotus"]


def test_storylet_packet_can_force_non_repeated_dinner_trap():
    system = SimulationSystem()
    scene = SceneState(
        scene_flags={
            "day_phase": "dinner",
            "recent_conflict_template_ids": [
                "family_public_blame",
                "white_lotus_needling",
                "brother_shuts_you_down",
            ],
        }
    )
    packet = system._build_storylet_packet(
        scene_state=scene,
        active_storylets=[
            {
                "storylet_id": "first_round_public_blame",
                "priority": 99,
                "tags": ["public", "blame", "fast"],
                "beat": {"preferred_template_ids": ["family_public_blame"]},
            },
            {
                "storylet_id": "family_closes_ranks",
                "priority": 96,
                "tags": ["bias", "pressure", "family"],
                "beat": {"preferred_template_ids": ["family_public_blame", "brother_shuts_you_down", "engagement_side_with_her"]},
            },
            {
                "storylet_id": "dinner_seating_cut",
                "priority": 98,
                "tags": ["dinner", "bias", "public"],
                "beat": {"preferred_template_ids": ["dining_seating_slight", "engagement_side_with_her"]},
            },
            {
                "storylet_id": "small_object_frame",
                "priority": 94,
                "tags": ["trap", "object", "public"],
                "beat": {"preferred_template_ids": ["small_object_frame"]},
            },
        ],
        current_step=3,
        situation_packet={
            "focus_situation": {
                "kind": "frontstage",
                "tags": ["dinner", "player_visible", "public", "bias", "white_lotus"],
            }
        },
    )

    assert packet["require_hit"] is True
    assert packet["forced_storylet_id"] == "dinner_seating_cut"


def test_storylet_enforcement_does_not_accept_flag_only_claim_as_realized_hit():
    control = SimulationControl(llm_config={})
    storylet = {
        "storylet_id": "white_lotus_opening_move",
        "priority": 97,
        "tags": ["trap", "social", "white_lotus"],
        "beat": {
            "preferred_actors": ["沈昭宁"],
            "preferred_template_ids": ["white_lotus_needling"],
            "required_flags": ["white_lotus", "trap"],
            "visibility": "public",
        },
    }
    result = control._enforce_storylets(
        {
            "resolved_actions": [
                {
                    "actor": "林见微",
                    "intent": "我抬眼看过去",
                    "outcome": "success",
                    "location": "沈宅客厅",
                    "result": "抬眼看了过去。",
                    "visibility": "public",
                }
            ],
            "storylet_hits": ["white_lotus_opening_move"],
            "conflict_flags": ["white_lotus", "trap"],
            "applied_conflict_templates": [],
        },
        {
            "player_pov": {"location": "沈宅客厅"},
            "storylet_pressure": {
                "priority_storylets": [storylet],
                "forced_storylet_id": "white_lotus_opening_move",
                "require_hit": True,
            },
            "conflict": {
                "active_templates": [
                    {
                        "template_id": "white_lotus_needling",
                        "instruction": "温温柔柔地把玩家踩进失礼位置。",
                        "preferred_actors": ["沈昭宁"],
                        "tags": ["white_lotus", "public", "trap", "provocation"],
                        "fallback_results": [
                            "先笑着替你说“刚回来不懂家里的规矩也正常”，像在解围，实际却先替你认下了失礼。"
                        ],
                    }
                ],
            },
        },
    )

    injected = [
        item for item in result["resolved_actions"]
        if item.get("actor") == "沈昭宁"
    ]

    assert injected
    assert result["storylet_hits"] == ["white_lotus_opening_move"]
    assert "white_lotus_needling" in result["applied_conflict_templates"]


def test_situation_packet_contains_frontstage_commitment_and_plot_layers():
    class DummyClock:
        current_step = 1

    system = SimulationSystem()
    scenario = false_heiress_scenario
    scene = SceneState(
        world_objects=deepcopy(scenario.initial_world_objects),
        actor_states=deepcopy(scenario.initial_actor_states),
        scene_flags=deepcopy(scenario.initial_scene_flags),
    )
    plot_state = PlotState.from_configs(scenario.plot_entities)
    timeline = system._refresh_timeline(
        scene,
        {"clock": DummyClock()},
        player_name="林见微",
    )
    player_pov = scene.get_view_pov("林见微")
    situation_state = SituationState()

    packet = system._refresh_situations(
        scene_state=scene,
        plot_state=plot_state,
        situation_state=situation_state,
        player_name="林见微",
        player_pov=player_pov,
        timeline_packet=timeline,
        current_step=DummyClock.current_step,
    )

    kinds = {item["kind"] for item in packet["active_situations"]}
    assert "frontstage" in kinds
    assert "commitment" in kinds
    assert "plot_pressure" in kinds
    assert packet["focus_situation"]["kind"] == "frontstage"


def test_due_commitment_can_stage_actors_into_new_location():
    class DummyClock:
        current_step = 1

    system = SimulationSystem()
    scene = SceneState(
        world_objects={
            "沈宅客厅": {"default_zone": "entry"},
            "餐厅": {"default_zone": "door"},
        },
        actor_states={
            "沈昭宁": {"location": "沈宅客厅", "sub_location": "entry"},
        },
        scene_flags={
            "day_phase": "pre_dinner",
            "upcoming_commitments": [
                {
                    "commitment_id": "family_dinner",
                    "due_step": 1,
                    "grace_steps": 0,
                    "phase": "dinner",
                    "title": "家宴",
                    "summary": "开席",
                    "location": "餐厅",
                    "stage_actors": [
                        {
                            "actor": "沈昭宁",
                            "location": "餐厅",
                            "sub_location": "door",
                        }
                    ],
                }
            ],
        },
    )

    system._refresh_timeline(scene, {"clock": DummyClock()})

    assert scene.get_actor_location("沈昭宁") == "餐厅"
    assert scene.get_actor_state("沈昭宁")["sub_location"] == "door"


def test_player_auto_intent_is_stabilized_away_from_repeated_passivity():
    system = InputSystem()
    scene = SceneState(
        world_objects={
            "餐厅": {
                "default_zone": "door",
                "zones": {"door": {"label": "门边"}},
            }
        },
        actor_states={
            "林见微": {"location": "餐厅", "sub_location": "door"},
            "沈昭宁": {
                "location": "餐厅",
                "sub_location": "door",
                "framing_style": "white_lotus",
            },
        },
    )

    stabilized = system._stabilize_player_auto_intent(
        intent="我安静地用餐，目光低垂，不参与任何交流。",
        scene_state=scene,
        player_name="林见微",
        current_step=2,
    )

    assert "安静地用餐" not in stabilized
    assert stabilized

    stabilized_observe = system._stabilize_player_auto_intent(
        intent="先观察局势，避免贸然暴露自己。",
        scene_state=scene,
        player_name="林见微",
        current_step=1,
    )

    assert "先观察局势" not in stabilized_observe


def test_input_system_autonomously_builds_player_proposal_without_override():
    class SimulationControl(Component):
        pass

    class Persona(Component):
        def act(self, immediate_context: str = ""):
            del immediate_context
            return {"thought": "我得先占个位。", "action": "我直接朝餐桌那边走过去。"}

    system = InputSystem()
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"沈宅客厅": {"default_zone": "entry"}},
            actor_states={"林见微": {"location": "沈宅客厅"}},
        )
    )
    player = Entity("林见微")
    player.add_component(Persona())

    context = {
        "dispatcher": None,
        "overrides": {},
        "clock": None,
        "player_name": "林见微",
        "inject_events": [],
        "intents": [],
    }

    system.update({"GameMaster": gm, "林见微": player}, context)

    assert context["intents"][0]["actor"] == "林见微"
    assert context["intents"][0]["source"] == "ai"
    assert context["intents"][0]["proposal_role"] == "character_proposal"


def test_input_system_rewrites_passive_white_lotus_npc_into_proactive_move():
    system = InputSystem()
    scene = SceneState(
        world_objects={
            "餐厅": {"default_zone": "door"},
        },
        actor_states={
            "林见微": {"location": "餐厅", "focus_target": "沈昭宁"},
            "沈昭宁": {
                "location": "餐厅",
                "focus_target": "林见微",
                "pressure_profile": "white_lotus",
                "framing_style": "white_lotus",
            },
        },
        scene_flags={"day_phase": "dinner"},
    )

    intent = system._stabilize_npc_auto_intent(
        intent="先观察局势，避免贸然暴露自己。",
        actor_name="沈昭宁",
        scene_state=scene,
        player_name="林见微",
        current_step=1,
    )

    assert "先观察局势" not in intent
    assert "林见微" in intent or "位置" in intent or "杯子" in intent


def test_renderer_detects_ungrounded_dialogue():
    renderer = NarrativeRenderer(llm_config={})
    assert renderer._has_ungrounded_dialogue(
        '她轻声说：“姐姐别闹了。”',
        '{"simulation_result":{"resolved_actions":[{"result":"轻声提醒了你一句。"}]}}',
    ) is True


def test_persona_action_line_preserves_full_sentence_without_ellipsis():
    persona = Persona(llm_config={})
    line = persona._normalize_action_line("我起身离席，走向客厅，但刻意放慢脚步，留意身后是否有人跟上。")

    assert line == "我起身离席，走向客厅，但刻意放慢脚步，留意身后是否有人跟上。"


def test_input_system_sanitizes_overwritten_player_auto_intent():
    system = InputSystem()
    sanitized = system._sanitize_auto_intent(
        "我平静地走向餐桌，在沈昭宁对面的空位旁停下脚步，手轻轻搭在椅背上，目光扫过主位的沈先生和沈夫人。",
        is_player=True,
    )

    assert "手轻轻搭在椅背上" not in sanitized
    assert "目光扫过主位" not in sanitized
    assert sanitized.endswith("。")


def test_renderer_rejects_ungrounded_melodramatic_family_line():
    renderer = NarrativeRenderer(llm_config={})
    payload = {
        "player_pov": {"location": "餐厅", "viewer": "林见微"},
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "沈昭宁",
                    "outcome": "complication",
                    "visibility": "public",
                    "result": "温温柔柔地把你往不利的位置上带。",
                }
            ]
        },
        "current_visible_facts": ["沈昭宁: 温温柔柔地把你往不利的位置上带。"],
    }

    text = renderer._ground_render_text(
        "她站起身，声音轻柔得像怕惊扰什么，说坐我对面真好，就像……我们终于都在家里了。",
        payload,
    )

    assert "终于都在家里了" not in text


def test_renderer_hides_backend_storylet_notes_from_public_text():
    renderer = NarrativeRenderer(llm_config={})
    text = renderer._build_fallback_text(
        {
            "player_pov": {"location": "餐厅", "viewer": "林见微"},
            "simulation_result": {
                "resolved_actions": [],
                "simulation_notes": [
                    "结构化模拟回退模式已启用。",
                    "优先 storylet 已被结构化推进：dinner_seating_cut",
                    "场面已经明显偏到你对面。",
                ],
            },
            "timeline": {},
        }
    )

    assert "storylet" not in text
    assert "结构化推进" not in text
    assert "场面已经明显偏到你对面。" in text


def test_motive_packet_ranks_visible_pressure_actors():
    system = SimulationSystem()
    scenario = false_heiress_scenario
    scene = SceneState(
        world_objects=scenario.initial_world_objects,
        actor_states=scenario.initial_actor_states,
        scene_flags=scenario.initial_scene_flags,
    )
    player_pov = scene.get_view_pov("林见微")
    social_packet = system._build_social_packet(
        scene_state=scene,
        relationship_state=None,
        player_name="林见微",
        player_pov=player_pov,
    )
    motive_packet = system._build_motive_packet(
        scene_state=scene,
        scenario=scenario,
        player_name="林见微",
        player_pov=player_pov,
        social_packet=social_packet,
        timeline_packet={},
    )

    assert motive_packet["visible_pressures"][0]["actor"] == "沈昭宁"
    assert motive_packet["requires_active_push"] is True


def test_storylet_resolution_is_routed_by_matching_situations():
    class DummyClock:
        current_step = 0

    system = SimulationSystem()
    scenario = false_heiress_scenario
    scene = SceneState(
        world_objects=deepcopy(scenario.initial_world_objects),
        actor_states=deepcopy(scenario.initial_actor_states),
        scene_flags=deepcopy(scenario.initial_scene_flags),
    )
    plot_state = PlotState.from_configs(scenario.plot_entities)
    timeline = system._refresh_timeline(
        scene,
        {"clock": DummyClock()},
        player_name="林见微",
    )
    player_pov = scene.get_view_pov("林见微")

    situation_packet = system._refresh_situations(
        scene_state=scene,
        plot_state=plot_state,
        situation_state=SituationState(),
        player_name="林见微",
        player_pov=player_pov,
        timeline_packet=timeline,
        current_step=DummyClock.current_step,
    )
    active_storylets = system._resolve_storylets(
        scene_state=scene,
        plot_state=plot_state,
        scenario=scenario,
        situation_packet=situation_packet,
    )

    white_lotus_storylet = next(
        item for item in active_storylets
        if item["storylet_id"] == "white_lotus_opening_move"
    )
    assert white_lotus_storylet["matched_situation_ids"]
    assert white_lotus_storylet["focus_situation_match"] is True


def test_conflict_template_for_actor_follows_signature_templates():
    control = SimulationControl(llm_config={})
    conflict_packet = {
        "active_templates": [
            {
                "template_id": template.template_id,
                "instruction": template.instruction,
                "preferred_actors": list(template.preferred_actors),
                "tags": list(template.tags),
                "fallback_results": list(template.fallback_results),
            }
            for template in false_heiress_scenario.conflict_templates
        ],
        "storylet_template_ids": [],
    }
    template = control._pick_template_for_actor(
        actor_entry={
            "actor": "沈砚川",
            "pressure_profile": "cutoff_guard",
            "public_lever": "打断、定调和逼人退一步",
            "signature_templates": ["brother_shuts_you_down", "family_public_blame"],
        },
        conflict_packet=conflict_packet,
        used_templates=[],
    )

    assert template["template_id"] == "brother_shuts_you_down"


def test_player_question_is_not_summarized_as_taking_a_seat():
    control = SimulationControl(llm_config={})
    summary = control._summarize_fallback_intent(
        actor="林见微",
        intent="我看着沈昭宁，问她怎么还坐在那个位置上",
        item={"is_player": True, "location": "沈宅客厅"},
        input_payload={"conflict": {}},
    )

    assert summary == "把话挑明了问了回去。"


def test_web_adapter_shows_auto_player_action_when_no_manual_input():
    adapter = WebGameAdapter(false_heiress_scenario)
    entry = adapter._build_history_entry(
        phase_trace={
            "player_intent": "我转向沈夫人，语气平静地开口：母亲，大哥……",
            "player_intent_source": "ai",
        },
        player_command="",
        inject_event="",
    )

    assert entry["player_command"] == "我转向沈夫人，语气平静地开口：母亲，大哥……"
    assert entry["player_command_source"] == "ai"


def test_auto_player_proposal_is_not_promoted_to_primary_anchor():
    system = SimulationSystem()
    packet = system._build_intent_focus_packet(
        intents=[
            {
                "actor": "林见微",
                "intent": "我看着沈昭宁，问她这位置到底算谁的。",
                "proposal_role": "character_proposal",
                "proposal_priority": 0.48,
                "source": "ai",
                "location": "沈宅客厅",
            },
            {
                "actor": "沈昭宁",
                "intent": "她先笑着说别让大家难做。",
                "proposal_role": "character_proposal",
                "proposal_priority": 0.48,
                "source": "ai",
                "location": "沈宅客厅",
            },
        ],
        player_name="林见微",
        player_intent={
            "actor": "林见微",
            "intent": "我看着沈昭宁，问她这位置到底算谁的。",
            "proposal_role": "character_proposal",
            "proposal_priority": 0.48,
            "source": "ai",
        },
        timeline_packet={},
        reaction_context={},
    )

    assert packet["player_proposal"]["source"] == "ai"
    assert packet["player_proposal_is_primary"] is False
    assert packet["anchor_intent"] == {}


def test_player_relevant_commitment_builds_transition_pressure_before_staging():
    class DummyClock:
        current_step = 3

    system = SimulationSystem()
    scenario = false_heiress_scenario
    scene = SceneState(
        world_objects=deepcopy(scenario.initial_world_objects),
        actor_states=deepcopy(scenario.initial_actor_states),
        scene_flags=deepcopy(scenario.initial_scene_flags),
    )

    timeline = system._refresh_timeline(
        scene,
        {"clock": DummyClock()},
        player_name="林见微",
    )

    assert scene.get_actor_location("沈昭宁") == "餐厅"
    assert timeline["transition_pressure"]["requires_human_backlash"] is True
    assert timeline["transition_pressure"]["target_location"] == "餐厅"
    assert "沈昭宁" in timeline["transition_pressure"]["carrier_actors"]
    assert "沈砚川" in timeline["transition_pressure"]["carrier_actors"]


def test_transition_pressure_keeps_conflict_alive_when_player_refuses_commitment():
    class DummyClock:
        current_step = 3

    system = SimulationSystem()
    scenario = false_heiress_scenario
    scene = SceneState(
        world_objects=deepcopy(scenario.initial_world_objects),
        actor_states=deepcopy(scenario.initial_actor_states),
        scene_flags=deepcopy(scenario.initial_scene_flags),
    )

    timeline = system._refresh_timeline(
        scene,
        {"clock": DummyClock()},
        player_name="林见微",
    )
    player_pov = scene.get_view_pov("林见微")
    social_packet = system._build_social_packet(
        scene_state=scene,
        relationship_state=None,
        player_name="林见微",
        player_pov=player_pov,
    )
    reaction_context = system._build_reaction_context(
        "林见微",
        player_pov,
        {"actor": "林见微", "intent": "我不去餐厅，我就在这儿站着"},
        social_packet,
        timeline,
    )
    motive_packet = system._build_motive_packet(
        scene_state=scene,
        scenario=scenario,
        player_name="林见微",
        player_pov=player_pov,
        social_packet=social_packet,
        timeline_packet=timeline,
    )
    conflict_packet = system._build_conflict_packet(
        scene_state=scene,
        scenario=scenario,
        current_step=DummyClock.current_step,
        reaction_context=reaction_context,
        storylet_packet={},
        timeline_packet=timeline,
    )

    assert player_pov["visible_actors"] == ["林见微"]
    assert reaction_context["transition_watchers"]
    assert "沈昭宁" in reaction_context["hostile_watchers"]
    assert reaction_context["requires_reaction"] is True
    assert motive_packet["visible_pressures"][0]["actor"] == "沈昭宁"
    assert conflict_packet["require_visible_conflict"] is True


def test_rendering_keeps_reactions_from_pre_move_location_visible_for_that_turn():
    system = RenderingSystem()
    visible = system._build_visible_simulation(
        simulation_result={
            "resolved_actions": [
                {
                    "actor": "沈昭宁",
                    "outcome": "complication",
                    "location": "沈宅客厅",
                    "result": "先替你把失礼认了下来。",
                    "visibility": "public",
                },
                {
                    "actor": "林见微",
                    "outcome": "success",
                    "location": "餐厅",
                    "result": "动身前往餐厅。",
                    "visibility": "public",
                },
            ]
        },
        player_pov={"location": "餐厅"},
        visible_locations=["沈宅客厅", "餐厅"],
    )

    assert len(visible["resolved_actions"]) == 2
