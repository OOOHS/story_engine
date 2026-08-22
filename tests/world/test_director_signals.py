from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction


def _scene_with_actor():
    return SceneState(
        world_objects={"粮仓": {}},
        actor_states={"守卫甲": {"location": "粮仓"}},
    )


def test_scene_state_queue_and_pop_director_signal_happy_path():
    scene = _scene_with_actor()

    queued = scene.queue_director_signal(
        "守卫甲",
        "  你注意到锁有些松动  ",
        current_step=10,
        source_plot_id="southern_drought",
        tags=["hint"],
    )
    popped = scene.pop_director_signals("守卫甲", current_step=11)

    assert queued is True
    assert len(popped) == 1
    assert popped[0]["suggestion"] == "你注意到锁有些松动"
    assert popped[0]["source_plot_id"] == "southern_drought"
    # Popping clears it -- a signal is delivered at most once.
    assert scene.pop_director_signals("守卫甲", current_step=12) == []


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


def test_world_transaction_commit_no_longer_queues_director_signals():
    """director_signals are narrative intuition now, produced by
    NarrativeDirector strictly after a commit has already succeeded (see
    SimulationSystem._run_narrative_director), never staged inside the
    atomic settlement transaction itself. A ``director_signals`` entry on
    the pre-commit result is accepted for call-site compatibility and
    silently ignored here.
    """
    scene = _scene_with_actor()
    plots = PlotState.from_configs([])
    result = {
        "state_updates": {},
        "director_signals": [
            {
                "actor": "守卫甲",
                "suggestion": "你注意到锁有些松动",
                "source_plot_id": "southern_drought",
                "tags": ["hint"],
            },
        ],
    }

    outcome = WorldStateTransaction().commit(
        scene, plots, None, result, current_step=7
    )

    assert outcome.committed is True
    assert scene.pop_director_signals("守卫甲", current_step=8) == []
