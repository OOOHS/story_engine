from src.story_engine.agents.llm_runtime import LLMCharacterAgent
from src.story_engine.agents.policy import CharacterPolicy
from src.story_engine.agents.scheduler import AgentScheduler
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.scene_state import SceneState
from src.story_engine.narrative.timeline import TimelineEngine
from src.story_engine.prefabs.templates import create_agent


def _scene():
    return SceneState(
        world_objects={
            "住处": {"connected_to": ["礼堂"]},
            "礼堂": {"connected_to": ["住处"]},
        },
        actor_states={
            "玩家": {"location": "礼堂"},
            "甲": {"location": "住处"},
            "乙": {"location": "住处"},
        },
        scene_flags={
            "upcoming_commitments": [
                {
                    "commitment_id": "ceremony",
                    "title": "参加仪式",
                    "summary": "受邀在仪式开始前到达礼堂。",
                    "participants": ["甲"],
                    "location": "礼堂",
                    "due_step": 3,
                    "wake_before_steps": 2,
                    "grace_steps": 0,
                }
            ]
        },
    )


def test_upcoming_schedule_wakes_offscreen_agent_without_moving_it():
    scene = _scene()
    entity = create_agent("甲", "来宾", "谨慎", [])

    activation = AgentScheduler().activation_for(
        entity,
        step=1,
        actor_location="住处",
        player_location="礼堂",
        proposals=[],
        is_player=False,
        has_manual_override=False,
        scene_state=scene,
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "schedule_due:ceremony"
    assert scene.get_actor_location("甲") == "住处"


def test_private_schedule_is_visible_only_to_named_participant():
    scene = _scene()
    engine = TimelineEngine()

    invited = engine.private_schedule(scene, "甲", current_step=1)
    uninvited = engine.private_schedule(scene, "乙", current_step=1)

    assert invited["active"][0]["commitment_id"] == "ceremony"
    assert invited["active"][0]["steps_until_due"] == 2
    assert uninvited == {"active": [], "due": [], "upcoming": []}


def test_host_policy_offers_schedule_move_but_keeps_other_choices():
    perception = AgentPerception(
        actor_name="甲",
        step=1,
        world_view={"location": "住处", "visible_actors": []},
        private_schedule=TimelineEngine().private_schedule(_scene(), "甲", 1),
    )

    candidates = CharacterPolicy()._environment_candidates(perception)

    schedule = [item for item in candidates if item.source == "schedule"]
    assert len(schedule) == 1
    assert schedule[0].action.kind == "move"
    assert schedule[0].action.target == "礼堂"
    assert any(item.action.kind == "observe" for item in candidates)
    assert any(item.action.kind == "wait" for item in candidates)


def test_llm_fallback_respects_schedule_without_declaring_attendance_success():
    perception = AgentPerception(
        actor_name="甲",
        step=1,
        world_view={"location": "住处"},
        private_schedule=TimelineEngine().private_schedule(_scene(), "甲", 1),
    )

    decision = LLMCharacterAgent(llm_config={})._fallback_decision(perception)

    assert decision.candidates[0].kind == "move"
    assert decision.candidates[0].target == "礼堂"
    assert "前往" in decision.candidates[0].detail
    assert any(item.kind == "wait" for item in decision.candidates)


def test_timeline_settles_attendance_from_real_locations_without_teleporting():
    scene = _scene()
    engine = TimelineEngine()
    engine.refresh(
        scene,
        {"clock": type("Clock", (), {"current_step": 3})()},
        player_name="玩家",
    )

    packet = engine.finalize(
        scene,
        {"clock": type("Clock", (), {"current_step": 3})()},
        player_name="玩家",
    )

    stored = scene.get_scene_flag("upcoming_commitments")[0]
    assert stored["status"] == "missed"
    assert stored["missing_participants"] == ["甲"]
    assert packet["last_missed_commitment"]["missing_participants"] == ["甲"]
    assert scene.get_actor_location("甲") == "住处"
