from src.story_engine.components.relationship import RelationshipBit
from src.story_engine.core.entity import Entity
from src.story_engine.social import SocialRelationRegistry
from src.story_engine.systems.relationships import RelationshipSystem


def test_relationship_system_decays_tracks_and_expires_timed_bits():
    registry = SocialRelationRegistry()
    book = registry.to_relationship_book()
    record = book.ensure("甲", "乙")
    book.set_track(
        "甲",
        "乙",
        "resentment",
        3.0,
        decay_per_step=0.5,
        updated_step=1,
    )
    record.bits["recent_conflict"] = RelationshipBit(
        bit_id="recent_conflict",
        created_step=1,
        expires_step=2,
    )
    entities = {}
    registry.apply_relationship_book(book, entities)
    context = {
        "relation_registry": registry,
        "clock": type("Clock", (), {"current_step": 3})(),
    }

    RelationshipSystem().update(entities, context)

    advanced = registry.to_relationship_book()
    assert advanced.get_metrics("甲", "乙")["resentment"] == 2.0
    assert "recent_conflict" not in advanced.relationships["pair:乙<->甲"].bits
    assert len(context["relationship_transitions"]) == 2


def test_relationship_system_does_not_schedule_pair_entity_as_agent():
    registry = SocialRelationRegistry()
    book = registry.to_relationship_book()
    book.ensure("甲", "乙")
    entities = {"甲": Entity("甲"), "乙": Entity("乙")}
    registry.apply_relationship_book(book, entities)

    pair = entities["Relationship:乙<->甲"]
    assert pair.get_component("AgentController") is None
