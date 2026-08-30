from copy import deepcopy

from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.agents.types import AgentDecision
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.memory import Memory
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.components.scene_state import SceneState
from src.story_engine.scenarios.config import NarrationConfig, ScenarioConfig
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.cognition import CognitionSystem
from src.story_engine.systems.memory import MemorySystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem
from src.story_engine.systems.system import System
from src.story_engine_content.bundled.false_heiress import false_heiress_scenario
from src.story_engine.social import RelationshipBook, SocialRelationRegistry
from src.story_engine.web.adapter import WebGameAdapter
from src.story_engine.session import create_session
from src.story_engine.prefabs.templates import create_agent


class _StubHermesRuntime:
    """Stands in for the real Hermes container runtime in tests that only
    care about scenario/session wiring and never invoke decide()."""

    def decide(self, entity, perception):
        raise NotImplementedError("stub hermes runtime never decides in these tests")


def _bundled_runtime_factories():
    return {"hermes": lambda entity, runtime_config: _StubHermesRuntime()}


def test_pair_relationship_aggregates_both_directional_tracks():
    registry = SocialRelationRegistry()
    book = registry.to_relationship_book()
    book.set_track("沈先生", "林见微", "favor", 1)
    book.set_track("沈先生", "林见微", "malice", 2)
    book.set_track("沈先生", "林见微", "trust", -1)
    book.set_track("林见微", "沈先生", "trust", 2)
    entities = {}
    registry.apply_relationship_book(book, entities)

    relation_entity = registry.get("pair:林见微<->沈先生")
    relation = relation_entity.get_component("SocialRelation")
    tracks = relation_entity.get_component("RelationshipTracks")
    assert relation_entity is entities["Relationship:林见微<->沈先生"]
    assert relation.relation_kind == "pair"
    assert tracks.get("沈先生", "林见微") == {
        "favor": 1,
        "malice": 2,
        "trust": -1,
    }
    assert tracks.get("林见微", "沈先生") == {"trust": 2}


def _scenario_relationship_book():
    book = RelationshipBook()
    for relation in false_heiress_scenario.initial_relationships:
        book.ensure(*relation.participants)
        for direction in relation.directions:
            for track_id, value in direction.tracks.items():
                book.set_track(direction.source, direction.target, track_id, value)
    return book


def test_scenario_loader_builds_sparse_pair_relationships_and_structured_traits():
    session = create_session(
        false_heiress_scenario,
        random_seed=17,
        agent_runtime_factories=_bundled_runtime_factories(),
    )
    registry = session.runner.relation_registry

    assert len(tuple(registry.entities("pair"))) == 10
    pair = registry.get("pair:林见微<->沈昭宁")
    tracks = pair.get_component("RelationshipTracks")
    assert tracks.get("林见微", "沈昭宁")["malice"] == 2
    assert tracks.get("沈昭宁", "林见微")["malice"] == 4
    assert pair.get_component("RelationshipBits").bits["family_sisters"]
    assert session.entities["沈昭宁"].get_component("TraitState").traits[
        "calculating"
    ].intensity == 0.9
    assert session.entities["沈昭宁"].get_component("SentimentState") is not None


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


def test_actor_pov_hides_host_and_private_fields_by_default():
    scene = SceneState(
        world_objects={"房间": {}},
        actor_states={
            "甲": {
                "location": "房间",
                "capabilities": ["magic"],
                "fear": 0.7,
                "dramatic_motive": "推动冲突",
            },
            "乙": {
                "location": "房间",
                "stance": "seated",
                "expression": "平静",
                "bias": "甲",
                "skills": {"deception": 1.0},
                "secret": "幕后秘密",
            },
        },
    )

    pov = scene.get_view_pov("甲")

    assert pov["visible_actor_states"]["乙"] == {
        "location": "房间",
        "stance": "seated",
        "expression": "平静",
    }
    assert "capabilities" not in pov["visible_actor_states"]["甲"]
    assert scene.get_self_actor_state("甲") == {
        "location": "房间",
        "capabilities": ["magic"],
        "fear": 0.7,
    }


def test_content_can_explicitly_publish_or_hide_actor_state_fields():
    scene = SceneState(
        world_objects={"广场": {}},
        actor_states={
            "甲": {"location": "广场"},
            "卫兵": {
                "location": "广场",
                "side_with": "城主",
                "uniform": "蓝色制服",
                "wounded": True,
                "public_state_fields": ["uniform", "wounded"],
                "private_state_fields": ["side_with"],
            },
        },
    )

    visible = scene.get_view_pov("甲")["visible_actor_states"]["卫兵"]

    assert visible == {
        "location": "广场",
        "uniform": "蓝色制服",
        "wounded": True,
    }
    assert "public_state_fields" not in visible
    assert "private_state_fields" not in visible


def test_object_pov_hides_host_affordance_policy_and_private_fields():
    scene = SceneState(
        world_objects={
            "房间": {},
            "面包": {
                "is_location": False,
                "location": "房间",
                "appearance": "新鲜",
                "combination": "host-secret",
                "private_state_fields": ["combination"],
                "stack_key": "bread",
                "policy_tags": ["acquire"],
                "affordances": [
                    {
                        "id": "eat",
                        "label": "吃掉",
                        "need_effects": {"hunger": -0.5},
                        "policy_tags": ["relief"],
                    }
                ],
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )

    raw = scene.get_object_state("面包")
    visible = scene.get_view_pov("甲")["visible_world"]["面包"]

    assert visible == {
        "is_location": False,
        "location": "房间",
        "appearance": "新鲜",
    }
    assert raw["affordances"][0]["policy_tags"] == ["relief"]
    assert raw["stack_key"] == "bread"


def test_actor_without_location_sees_owned_objects_not_the_global_world():
    scene = SceneState(
        world_objects={
            "远方房间": {},
            "远方秘密": {
                "is_location": False,
                "location": "远方房间",
            },
            "随身信件": {
                "is_location": False,
                "owner": "甲",
            },
        },
        actor_states={"甲": {"fear": 0.2}},
    )

    visible_world = scene.get_view_pov("甲")["visible_world"]

    assert visible_world == {
        "随身信件": {"is_location": False, "owner": "甲"}
    }
    assert "远方房间" not in visible_world
    assert "远方秘密" not in visible_world


def test_simulation_gm_gets_affordance_rules_without_host_policy_metadata():
    scene = SceneState(
        world_objects={
            "房间": {},
            "面包": {
                "is_location": False,
                "location": "房间",
                "stack_key": "bread",
                "affordances": [
                    {
                        "id": "eat",
                        "need_effects": {"hunger": -0.5},
                        "policy_tags": ["relief"],
                    }
                ],
            },
        },
        actor_states={
            "甲": {
                "location": "房间",
                "capabilities": ["eat"],
                "dramatic_motive": "制造冲突",
            }
        },
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    control = SimulationControl()
    gm.add_component(control)
    captured = {}

    class FakeLLM:
        def generate(self, prompt):
            captured["prompt"] = prompt
            return {"content": "{}"}

    control._llm = FakeLLM()

    control.simulate(
        {
            "intents": [],
            "storylet_pressure": {
                "priority_storylets": [
                    {"storylet_id": "secret_director_beat"}
                ]
            },
            "conflict": {
                "active_templates": [
                    {"instruction": "secret_conflict_instruction"}
                ]
            },
            "motive_pressure": {
                "visible_pressures": [
                    {"dramatic_motive": "secret_motive_instruction"}
                ]
            },
        }
    )

    prompt = captured["prompt"]
    assert '"id": "eat"' in prompt
    assert '"need_effects"' in prompt
    assert '"capabilities"' in prompt
    assert "policy_tags" not in prompt
    assert '"stack_key":' not in prompt
    assert "dramatic_motive" not in prompt
    assert "secret_director_beat" not in prompt
    assert "secret_conflict_instruction" not in prompt
    assert "secret_motive_instruction" not in prompt


def test_actor_without_location_does_not_receive_global_actor_state():
    scene = SceneState(
        world_objects={"房间": {}},
        actor_states={
            "甲": {"fear": 0.2},
            "远方角色": {"location": "房间", "stance": "standing"},
        },
    )

    pov = scene.get_view_pov("甲")

    assert pov["visible_actors"] == ["甲"]
    assert "远方角色" not in pov["visible_actor_states"]


def test_scene_pov_exposes_only_default_or_content_declared_public_flags():
    scene = SceneState(
        description="暴雨中的广场",
        world_objects={"广场": {}},
        actor_states={"甲": {"location": "广场"}},
        scene_flags={
            "day_phase": "night",
            "weather": "storm",
            "ceremony_bell": "ringing",
            "secret_clock": 3,
            "world_version": 9,
        },
        public_scene_fields=["ceremony_bell", "secret_clock"],
        private_scene_fields=["secret_clock"],
    )

    public_scene = scene.get_view_pov("甲")["public_scene"]

    assert public_scene == {
        "description": "暴雨中的广场",
        "flags": {
            "day_phase": "night",
            "weather": "storm",
            "ceremony_bell": "ringing",
        },
    }
    assert "world_version" not in str(public_scene)


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

    assert packet["mode"] == "advisory_opportunities"
    assert packet["salient_storylet_id"] == "opening"
    assert "require_hit" not in packet
    assert packet["preferred_template_ids"] == ["white_lotus_needling"]
    assert packet["priority_beats"][0]["required_flags"] == ["white_lotus"]


def test_storylet_packet_surfaces_non_repeated_dinner_opportunity():
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

    assert packet["salient_storylet_id"] == "dinner_seating_cut"
    assert "forced_storylet_id" not in packet


def test_storylet_host_detection_never_invents_an_unproposed_character_action():
    engine = SimulationSystem().storylets
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
    result = {
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
        }

    hits = engine.detect_hits([storylet], result)

    injected = [
        item for item in result["resolved_actions"]
        if item.get("actor") == "沈昭宁"
    ]

    assert injected == []
    assert hits == []
    assert result["storylet_hits"] == ["white_lotus_opening_move"]


def test_storylet_host_detection_recognizes_a_naturally_realized_beat():
    engine = SimulationSystem().storylets
    storylet = {
        "storylet_id": "opening_pressure",
        "beat": {
            "preferred_actors": ["乙"],
            "visibility": "public",
        },
    }

    hits = engine.detect_hits(
        [storylet],
        {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "outcome": "complication",
                    "visibility": "public",
                    "result": "乙根据自己的行动当众提出了异议。",
                }
            ]
        },
    )

    assert hits == ["opening_pressure"]




def test_due_commitment_becomes_private_schedule_without_moving_actor():
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
                    "participants": ["沈昭宁"],
                }
            ],
        },
    )

    system._refresh_timeline(scene, {"clock": DummyClock()})

    assert scene.get_actor_location("沈昭宁") == "沈宅客厅"
    schedule = system.timeline.private_schedule(scene, "沈昭宁", 1)
    assert schedule["due"][0]["location"] == "餐厅"
    assert schedule["due"][0]["commitment_id"] == "family_dinner"


def test_input_system_autonomously_builds_player_proposal_without_override():
    class SimulationControl(Component):
        pass

    class Runtime:
        def decide(self, _entity, _perception):
            return AgentDecision(
                thought="我得先占个位。",
                action="我直接朝餐桌那边走过去。",
            )

    system = InputSystem()
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"沈宅客厅": {"default_zone": "entry"}},
            actor_states={"林见微": {"location": "沈宅客厅"}},
        )
    )
    player = create_agent("林见微", "来客", "果断", [], agent_runtime="llm")
    registry = AgentRegistry()
    registry.register(player, Runtime())

    context = {
        "dispatcher": None,
        "overrides": {},
        "clock": None,
        "player_name": "林见微",
        "inject_events": [],
        "intents": [],
        "agent_registry": registry,
    }

    system.update({"GameMaster": gm, "林见微": player}, context)

    assert context["intents"][0]["actor"] == "林见微"
    assert context["intents"][0]["source"] == "ai"
    assert context["intents"][0]["proposal_role"] == "character_proposal"

def test_renderer_detects_ungrounded_dialogue():
    renderer = NarrativeRenderer(llm_config={})
    assert renderer._has_ungrounded_dialogue(
        '她轻声说：“姐姐别闹了。”',
        '{"simulation_result":{"resolved_actions":[{"result":"轻声提醒了你一句。"}]}}',
    ) is True


def test_renderer_uses_content_narration_policy_without_core_pacing_default():
    scenario = ScenarioConfig(
        name="安静场景",
        default_agent_runtime="llm",
        description="测试",
        environment="空房间",
        initial_state="角色独处。",
        narration=NarrationConfig(
            guidance=["语气克制，允许停顿，不主动制造紧迫感。"],
            max_sentences=2,
            max_characters=40,
        ),
    )
    renderer = NarrativeRenderer(llm_config={}, scenario=scenario)
    narrator = Entity("Narrator")
    narrator.add_component(renderer)
    captured = {}

    class FakeLLM:
        def generate(self, prompt):
            captured["prompt"] = prompt
            return {"content": "第一句。第二句。第三句。"}

    renderer._llm = FakeLLM()
    text = renderer.render(
        {
            "player_pov": {"viewer": "甲", "location": "空房间"},
            "simulation_result": {"resolved_actions": []},
        }
    )

    assert "语气克制，允许停顿，不主动制造紧迫感。" in captured["prompt"]
    assert '"max_sentences": 2' in captured["prompt"]
    assert '"max_characters": 40' in captured["prompt"]
    assert "节奏要快" not in captured["prompt"]
    assert "锋利感" not in captured["prompt"]
    assert text == "第一句。 第二句。"


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


def test_memory_archives_each_actor_personal_cognition_not_player_narration():
    class Clock:
        current_step = 4

    gm = Entity("GameMaster")
    gm_memory = Memory(agent_name="gm-memory-projection-test")
    gm.add_component(gm_memory)
    gm.add_component(
        SceneState(
            description="测试世界",
            world_objects={"大厅": {}, "远处": {}},
            actor_states={
                "甲": {"location": "大厅"},
                "乙": {"location": "大厅"},
                "丙": {"location": "远处"},
            },
            scene_flags={"day_phase": "public_phase_marker"},
        )
    )
    actors = {}
    memories = {}
    for name in ("甲", "乙", "丙"):
        actor = Entity(name)
        memory = Memory(agent_name=f"actor-memory-projection-test-{name}")
        actor.add_component(memory)
        actor.add_component(Cognition())
        actors[name] = actor
        memories[name] = memory
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "intent": "检查房间",
                "action_kind": "observe",
                "outcome": "success",
                "result": "甲确认大厅仍然安静。",
                "location": "大厅",
                "visibility": "public",
            },
            {
                "actor": "丙",
                "intent": "检查远处",
                "action_kind": "observe",
                "outcome": "success",
                "result": "丙在远处发现了异动。",
                "location": "远处",
                "visibility": "public",
            }
        ],
        "state_updates": {"scene": {"public_status": "quiet"}},
        "object_lifecycle": [],
        "exchanges": [],
    }
    context = {
        "clock": Clock(),
        "memory_namespace": "memory-projection-test",
        "intents": [{"actor": "甲", "intent": "检查房间", "is_player": True}],
        "simulation_result": result,
        "visible_simulation_result": result,
        "player_pov": {"viewer": "甲", "location": "大厅"},
        "timeline": {
            "transition_pressure": {
                "secret_director_timeline_marker": True,
            },
            "private_schedule": ["secret_schedule_marker"],
        },
        "outcome_check_traces": ["secret_host_roll_marker"],
        "goal_transitions": ["secret_goal_policy_marker"],
        "rendered_text": "public_rendered_marker",
    }

    entities = {"GameMaster": gm, **actors}
    CognitionSystem().update(entities, context)
    MemorySystem().update(entities, context)

    gm_text = gm_memory.list_memories()[0]["content"]
    actor_texts = {
        name: memory.list_memories()[0]["content"]
        for name, memory in memories.items()
    }
    assert "甲确认大厅仍然安静" in gm_text
    assert "丙在远处发现了异动" in gm_text
    assert "public_status" in gm_text
    assert "public_rendered_marker" not in gm_text
    assert "secret_director_timeline_marker" not in gm_text
    assert "secret_schedule_marker" not in gm_text
    assert "secret_host_roll_marker" not in gm_text
    assert "secret_goal_policy_marker" not in gm_text
    assert "甲确认大厅仍然安静" in actor_texts["甲"]
    assert "甲确认大厅仍然安静" in actor_texts["乙"]
    assert "丙在远处发现了异动" not in actor_texts["甲"]
    assert "丙在远处发现了异动" not in actor_texts["乙"]
    assert "丙在远处发现了异动" in actor_texts["丙"]
    assert "甲确认大厅仍然安静" not in actor_texts["丙"]
    for actor_text in actor_texts.values():
        assert "public_phase_marker" in actor_text
        assert "public_rendered_marker" not in actor_text
        assert "secret_director_timeline_marker" not in actor_text
        assert "secret_schedule_marker" not in actor_text


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
        relationship_book=_scenario_relationship_book(),
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
    assert "bias" not in player_pov["visible_actor_states"]["沈昭宁"]
    assert "dramatic_motive" not in player_pov["visible_actor_states"]["沈昭宁"]
    assert motive_packet["visible_pressures"][0]["dramatic_motive"]






def test_fallback_summary_preserves_actor_proposal_without_story_heuristics():
    control = SimulationControl(llm_config={})
    summary = control._summarize_fallback_intent(
        actor="甲",
        intent="我看着乙，询问他为何仍留在那个位置",
        item={"is_player": True, "location": "大厅"},
        input_payload={"conflict": {}},
    )

    assert summary == "甲尝试执行其意图：我看着乙，询问他为何仍留在那个位置。"


def test_web_adapter_shows_auto_player_action_when_no_manual_input():
    adapter = WebGameAdapter(false_heiress_scenario, agent_runtime_factories=_bundled_runtime_factories())
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


def test_web_adapter_exposes_same_bounded_player_decision_context():
    adapter = WebGameAdapter(false_heiress_scenario, agent_runtime_factories=_bundled_runtime_factories())
    player_name = adapter._session.player_character_name
    cognition = adapter._session.entities[player_name].get_component("Cognition")
    cognition.secrets.append("只应留在完整私有认知中的秘密")
    cognition.record_world_event(
        event_id="manual-preview:event",
        statement="客厅里的灯突然熄灭了。",
        step=0,
        location="沈宅客厅",
        witness_mode="direct",
    )

    payload = adapter.get_state()
    decision = payload["player"]["decision_context"]

    assert decision["actor"] == player_name
    assert decision["pending_world_events"] == ["manual-preview:event"]
    assert decision["passive_observations"][-1]["result"] == (
        "客厅里的灯突然熄灭了。"
    )
    assert "secrets" not in decision
    assert "beliefs" not in decision
    assert cognition.pending_world_events == ["manual-preview:event"]


class _WebAuthoritativeFailure(System):
    def update(self, entities, context):
        raise RuntimeError("internal-secret-authoritative-detail")


class _WebDeliveryFailure(System):
    def update(self, entities, context):
        raise RuntimeError("internal-secret-delivery-detail")


def _dormant_nonplayer_web_agents(adapter):
    player = adapter._session.player_character_name
    for name, entity in adapter._session.entities.items():
        controller = entity.get_component("AgentController")
        if controller is not None and name != player:
            controller.activation_policy = "dormant"


def test_web_history_marks_authoritative_rollback_instead_of_fake_story_turn():
    adapter = WebGameAdapter(false_heiress_scenario, agent_runtime_factories=_bundled_runtime_factories())
    _dormant_nonplayer_web_agents(adapter)
    adapter._session.runner.systems[2] = _WebAuthoritativeFailure()

    payload = adapter.submit_turn("我留在原地。")
    entry = payload["last_step"]

    assert entry["kind"] == "system"
    assert entry["status"] == "rolled_back"
    assert entry["committed"] is False
    assert entry["title"] == "步骤已回滚"
    assert payload["step_count"] == 0
    assert payload["history"][-1] == entry
    assert "局面没有显著变化" not in entry["narration"]
    assert "internal-secret" not in str(entry)


def test_web_history_preserves_committed_world_when_delivery_fails():
    adapter = WebGameAdapter(false_heiress_scenario, agent_runtime_factories=_bundled_runtime_factories())
    _dormant_nonplayer_web_agents(adapter)
    rendering_index = next(
        index
        for index, system in enumerate(adapter._session.runner.systems)
        if system.__class__.__name__ == "RenderingSystem"
    )
    original_renderer = adapter._session.runner.systems[rendering_index]
    adapter._session.runner.systems[rendering_index] = _WebDeliveryFailure()

    payload = adapter.submit_turn("我留在原地。")
    entry = payload["last_step"]

    assert entry["kind"] == "turn"
    assert entry["status"] == "delivery_failed"
    assert entry["committed"] is True
    assert entry["narration"] == "世界状态已经推进，但本轮叙事文本交付失败。"
    assert payload["step_count"] == 1
    assert payload["delivery_pending"] is True
    assert "internal-secret" not in str(entry)

    blocked = adapter.submit_turn("这条新行动不能执行。")
    assert blocked["submission_blocked"] == "pending_delivery_retry"
    assert blocked["step_count"] == 1
    assert len(blocked["history"]) == len(payload["history"])

    adapter._session.runner.systems[rendering_index] = original_renderer
    recovered = adapter.retry_delivery()

    assert recovered["delivery_pending"] is False
    assert recovered["step_count"] == 1
    assert len(recovered["history"]) == len(payload["history"])
    assert recovered["last_step"]["status"] == "committed"
    assert recovered["last_step"]["committed"] is True
    assert recovered["last_step"]["narration"] != (
        "世界状态已经推进，但本轮叙事文本交付失败。"
    )


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


def test_player_relevant_commitment_builds_transition_pressure_without_staging():
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

    assert scene.get_actor_location("沈昭宁") == "沈宅客厅"
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
        relationship_book=_scenario_relationship_book(),
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

    assert "沈昭宁" in player_pov["visible_actors"]
    assert "沈砚川" in player_pov["visible_actors"]
    assert reaction_context["transition_watchers"]
    assert "沈昭宁" in reaction_context["hostile_watchers"]
    assert reaction_context["requires_reaction"] is True
    assert motive_packet["visible_pressures"][0]["actor"] == "沈昭宁"
    assert conflict_packet["visible_conflict_opportunity"] is True
    assert conflict_packet["pressure_state"] in {"rising", "acute"}
    assert "require_visible_conflict" not in conflict_packet


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
