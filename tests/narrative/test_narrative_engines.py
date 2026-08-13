from copy import deepcopy

from src.story_engine.components.scene_state import SceneState
from src.story_engine.social import RelationshipBook
from src.story_engine.narrative import TimelineEngine


class Clock:
    def __init__(self, step):
        self.current_step = step


def test_timeline_engine_advances_phase_without_moving_committed_actors():
    scene = SceneState(
        world_objects={"前厅": {}, "会议室": {"default_zone": "door"}},
        actor_states={
            "玩家": {"location": "前厅"},
            "主持人": {"location": "前厅"},
        },
        scene_flags={
            "day_phase": "arrival",
            "phase_schedule": [
                {"phase": "arrival", "start_step": 0},
                {"phase": "meeting", "start_step": 2},
            ],
            "upcoming_commitments": [
                {
                    "commitment_id": "meeting_starts",
                    "title": "会议开始",
                    "summary": "主持人必须到场。",
                    "phase": "meeting",
                    "due_step": 2,
                    "location": "会议室",
                    "participants": ["主持人"],
                }
            ],
        },
    )

    packet = TimelineEngine().refresh(
        scene,
        {"clock": Clock(2)},
        player_name="玩家",
    )

    assert packet["day_phase"] == "meeting"
    assert packet["phase_turn"] == 0
    assert packet["due_commitments"][0]["commitment_id"] == "meeting_starts"
    assert scene.get_actor_location("主持人") == "前厅"
    schedule = TimelineEngine().private_schedule(scene, "主持人", 2)
    assert schedule["due"][0]["location"] == "会议室"
    assert schedule["due"][0]["attendance_is_voluntary"] is True


def test_timeline_engine_marks_missed_player_commitment_without_rewriting_history():
    commitment = {
        "commitment_id": "departure",
        "title": "启程",
        "summary": "车队按时离开。",
        "phase": "morning",
        "due_step": 3,
        "grace_steps": 0,
        "location": "城门",
        "player_relevant": True,
        "absent_consequence": "车队没有等玩家。",
    }
    scene = SceneState(
        world_objects={"旅馆": {}, "城门": {}},
        actor_states={"玩家": {"location": "旅馆"}},
        scene_flags={
            "day_phase": "morning",
            "upcoming_commitments": [deepcopy(commitment)],
        },
    )
    engine = TimelineEngine()
    engine.refresh(scene, {"clock": Clock(3)}, player_name="玩家")
    packet = engine.finalize(scene, {"clock": Clock(3)}, player_name="玩家")

    missed = packet["last_missed_commitment"]
    assert missed["commitment_id"] == "departure"
    assert missed["note"] == "车队没有等玩家。"
    stored = scene.get_scene_flag("upcoming_commitments")[0]
    assert stored["status"] == "missed"
    assert stored["summary"] == "车队按时离开。"


def test_timeline_transition_carriers_are_ranked_from_state_not_story_names():
    scene = SceneState(
        world_objects={"庭院": {}, "礼堂": {}},
        actor_states={
            "玩家": {"location": "庭院"},
            "甲": {"location": "庭院"},
            "乙": {"location": "庭院"},
            "丙": {"location": "庭院"},
        },
    )
    commitment = {
        "commitment_id": "ceremony",
        "due_step": 1,
        "location": "礼堂",
        "player_relevant": True,
        "participants": ["甲"],
    }

    relationships = RelationshipBook()
    relationships.set_track("甲", "玩家", "malice", 1)
    relationships.set_track("乙", "玩家", "malice", 3)
    relationships.set_track("丙", "玩家", "trust", -2)
    pressure = TimelineEngine().build_transition_pressure(
        scene,
        [commitment],
        current_step=1,
        player_name="玩家",
        relationship_book=relationships,
    )

    assert pressure["active"] is True
    assert pressure["carrier_actors"][0] == "甲"
    assert set(pressure["carrier_actors"]) == {"甲", "乙", "丙"}
