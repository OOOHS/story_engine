from copy import deepcopy

import pytest
from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.motivation import NeedDynamics
from src.story_engine.simulation import ResourceContestResolver
from src.story_engine.systems.memory import MemorySystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return deepcopy(self.scripted_result)


def _scene(
    *,
    quantity=1,
    actors=("甲", "乙"),
    affordance=None,
    hidden=False,
):
    return SceneState(
        world_objects={
            "营地": {},
            "远处": {},
            "面包": {
                "is_location": False,
                "kind": "resource",
                "location": "营地",
                "owner": None,
                "hidden": hidden,
                "portable": True,
                "quantity": quantity,
                "affordances": [
                    affordance
                    or {
                        "id": "eat",
                        "need_effects": {"hunger": -0.5},
                        "consumes": True,
                    }
                ],
            },
        },
        actor_states={name: {"location": "营地"} for name in actors},
    )


def _drive(pressure=0.8):
    return DriveState.from_initial(
        [{"name": "hunger", "pressure": pressure}],
    )


def _use(actor, affordance_id="eat"):
    return {
        "operation": "use",
        "object_id": "面包",
        "affordance_id": affordance_id,
        "actor": actor,
        "reason": f"{actor}实际尝试使用面包",
    }


def _result(operations, actors=None):
    actors = actors or list(
        dict.fromkeys(
            operation.get("actor")
            for operation in operations
            if isinstance(operation, dict) and operation.get("actor")
        )
    )
    return {
        "resolved_actions": [
            {
                "actor": actor,
                "intent": "取得并使用目标资源",
                "outcome": "success",
                "location": "营地",
                "visibility": "public",
                "result": f"{actor}成功使用了目标资源。",
            }
            for actor in actors
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": operations,
        "drive_updates": [],
        "tension_delta": 0,
    }


def _resolved(scene, result, intents=None):
    return ResourceContestResolver().resolve(
        scene,
        result,
        intents=intents or [],
    )


def test_last_unit_has_stable_winner_and_loser_does_not_roll_back_transaction():
    winners = []
    for operations in ([_use("甲"), _use("乙")], [_use("乙"), _use("甲")]):
        scene = _scene(quantity=1)
        drives = {"甲": _drive(), "乙": _drive()}
        resolved = _resolved(scene, _result(operations))

        assert [item["actor"] for item in resolved["object_lifecycle"]] == ["乙"]
        assert resolved["resource_contests"][0]["winners"] == ["乙"]
        assert resolved["resource_contests"][0]["losers"] == ["甲"]
        assert next(
            action for action in resolved["resolved_actions"] if action["actor"] == "甲"
        )["outcome"] == "blocked"

        outcome = WorldStateTransaction().commit(
            scene,
            DramaState(),
            resolved,
            drive_states=drives,
            proposal_actors={"甲", "乙"},
        )
        assert outcome.committed is True
        assert "面包" not in scene.world_objects
        assert drives["甲"].needs["hunger"].pressure == pytest.approx(0.8)
        assert drives["乙"].needs["hunger"].pressure == pytest.approx(0.3)
        winners.append(resolved["resource_contests"][0]["winners"])

    assert winners == [["乙"], ["乙"]]


def test_manual_proposal_priority_beats_name_tiebreak_without_trusting_raw_score():
    scene = _scene(quantity=1)
    result = _result([_use("甲"), _use("乙")])
    resolved = _resolved(
        scene,
        result,
        intents=[
            {"actor": "乙", "proposal_priority": 0.9},
            {"actor": "甲", "proposal_priority": -10, "source": "manual"},
        ],
    )

    assert [item["actor"] for item in resolved["object_lifecycle"]] == ["甲"]


def test_quantity_is_a_stable_quota_independent_of_operation_and_actor_order():
    expected = ["丙", "乙"]
    for actors, operations in (
        (("丙", "乙", "甲"), [_use("丙"), _use("乙"), _use("甲")]),
        (("甲", "乙", "丙"), [_use("甲"), _use("丙"), _use("乙")]),
    ):
        scene = _scene(quantity=2, actors=actors)
        resolved = _resolved(scene, _result(operations))
        retained_actors = [item["actor"] for item in resolved["object_lifecycle"]]

        assert sorted(retained_actors) == ["丙", "乙"]
        assert resolved["resource_contests"][0]["winners"] == ["丙", "乙"]
        assert resolved["resource_contests"][0]["losers"] == ["甲"]
        # The retained list preserves each actor's model-provided sequencing;
        # winner selection itself is independent from that sequencing.
        assert sorted(retained_actors) == sorted(expected)


def test_duplicate_consuming_claims_by_one_actor_count_against_quantity():
    scene = _scene(quantity=1, actors=("甲",))
    resolved = _resolved(scene, _result([_use("甲"), _use("甲")], actors=["甲"]))

    assert len(resolved["object_lifecycle"]) == 1
    assert resolved["resource_contests"][0]["partial"] == ["甲"]
    assert resolved["resolved_actions"][0]["outcome"] == "partial"


def test_mixed_relocate_and_destroy_claims_keep_only_one_actor_claim():
    scene = _scene(quantity=1)
    result = _result(
        [
            {
                "operation": "destroy",
                "object_id": "面包",
                "actor": "乙",
                "reason": "乙试图销毁面包",
            },
            {
                "operation": "relocate",
                "object_id": "面包",
                "actor": "甲",
                "owner": "甲",
                "reason": "甲试图拿走面包",
            },
        ]
    )
    resolved = _resolved(scene, result)

    assert len(resolved["object_lifecycle"]) == 1
    assert resolved["object_lifecycle"][0]["actor"] == "乙"
    assert resolved["object_lifecycle"][0]["operation"] == "destroy"


def test_shareable_non_consuming_affordance_allows_multiple_users():
    scene = _scene(
        affordance={
            "id": "inspect",
            "need_effects": {},
            "consumes": False,
            "exclusive": False,
        }
    )
    resolved = _resolved(
        scene,
        _result([_use("甲", "inspect"), _use("乙", "inspect")]),
    )

    assert len(resolved["object_lifecycle"]) == 2
    assert resolved["resource_contests"] == []


def test_exclusive_non_consuming_affordance_allows_one_user():
    scene = _scene(
        affordance={
            "id": "operate",
            "need_effects": {},
            "consumes": False,
            "exclusive": True,
        }
    )
    resolved = _resolved(
        scene,
        _result([_use("乙", "operate"), _use("甲", "operate")]),
    )

    assert [item["actor"] for item in resolved["object_lifecycle"]] == ["乙"]
    assert resolved["resource_contests"][0]["mode"] == "exclusive_use"


def test_authoritative_transaction_rejects_exclusive_claims_that_bypass_resolver():
    scene = _scene(
        affordance={
            "id": "operate",
            "need_effects": {},
            "consumes": False,
            "exclusive": True,
        }
    )
    raw = _result([_use("甲", "operate"), _use("乙", "operate")])

    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        raw,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert any("unresolved simultaneous object contest" in error for error in outcome.errors)


def test_authoritative_transaction_allows_shareable_or_fully_supplied_direct_uses():
    shareable_scene = _scene(
        affordance={
            "id": "inspect",
            "need_effects": {},
            "consumes": False,
            "exclusive": False,
        }
    )
    supplied_scene = _scene(quantity=2)

    shareable = WorldStateTransaction().commit(
        shareable_scene,
        DramaState(),
        _result([_use("甲", "inspect"), _use("乙", "inspect")]),
        proposal_actors={"甲", "乙"},
    )
    supplied = WorldStateTransaction().commit(
        supplied_scene,
        DramaState(),
        _result([_use("甲"), _use("乙")]),
        drive_states={"甲": _drive(), "乙": _drive()},
        proposal_actors={"甲", "乙"},
    )

    assert shareable.committed is True
    assert supplied.committed is True
    assert "面包" not in supplied_scene.world_objects


def test_authoritative_transaction_rejects_conflicting_visibility_without_arbitration():
    scene = _scene(quantity=1)
    raw = _result(
        [
            {
                "operation": "set_visibility",
                "object_id": "面包",
                "actor": "甲",
                "hidden": True,
                "reason": "甲试图藏起面包",
            },
            {
                "operation": "set_visibility",
                "object_id": "面包",
                "actor": "乙",
                "hidden": False,
                "reason": "乙试图公开面包",
            },
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        raw,
        proposal_actors={"甲", "乙"},
    )

    assert outcome.committed is False
    assert scene.get_object_state("面包")["hidden"] is False


def test_conflicting_container_state_claims_have_one_stable_winner():
    scene = _scene(quantity=1)
    scene.get_object_state("面包").update(
        {
            "is_container": True,
            "container_capacity": 1,
            "container_open": False,
            "container_opaque": True,
        }
    )
    operations = [
        {
            "operation": "set_container_state",
            "object_id": "面包",
            "actor": "甲",
            "open": True,
            "reason": "甲试图打开容器",
        },
        {
            "operation": "set_container_state",
            "object_id": "面包",
            "actor": "乙",
            "open": False,
            "reason": "乙试图同时关住容器",
        },
    ]

    resolved = _resolved(scene, _result(operations))
    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        resolved,
        proposal_actors={"甲", "乙"},
    )

    assert [item["actor"] for item in resolved["object_lifecycle"]] == ["乙"]
    assert resolved["resource_contests"][0]["mode"] == "exclusive_claim"
    assert outcome.committed is True
    assert scene.get_object_state("面包")["container_open"] is False


def test_same_container_state_claim_is_shareable_and_authoritatively_valid():
    scene = _scene(quantity=1)
    scene.get_object_state("面包").update(
        {
            "is_container": True,
            "container_capacity": 1,
            "container_open": False,
            "container_opaque": True,
        }
    )
    operations = [
        {
            "operation": "set_container_state",
            "object_id": "面包",
            "actor": actor,
            "open": True,
            "reason": f"{actor}共同打开容器",
        }
        for actor in ("甲", "乙")
    ]

    resolved = _resolved(scene, _result(operations))
    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        resolved,
        proposal_actors={"甲", "乙"},
    )

    assert len(resolved["object_lifecycle"]) == 2
    assert resolved["resource_contests"] == []
    assert outcome.committed is True
    assert scene.get_object_state("面包")["container_open"] is True


def test_missing_capability_rejects_use_and_rolls_back_need_effect():
    scene = _scene(
        actors=("甲",),
        affordance={
            "id": "operate",
            "need_effects": {"hunger": -0.5},
            "consumes": False,
            "requires_capabilities": ["radio_operation"],
        },
    )
    drive = _drive()
    result = _result([_use("甲", "operate")], actors=["甲"])

    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        result,
        drive_states={"甲": drive},
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert drive.needs["hunger"].pressure == pytest.approx(0.8)
    assert any("lacks required capabilities" in error for error in outcome.errors)


def test_capability_must_exist_before_the_turn_not_be_granted_by_same_result():
    scene = _scene(
        actors=("甲",),
        affordance={
            "id": "operate",
            "need_effects": {},
            "consumes": False,
            "requires_capabilities": ["radio_operation"],
        },
    )
    result = _result([_use("甲", "operate")], actors=["甲"])
    result["state_updates"]["actor_states"] = {
        "甲": {"capabilities": ["radio_operation"]}
    }

    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        result,
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert scene.get_actor_state("甲").get("capabilities") is None
    assert any("lacks required capabilities" in error for error in outcome.errors)


def test_owner_requirement_is_authoritative_and_opportunity_explains_unavailability():
    scene = _scene(
        actors=("甲", "乙"),
        affordance={
            "id": "unlock",
            "need_effects": {"hunger": -0.2},
            "consumes": False,
            "requires_owner": True,
            "requires_capabilities": ["lockwork"],
        },
    )
    scene.get_object_state("面包").update({"owner": "乙", "location": None})
    scene.get_actor_state("甲")["capabilities"] = []
    scene.get_actor_state("乙")["capabilities"] = ["lockwork"]

    unavailable = NeedDynamics().build_opportunities(scene, "甲", _drive())[0]
    available = NeedDynamics().build_opportunities(scene, "乙", _drive())[0]

    assert unavailable["available"] is False
    assert unavailable["requires_owner"] is True
    assert unavailable["missing_capabilities"] == ["lockwork"]
    assert available["available"] is True
    assert available["missing_capabilities"] == []

    result = _result([_use("甲", "unlock")], actors=["甲"])
    outcome = WorldStateTransaction().commit(
        scene,
        DramaState(),
        result,
        drive_states={"甲": _drive()},
        proposal_actors={"甲"},
    )
    assert outcome.committed is False
    assert any("requires actor to own object" in error for error in outcome.errors)


def test_contest_trace_is_removed_from_player_rendering_even_when_object_is_visible():
    scene = _scene(quantity=1)
    resolved = _resolved(scene, _result([_use("甲"), _use("乙")]))

    visible = RenderingSystem()._build_visible_simulation(
        resolved,
        scene.get_view_pov("乙"),
        visible_locations=["营地"],
    )

    assert resolved["resource_contests"]
    assert visible["resource_contests"] == []


class Memory(Component):
    records: list = Field(default_factory=list)

    def add_memory(self, content, metadata=None):
        self.records.append({"content": content, "metadata": metadata or {}})


def test_gm_and_personal_memory_exclude_host_contest_trace():
    scene = _scene(quantity=1)
    resolved = _resolved(scene, _result([_use("甲"), _use("乙")]))
    gm = Entity("GameMaster")
    gm.add_component(Memory())
    actor = Entity("甲")
    actor.add_component(Memory())
    context = {
        "intents": [],
        "simulation_result": resolved,
        "visible_simulation_result": RenderingSystem()._build_visible_simulation(
            resolved,
            scene.get_view_pov("甲"),
            visible_locations=["营地"],
        ),
        "rendered_text": "本轮结束。",
        "timeline": {},
        "player_pov": scene.get_view_pov("甲"),
        "visible_actor_names": [],
        "clock": type("Clock", (), {"current_step": 1})(),
    }

    MemorySystem().update({"GameMaster": gm, "甲": actor}, context)

    gm_text = gm.get_component("Memory").records[0]["content"]
    actor_text = actor.get_component("Memory").records[0]["content"]
    assert "Resource Contests:" not in gm_text
    assert "同时发生的资源竞争使该行动未能完成" in gm_text
    assert "Resource Contests:" not in actor_text
    assert actor.get_component("Memory").records[0]["metadata"]["salience"] >= 1.5


def test_hidden_offscreen_contest_trace_cannot_enter_visible_simulation():
    scene = _scene(quantity=1, actors=("甲", "乙", "玩家"), hidden=True)
    scene.get_actor_state("玩家")["location"] = "远处"
    resolved = _resolved(scene, _result([_use("甲"), _use("乙")]))

    visible = RenderingSystem()._build_visible_simulation(
        resolved,
        scene.get_view_pov("玩家"),
        visible_locations=["远处"],
    )

    assert visible["resolved_actions"] == []
    assert visible["object_lifecycle"] == []
    assert visible["resource_contests"] == []


def test_resolver_does_not_mutate_raw_model_result():
    scene = _scene(quantity=1)
    raw = _result([_use("甲"), _use("乙")])
    before = deepcopy(raw)

    _resolved(scene, raw)

    assert raw == before


def test_resource_contest_trace_is_engine_owned_and_cleared_on_rejection():
    scene = _scene(quantity=1, actors=("甲",))
    raw = _result([_use("甲")], actors=["甲"])
    raw["resource_contests"] = [{"object_id": "伪造对象", "winners": ["甲"]}]

    resolved = _resolved(scene, raw)
    rejected = WorldStateTransaction().sanitize_rejected_result(
        {**resolved, "resource_contests": [{"object_id": "面包"}]},
        ["forced rejection"],
    )

    assert resolved["resource_contests"] == []
    assert rejected["resource_contests"] == []


def test_simulation_system_resolves_contest_before_authoritative_transaction():
    scene = _scene(quantity=1)
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(scripted_result=_result([_use("甲"), _use("乙")]))
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    actors = {}
    for name in ("甲", "乙"):
        entity = Entity(name)
        entity.add_component(_drive())
        actors[name] = entity
    entities = {"GameMaster": gm, **actors}
    context = {
        "intents": [
            {"actor": "甲", "intent": "吃面包"},
            {"actor": "乙", "intent": "吃面包"},
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["simulation_result"]["resource_contests"]
    assert "面包" not in scene.world_objects
    assert actors["甲"].get_component("DriveState").needs["hunger"].pressure == pytest.approx(0.8)
    assert actors["乙"].get_component("DriveState").needs["hunger"].pressure == pytest.approx(0.3)


def test_host_materializes_selected_affordance_when_semantic_gm_omits_operation():
    scene = _scene(quantity=1, actors=("甲",))
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(scripted_result=_result([], actors=["甲"]))
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    actor = Entity("甲")
    actor.add_component(_drive())
    entities = {"GameMaster": gm, "甲": actor}
    context = {
        "intents": [
            {
                "actor": "甲",
                "intent": "吃掉面包",
                "action_kind": "interact",
                "action_target": "面包",
                "action_affordance_id": "eat",
            }
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["affordance_action_traces"] == [
        {
            "actor": "甲",
            "object_id": "面包",
            "affordance_id": "eat",
            "status": "host_use_materialized",
        }
    ]
    assert context["simulation_result"]["object_lifecycle"][0][
        "affordance_id"
    ] == "eat"
    assert "面包" not in scene.world_objects
    assert actor.get_component("DriveState").needs["hunger"].pressure == pytest.approx(
        0.3
    )
