from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction


def _scene_with_actor():
    return SceneState(
        world_objects={"粮仓": {}},
        actor_states={"守卫甲": {"location": "粮仓"}},
    )




def test_scene_state_director_signal_queue_depth_capped_at_one_per_actor():
    scene = _scene_with_actor()

    first = scene.queue_director_signal("守卫甲", "第一条", current_step=1)
    second = scene.queue_director_signal("守卫甲", "第二条", current_step=2)

    assert first is True
    assert second is False
    popped = scene.pop_director_signals("守卫甲", current_step=3)
    assert [item["suggestion"] for item in popped] == ["第一条"]


def test_scene_state_expired_director_signal_is_dropped_silently():
    scene = _scene_with_actor()
    scene.queue_director_signal(
        "守卫甲", "过期提示", current_step=1, expires_after_steps=2
    )

    assert scene.pop_director_signals("守卫甲", current_step=10) == []
