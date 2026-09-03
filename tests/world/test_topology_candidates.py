from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.topology_candidates import (
    TopologyCandidateAuthority,
    TopologyCandidateLifecycle,
)
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scene():
    return SceneState(
        world_objects={
            "大厅": {"connected_to": ["书房"]},
            "书房": {"connected_to": ["大厅"]},
        },
        actor_states={"甲": {"location": "大厅"}},
    )


def _authorization(**updates):
    value = {
        "authorization_id": "director:secret_passage",
        "location_id": "密室",
        "connects_to": ["书房"],
        "visibility": "hidden",
        "reason": "剧情需要一个隐藏的房间",
        "not_before_step": 2,
        "expires_step": 2,
    }
    value.update(updates)
    return value


def test_authority_rejects_candidate_without_matching_authorization():
    resolved = TopologyCandidateAuthority().resolve(
        {"authorization_id": "invented"},
        authorizations=[],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.request is None
    assert resolved.rejected == ["topology:unknown_authorization:invented"]


def test_authority_compiles_canonical_payload_from_authorization():
    resolved = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:secret_passage"},
        authorizations=[_authorization()],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.rejected == []
    assert resolved.request["location_id"] == "密室"
    assert resolved.request["connects_to"] == ["书房"]
    assert resolved.request["visibility"] == "hidden"


def test_lifecycle_rejects_connection_to_unknown_location():
    scene = _scene()
    resolved = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:secret_passage"},
        authorizations=[_authorization(connects_to=["不存在的地点"])],
        scene_state=scene,
        current_step=2,
    )

    preparation = TopologyCandidateLifecycle().prepare(scene, resolved.request)

    assert preparation.plan is None
    assert any("unknown location" in error for error in preparation.errors)


def test_lifecycle_rejects_duplicate_location_id():
    scene = _scene()
    resolved = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:secret_passage"},
        authorizations=[_authorization(location_id="大厅")],
        scene_state=scene,
        current_step=2,
    )

    preparation = TopologyCandidateLifecycle().prepare(scene, resolved.request)

    assert preparation.plan is None
    assert any("existing object" in error for error in preparation.errors)


def test_lifecycle_prepare_and_stage_grows_the_graph_with_reciprocal_edges():
    scene = _scene()
    resolved = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:secret_passage"},
        authorizations=[_authorization()],
        scene_state=scene,
        current_step=2,
    )

    lifecycle = TopologyCandidateLifecycle()
    preparation = lifecycle.prepare(scene, resolved.request)
    assert preparation.errors == []

    errors = lifecycle.stage(scene, preparation.plan)

    assert errors == []
    assert "密室" in scene.get_known_locations()
    assert scene.get_object_state("密室")["connected_to"] == ["书房"]
    assert "密室" in scene.get_object_state("书房")["connected_to"]
    assert scene.get_scene_flag("dynamic_location_names") == ["密室"]
    assert scene.get_scene_flag("consumed_topology_authorizations") == [
        "director:secret_passage"
    ]


def test_lifecycle_enforces_max_dynamic_locations_cap():
    scene = _scene()
    scene.update_scene_flags({"max_dynamic_locations": 1})
    lifecycle = TopologyCandidateLifecycle()

    resolved_first = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:first"},
        authorizations=[
            _authorization(authorization_id="director:first", location_id="密室")
        ],
        scene_state=scene,
        current_step=2,
    )
    plan_first = lifecycle.prepare(scene, resolved_first.request).plan
    lifecycle.stage(scene, plan_first)

    resolved_second = TopologyCandidateAuthority().resolve(
        {"authorization_id": "director:second"},
        authorizations=[
            _authorization(authorization_id="director:second", location_id="地窖")
        ],
        scene_state=scene,
        current_step=2,
    )
    preparation_second = lifecycle.prepare(scene, resolved_second.request)

    assert preparation_second.plan is None
    assert any(
        "max_dynamic_locations" in error for error in preparation_second.errors
    )


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        from copy import deepcopy

        return deepcopy(self.scripted_result)


def test_runner_pipeline_authorizes_and_commits_a_new_location():
    """End-to-end: injected authorization -> GM cites it -> atomic commit grows
    the graph, mirroring the character/storylet_definition integration tests
    for the topology candidate kind."""
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "World",
                        "intent": "叙事导演建议开放一条密道。",
                        "action_kind": "interact",
                        "outcome": "success",
                        "location": "书房",
                        "visibility": "hidden",
                        "result": "书架后的密室通道被发现了。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "topology_candidate": {
                    "authorization_id": "director:secret_passage",
                },
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm}
    context = {
        "intents": [],
        "inject_events": [
            {
                "event_id": "director:secret_passage",
                "intent": "叙事导演建议开放一条密道。",
                "topology_candidate": _authorization(),
            }
        ],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": None,
    }

    InputSystem().update(entities, context)
    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["topology_candidate_rejections"] == []
    assert "密室" in scene.get_known_locations()
    assert "密室" in scene.get_object_state("书房")["connected_to"]
    audit = scene.get_scene_flag("narrative_candidate_audit")
    assert audit[-1]["kind"] == "topology"
    assert audit[-1]["accepted"] is True


def test_rejected_candidate_leaves_graph_untouched():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "World",
                        "intent": "GM 试图凭空声明一个新地点。",
                        "action_kind": "interact",
                        "outcome": "success",
                        "location": "书房",
                        "visibility": "hidden",
                        "result": "一段没有授权的叙述。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "topology_candidate": {
                    "authorization_id": "invented",
                    "location_id": "凭空出现的密室",
                },
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm}
    context = {
        "intents": [],
        "inject_events": [],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": None,
    }

    InputSystem().update(entities, context)
    SimulationSystem().update(entities, context)

    assert "凭空出现的密室" not in scene.get_known_locations()
    assert context["topology_candidate_rejections"] == [
        "topology:unknown_authorization:invented"
    ]
