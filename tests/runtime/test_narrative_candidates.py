from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.narrative_candidates import (
    CANDIDATE_AUDIT_FLAG,
    CandidateLedger,
    NarrativeCandidateAuthority,
    record_candidate_audit,
)


def _scene():
    return SceneState(
        world_objects={"酒馆": {}},
        actor_states={"玩家": {"location": "酒馆"}},
    )


def _authorization(**updates):
    value = {
        "authorization_id": "arrival:messenger",
        "not_before_step": 2,
        "expires_step": 2,
    }
    value.update(updates)
    return value


def test_resolve_authorization_rejects_missing_and_unknown_ids():
    authority = NarrativeCandidateAuthority()
    scene = _scene()

    missing = authority.resolve_authorization(
        {"name": "刺客"},
        domain="spawn_character",
        authorizations=[],
        scene_state=scene,
        consumed_flag="consumed_x",
        current_step=2,
    )
    assert missing.authorization is None
    assert missing.rejected == ["spawn_character:missing_authorization_id"]

    unknown = authority.resolve_authorization(
        {"authorization_id": "invented"},
        domain="spawn_character",
        authorizations=[],
        scene_state=scene,
        consumed_flag="consumed_x",
        current_step=2,
    )
    assert unknown.rejected == ["spawn_character:unknown_authorization:invented"]


def test_resolve_authorization_rejects_ambiguous_consumed_and_out_of_window():
    authority = NarrativeCandidateAuthority()
    scene = _scene()
    authorizations = [_authorization(), _authorization()]

    ambiguous = authority.resolve_authorization(
        {"authorization_id": "arrival:messenger"},
        domain="topology",
        authorizations=authorizations,
        scene_state=scene,
        consumed_flag="consumed_topology",
        current_step=2,
    )
    assert ambiguous.rejected == [
        "topology:ambiguous_authorization:arrival:messenger"
    ]

    scene.update_scene_flags({"consumed_topology": ["arrival:messenger"]})
    consumed = authority.resolve_authorization(
        {"authorization_id": "arrival:messenger"},
        domain="topology",
        authorizations=[_authorization()],
        scene_state=scene,
        consumed_flag="consumed_topology",
        current_step=2,
    )
    assert consumed.rejected == [
        "topology:consumed_authorization:arrival:messenger"
    ]

    fresh_scene = _scene()
    out_of_window = authority.resolve_authorization(
        {"authorization_id": "arrival:messenger"},
        domain="topology",
        authorizations=[_authorization(not_before_step=5, expires_step=6)],
        scene_state=fresh_scene,
        consumed_flag="consumed_topology",
        current_step=2,
    )
    assert out_of_window.rejected == [
        "topology:authorization_out_of_window:arrival:messenger"
    ]


def test_resolve_authorization_accepts_valid_window():
    authority = NarrativeCandidateAuthority()
    scene = _scene()

    resolved = authority.resolve_authorization(
        {"authorization_id": "arrival:messenger"},
        domain="topology",
        authorizations=[_authorization()],
        scene_state=scene,
        consumed_flag="consumed_topology",
        current_step=2,
    )
    assert resolved.rejected == []
    assert resolved.authorization["authorization_id"] == "arrival:messenger"


def test_candidate_ledger_caps_dedupes_and_consumes():
    scene = _scene()

    assert CandidateLedger.check_cap(
        scene, names_flag="dynamic_x", cap_flag="max_dynamic_x", default_cap=1
    ) is None
    CandidateLedger.append_name(scene, "dynamic_x", "甲")
    assert CandidateLedger.normalized_names(scene, "dynamic_x") == ["甲"]

    over_cap = CandidateLedger.check_cap(
        scene, names_flag="dynamic_x", cap_flag="max_dynamic_x", default_cap=1
    )
    assert over_cap == "exceeds max_dynamic_x"

    CandidateLedger.append_name(scene, "dynamic_x", "甲")
    assert CandidateLedger.normalized_names(scene, "dynamic_x") == ["甲"]

    CandidateLedger.consume_authorization(scene, "consumed_x", "auth-1")
    CandidateLedger.consume_authorization(scene, "consumed_x", "auth-1")
    assert scene.get_scene_flag("consumed_x") == ["auth-1"]


def test_candidate_audit_records_and_bounds_entries():
    scene = _scene()

    record_candidate_audit(
        scene,
        kind="object",
        source="gm",
        accepted=True,
        candidate_id="铜钥匙",
        step=4,
    )
    record_candidate_audit(
        scene,
        kind="character",
        source="injected",
        accepted=False,
        reason="spawn_character:missing_authorization_id",
        step=4,
    )

    entries = scene.get_scene_flag(CANDIDATE_AUDIT_FLAG)
    assert [item["kind"] for item in entries] == ["object", "character"]
    assert entries[0]["accepted"] is True
    assert entries[1]["reason"] == "spawn_character:missing_authorization_id"

    for index in range(250):
        record_candidate_audit(
            scene, kind="object", source="gm", accepted=True, step=index
        )
    bounded = scene.get_scene_flag(CANDIDATE_AUDIT_FLAG)
    assert len(bounded) == 200
    assert bounded[-1]["step"] == 249
