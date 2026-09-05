from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.scenarios.config import (
    ScenarioConfig,
    StateCondition,
)
from src.story_engine.systems.memory import MemorySystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scene():
    coin_state = {
        "is_location": False,
        "kind": "currency",
        "hidden": False,
        "portable": True,
        "stack_key": "currency:copper",
        "affordances": [],
    }
    return SceneState(
        world_objects={
            "集市": {},
            "远处": {},
            "甲的信": {
                "is_location": False,
                "kind": "letter",
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "乙的钥匙": {
                "is_location": False,
                "kind": "key",
                "owner": "乙",
                "location": None,
                "hidden": False,
                "portable": True,
            },
            "甲的铜币": {
                **coin_state,
                "owner": "甲",
                "location": None,
                "quantity": 5,
            },
            "乙的铜币": {
                **coin_state,
                "owner": "乙",
                "location": None,
                "quantity": 3,
            },
            "隐藏戒指": {
                "is_location": False,
                "kind": "ring",
                "owner": "甲",
                "location": None,
                "hidden": True,
                "portable": True,
            },
            "石像": {
                "is_location": False,
                "kind": "statue",
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": False,
            },
        },
        actor_states={
            "甲": {"location": "集市"},
            "乙": {"location": "集市"},
            "丙": {"location": "集市"},
        },
    )


def _action(actor, result="同意并完成交换"):
    return {
        "actor": actor,
        "outcome": "success",
        "location": "集市",
        "visibility": "public",
        "result": result,
    }


def _exchange(exchange_id, parties, transfers, accepted_by=None):
    return {
        "exchange_id": exchange_id,
        "parties": list(parties),
        "accepted_by": list(accepted_by or parties),
        "transfers": transfers,
        "reason": "双方当面确认并交付交换物",
    }


def _result(exchanges, actions=None):
    return {
        "resolved_actions": actions or [_action("甲"), _action("乙")],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": exchanges,
        "drive_updates": [],
        "tension_delta": 0,
    }


def _commit(scene, result, proposal_actors=None, **kwargs):
    return WorldStateTransaction().commit(
        scene,
        kwargs.pop("drama_state", DramaState()),
        result,
        proposal_actors=set(proposal_actors or {"甲", "乙"}),
        **kwargs,
    )


def test_whole_objects_can_be_bartered_atomically():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "letter_for_key",
                ["甲", "乙"],
                [
                    {"from": "甲", "to": "乙", "object_id": "甲的信"},
                    {"from": "乙", "to": "甲", "object_id": "乙的钥匙"},
                ],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is True
    assert scene.get_object_state("甲的信")["owner"] == "乙"
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"


def test_whole_container_transfer_moves_nested_contents_without_rewriting_them():
    scene = _scene()
    scene.world_objects.update(
        {
            "甲的皮箱": {
                "is_location": False,
                "kind": "case",
                "owner": "甲",
                "location": None,
                "container": None,
                "hidden": False,
                "portable": True,
                "is_container": True,
                "container_capacity": 4,
                "container_open": False,
                "container_opaque": True,
            },
            "箱中账本": {
                "is_location": False,
                "kind": "ledger",
                "owner": None,
                "location": None,
                "container": "甲的皮箱",
                "hidden": False,
                "portable": True,
            },
        }
    )
    result = _result(
        [
            _exchange(
                "closed_case_sale",
                ["甲", "乙"],
                [{"from": "甲", "to": "乙", "object_id": "甲的皮箱"}],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is True
    assert scene.get_object_state("甲的皮箱")["owner"] == "乙"
    assert scene.get_object_state("箱中账本")["container"] == "甲的皮箱"
    assert scene.get_effective_object_location("箱中账本") == "集市"
    assert "箱中账本" not in scene.get_view_pov("乙")["visible_objects"]

    move = _result([], actions=[_action("乙", "乙带着皮箱离开")])
    move["state_updates"]["actor_states"] = {"乙": {"location": "远处"}}
    moved = _commit(scene, move, proposal_actors={"乙"})
    assert moved.committed is True
    assert scene.get_effective_object_location("箱中账本") == "远处"


def test_contained_object_must_be_removed_before_direct_exchange():
    scene = _scene()
    scene.world_objects.update(
        {
            "甲的袋子": {
                "is_location": False,
                "kind": "bag",
                "owner": "甲",
                "location": None,
                "container": None,
                "hidden": False,
                "portable": True,
                "is_container": True,
                "container_capacity": 2,
                "container_open": True,
                "container_opaque": True,
            },
            "袋中硬币": {
                "is_location": False,
                "kind": "coin",
                "owner": None,
                "location": None,
                "container": "甲的袋子",
                "hidden": False,
                "portable": True,
            },
        }
    )
    before = deepcopy(scene.get_snapshot())
    result = _result(
        [
            _exchange(
                "invalid_nested_transfer",
                ["甲", "乙"],
                [{"from": "甲", "to": "乙", "object_id": "袋中硬币"}],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is False
    assert any("source does not own object" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_partial_fungible_transfer_merges_into_compatible_recipient_stack():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "pay_two_coins",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 2,
                    }
                ],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is True
    assert scene.get_object_state("甲的铜币")["quantity"] == 3
    assert scene.get_object_state("乙的铜币")["quantity"] == 5
    assert scene.get_scene_flag("dynamic_world_object_names", []) == []


def test_partial_transfer_creates_deterministic_fragment_when_no_stack_exists():
    scene = _scene()
    scene.world_objects.pop("乙的铜币")
    result = _result(
        [
            _exchange(
                "first_payment",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 2,
                    }
                ],
            )
        ]
    )

    outcome = _commit(scene, result)
    fragments = [
        object_id
        for object_id, state in scene.world_objects.items()
        if not scene.is_location(object_id)
        and state.get("owner") == "乙"
        and state.get("stack_key") == "currency:copper"
    ]

    assert outcome.committed is True
    assert scene.get_object_state("甲的铜币")["quantity"] == 3
    assert len(fragments) == 1
    assert scene.get_object_state(fragments[0])["quantity"] == 2
    assert scene.get_scene_flag("dynamic_world_object_names") == fragments


def test_split_fragment_is_order_independent_across_multiple_exchange_records():
    variants = []
    exchanges = [
        _exchange(
            "part_a",
            ["甲", "乙"],
            [
                {
                    "from": "甲",
                    "to": "乙",
                    "object_id": "甲的铜币",
                    "quantity": 1,
                }
            ],
        ),
        _exchange(
            "part_b",
            ["甲", "乙"],
            [
                {
                    "from": "甲",
                    "to": "乙",
                    "object_id": "甲的铜币",
                    "quantity": 1,
                }
            ],
        ),
    ]
    for ordered in (exchanges, list(reversed(exchanges))):
        scene = _scene()
        scene.world_objects.pop("乙的铜币")
        outcome = _commit(scene, _result(ordered))
        assert outcome.committed is True
        variants.append(scene.get_snapshot())

    assert variants[0] == variants[1]


def test_split_respects_dynamic_world_object_limit():
    scene = _scene()
    scene.world_objects.pop("乙的铜币")
    scene.update_scene_flags({"max_dynamic_world_objects": 0})
    result = _result(
        [
            _exchange(
                "no_fragment_capacity",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 1,
                    }
                ],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is False
    assert scene.get_object_state("甲的铜币")["quantity"] == 5
    assert any("exceeds max_dynamic_world_objects" in error for error in outcome.errors)


def test_full_stack_transfer_preserves_object_identity_without_fragment():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "all_coins",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 5,
                    }
                ],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is True
    assert scene.get_object_state("甲的铜币")["owner"] == "乙"
    assert scene.get_object_state("甲的铜币")["quantity"] == 5
    assert scene.get_scene_flag("dynamic_world_object_names", []) == []


def test_quantity_shortage_rejects_exchange_and_other_world_changes():
    scene = _scene()
    before = deepcopy(scene.get_snapshot())
    drama = DramaState(tension=0.3)
    result = _result(
        [
            _exchange(
                "overspend",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 6,
                    }
                ],
            )
        ]
    )
    result["state_updates"]["scene"]["description"] = "不应提交"
    result["tension_delta"] = 0.2

    outcome = _commit(scene, result, drama_state=drama)

    assert outcome.committed is False
    assert scene.get_snapshot() == before
    assert drama.tension == 0.3
    assert any("exceeds available units" in error for error in outcome.errors)


def test_partial_transfer_requires_content_defined_stack_key():
    scene = _scene()
    scene.get_object_state("甲的铜币").pop("stack_key")
    result = _result(
        [
            _exchange(
                "invalid_split",
                ["甲", "乙"],
                [
                    {
                        "from": "甲",
                        "to": "乙",
                        "object_id": "甲的铜币",
                        "quantity": 2,
                    }
                ],
            )
        ]
    )

    outcome = _commit(scene, result)

    assert outcome.committed is False
    assert scene.get_object_state("甲的铜币")["quantity"] == 5
    assert any("requires stack_key" in error for error in outcome.errors)


def test_hidden_or_nonportable_objects_cannot_be_smuggled_through_exchange():
    for object_id, expected_error in (
        ("隐藏戒指", "must be disclosed"),
        ("石像", "non-portable"),
    ):
        scene = _scene()
        result = _result(
            [
                _exchange(
                    f"bad_{object_id}",
                    ["甲", "乙"],
                    [{"from": "甲", "to": "乙", "object_id": object_id}],
                )
            ]
        )

        outcome = _commit(scene, result)

        assert outcome.committed is False
        assert scene.get_object_state(object_id)["owner"] == "甲"
        assert any(expected_error in error for error in outcome.errors)


def test_exchange_requires_real_proposals_acceptance_and_colocation():
    scene = _scene()
    base_exchange = _exchange(
        "consent_check",
        ["甲", "乙"],
        [{"from": "甲", "to": "乙", "object_id": "甲的信"}],
    )
    no_proposal = _commit(
        scene,
        _result([base_exchange]),
        proposal_actors={"甲"},
    )

    scene = _scene()
    bad_acceptance = {**base_exchange, "accepted_by": ["甲"]}
    not_accepted = _commit(scene, _result([bad_acceptance]))

    scene = _scene()
    scene.actor_states["乙"]["location"] = "远处"
    remote_actions = [_action("甲"), {**_action("乙"), "location": "远处"}]
    remote = _commit(scene, _result([base_exchange], actions=remote_actions))

    assert no_proposal.committed is False
    assert not_accepted.committed is False
    assert remote.committed is False
    assert any("proposal from 乙" in error for error in no_proposal.errors)
    assert any("accepted_by" in error for error in not_accepted.errors)
    assert any("co-located" in error for error in remote.errors)


def test_exchange_object_cannot_also_be_used_or_relocated_this_turn():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "double_path",
                ["甲", "乙"],
                [{"from": "甲", "to": "乙", "object_id": "甲的信"}],
            )
        ]
    )
    result["object_lifecycle"] = [
        {
            "operation": "relocate",
            "object_id": "甲的信",
            "actor": "甲",
            "owner": "乙",
            "reason": "重复转交路径",
        }
    ]

    outcome = _commit(scene, result)

    assert outcome.committed is False
    assert scene.get_object_state("甲的信")["owner"] == "甲"
    assert any("also appear in object_lifecycle" in error for error in outcome.errors)


def test_aggregate_claims_prevent_double_spending_across_exchanges():
    scene = _scene()
    exchanges = [
        _exchange(
            "payment_one",
            ["甲", "乙"],
            [
                {
                    "from": "甲",
                    "to": "乙",
                    "object_id": "甲的铜币",
                    "quantity": 3,
                }
            ],
        ),
        _exchange(
            "payment_two",
            ["甲", "乙"],
            [
                {
                    "from": "甲",
                    "to": "乙",
                    "object_id": "甲的铜币",
                    "quantity": 3,
                }
            ],
        ),
    ]

    outcome = _commit(scene, _result(exchanges))

    assert outcome.committed is False
    assert scene.get_object_state("甲的铜币")["quantity"] == 5
    assert any("exceeds available units" in error for error in outcome.errors)


def test_exchange_result_is_independent_of_exchange_and_transfer_order():
    snapshots = []
    first = _exchange(
        "barter",
        ["甲", "乙"],
        [
            {"from": "甲", "to": "乙", "object_id": "甲的信"},
            {"from": "乙", "to": "甲", "object_id": "乙的钥匙"},
        ],
    )
    second = _exchange(
        "payment",
        ["甲", "乙"],
        [
            {
                "from": "甲",
                "to": "乙",
                "object_id": "甲的铜币",
                "quantity": 2,
            }
        ],
    )
    variants = [
        [first, second],
        [second, {**first, "transfers": list(reversed(first["transfers"]))}],
    ]
    for exchanges in variants:
        scene = _scene()
        outcome = _commit(scene, _result(exchanges))
        assert outcome.committed is True
        snapshots.append(scene.get_snapshot())

    assert snapshots[0] == snapshots[1]




def test_stack_key_cannot_be_forged_through_state_updates():
    scene = _scene()
    scene.get_object_state("甲的信").pop("stack_key", None)
    result = _result([])
    result["state_updates"]["world_objects"] = {
        "甲的信": {"stack_key": "currency:forged"}
    }

    outcome = _commit(scene, result)

    assert outcome.committed is False
    assert scene.get_object_state("甲的信").get("stack_key") is None
    assert any("require object_lifecycle" in error for error in outcome.errors)


def test_rendering_hides_exchange_bundle_and_gm_memory_archives_it():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "private_terms",
                ["甲", "乙"],
                [{"from": "乙", "to": "甲", "object_id": "乙的钥匙"}],
            )
        ]
    )
    visible = RenderingSystem()._build_visible_simulation(
        result,
        scene.get_view_pov("甲"),
        visible_locations=["集市"],
    )

    assert visible["exchanges"] == []

    gm = Entity("WorldHost")
    gm.add_component(Memory())
    actor = Entity("甲")
    actor.add_component(Memory())
    context = {
        "intents": [],
        "simulation_result": result,
        "visible_simulation_result": visible,
        "rendered_text": "双方完成了当面交付。",
        "timeline": {},
        "player_pov": scene.get_view_pov("甲"),
        "visible_actor_names": [],
        "clock": type("Clock", (), {"current_step": 1})(),
    }
    MemorySystem().update({"WorldHost": gm, "甲": actor}, context)

    gm_text = gm.get_component("Memory").records[0]["content"]
    actor_text = actor.get_component("Memory").records[0]["content"]
    assert "Exchanges:" in gm_text
    assert "private_terms" in gm_text
    assert "Exchanges:" not in actor_text


class Memory(Component):
    records: list = Field(default_factory=list)

    def add_memory(self, content, metadata=None):
        self.records.append({"content": content, "metadata": metadata or {}})


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return self.scripted_result


def test_simulation_system_commits_exchange_using_current_agent_proposals():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "runtime_trade",
                ["甲", "乙"],
                [{"from": "乙", "to": "甲", "object_id": "乙的钥匙"}],
            )
        ]
    )
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(DramaState())
    alice = Entity("甲")
    bob = Entity("乙")
    context = {
        "intents": [
            {"actor": "甲", "intent": "接受钥匙"},
            {"actor": "乙", "intent": "交出钥匙"},
        ]
    }

    SimulationSystem().update(
        {"WorldHost": gm, "甲": alice, "乙": bob},
        context,
    )

    assert context["state_transaction"]["committed"] is True
    assert scene.get_object_state("乙的钥匙")["owner"] == "甲"


def test_failed_runtime_exchange_is_sanitized_before_rendering():
    scene = _scene()
    result = _result(
        [
            _exchange(
                "invented_acceptance",
                ["甲", "乙"],
                [{"from": "乙", "to": "甲", "object_id": "乙的钥匙"}],
            )
        ]
    )
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {"intents": [{"actor": "甲", "intent": "单方面声称交易"}]}

    SimulationSystem().update(
        {"WorldHost": gm, "甲": Entity("甲"), "乙": Entity("乙")},
        context,
    )

    assert context["state_transaction"]["committed"] is False
    assert context["simulation_result"]["resolved_actions"] == []
    assert context["simulation_result"]["exchanges"] == []
    assert scene.get_object_state("乙的钥匙")["owner"] == "乙"
