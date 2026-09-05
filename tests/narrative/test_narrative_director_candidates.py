from src.story_engine.components.narrative_director import NarrativeDirector
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.narrative_candidates import (
    PENDING_DIRECTOR_AUTHORIZATIONS_FLAG,
    drain_due_director_authorizations,
    queue_director_authorization,
)
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scene():
    return SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}},
    )


def test_normalize_candidates_drops_unknown_kind_and_non_dict_payload():
    director = NarrativeDirector()

    compiled = director._normalize_candidates(
        [
            {"kind": "unknown_kind", "payload": {"name": "x"}},
            {"kind": "character", "payload": "not-a-dict"},
            {"kind": "character", "payload": {"name": "陌生人", "location": "大厅"}},
        ]
    )

    assert compiled == [
        {"kind": "character", "reason": "", "payload": {"name": "陌生人", "location": "大厅"}}
    ]


def test_normalize_candidates_enforces_per_tick_budget():
    director = NarrativeDirector()

    compiled = director._normalize_candidates(
        [
            {"kind": "topology", "payload": {"location_id": f"地点{i}"}}
            for i in range(5)
        ]
    )

    assert len(compiled) == director.MAX_CANDIDATES_PER_TICK


def test_queue_and_drain_director_authorization_respects_window():
    scene = _scene()

    queue_director_authorization(
        scene,
        kind="topology",
        payload={"location_id": "密室", "connects_to": ["大厅"]},
        current_step=5,
    )

    assert scene.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG)[0][
        "not_before_step"
    ] == 6

    not_yet = drain_due_director_authorizations(
        scene,
        kind="topology",
        consumed_flag="consumed_topology_authorizations",
        current_step=5,
    )
    assert not_yet == []

    due = drain_due_director_authorizations(
        scene,
        kind="topology",
        consumed_flag="consumed_topology_authorizations",
        current_step=6,
    )
    assert len(due) == 1
    assert due[0]["location_id"] == "密室"
    # Still queued (not yet consumed) so a later step can also see it.
    assert len(scene.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG)) == 1


def test_drain_prunes_expired_and_consumed_authorizations():
    scene = _scene()
    queue_director_authorization(
        scene,
        kind="topology",
        payload={"location_id": "密室"},
        current_step=1,
        window=1,
    )
    scene.update_scene_flags(
        {"consumed_topology_authorizations": ["director:topology:1:0"]}
    )

    due = drain_due_director_authorizations(
        scene,
        kind="topology",
        consumed_flag="consumed_topology_authorizations",
        current_step=2,
    )

    assert due == []
    assert scene.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG) == []


def test_input_system_surfaces_due_director_authorizations_into_context():
    scene = _scene()
    queue_director_authorization(
        scene,
        kind="character",
        payload={"name": "神秘来客", "location": "大厅"},
        current_step=1,
    )
    from src.story_engine.components.drama_state import DramaState
    from src.story_engine.components.simulation_control import SimulationControl
    from src.story_engine.core.entity import Entity

    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {
        "intents": [],
        "inject_events": [],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": None,
    }

    InputSystem().update({"WorldHost": gm}, context)

    authorizations = context["character_spawn_authorizations"]
    assert len(authorizations) == 1
    assert authorizations[0]["name"] == "神秘来客"
    assert authorizations[0]["authorization_id"] == "director:character:1:0"


def test_run_narrative_director_queues_a_topology_authorization_for_next_step():
    scene = _scene()

    class FakeNarrativeDirector:
        def direct(self, _payload):
            return {
                "director_signals": [],
                "narrative_candidates": [
                    {
                        "kind": "topology",
                        "reason": "剧情需要一个隐藏房间",
                        "payload": {"location_id": "密室", "connects_to": ["大厅"]},
                    }
                ],
            }

    system = SimulationSystem()
    system._run_narrative_director(
        FakeNarrativeDirector(),
        scene_state=scene,
        result={},
        director_packet={},
        active_storylets=[],
        current_step=3,
        context={},
    )

    pending = scene.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG)
    assert len(pending) == 1
    assert pending[0]["kind"] == "topology"
    assert pending[0]["location_id"] == "密室"
    assert pending[0]["not_before_step"] == 4
