from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from src.story_engine.components.relationship import (
    RelationshipBit,
    RelationshipBits,
    RelationshipTimeline,
    RelationshipTrack,
    RelationshipTracks,
)
from src.story_engine.components.social_relation import SocialRelation
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity


DEFAULT_TRACK_BOUNDS = {
    "favor": (0.0, 5.0),
    "malice": (0.0, 5.0),
    "trust": (-5.0, 5.0),
}

DEFAULT_TRACK_POLICY_WEIGHTS = {
    "favor": {"social": 0.45, "aid": 0.75, "confront": -0.4, "deception": -0.2},
    "malice": {"social": -0.25, "aid": -0.8, "confront": 0.85, "deception": 0.25},
    "trust": {"social": 0.55, "aid": 0.45, "confront": -0.2, "deception": -0.7},
}


class PairRelationshipRecord(BaseModel):
    participants: List[str] = Field(min_length=2, max_length=2)
    directed_tracks: Dict[str, Dict[str, RelationshipTrack]] = Field(default_factory=dict)
    bits: Dict[str, RelationshipBit] = Field(default_factory=dict)
    first_met_step: int = 0
    last_interaction_step: int = 0
    important_event_refs: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class RelationshipBook(BaseModel):
    """Transaction view of Sims-style pair relationship aggregates."""

    relationships: Dict[str, PairRelationshipRecord] = Field(default_factory=dict)

    @staticmethod
    def relation_id(first: Any, second: Any) -> str:
        participants = sorted(
            {str(first or "").strip(), str(second or "").strip()}
        )
        if len(participants) != 2 or not all(participants):
            raise ValueError("pair relationship requires two distinct participants")
        return f"pair:{participants[0]}<->{participants[1]}"

    @staticmethod
    def direction_key(source: Any, target: Any) -> str:
        return f"{str(source or '').strip()}->{str(target or '').strip()}"

    def ensure(
        self,
        first: Any,
        second: Any,
        *,
        created_step: int = 0,
        provenance: Optional[Dict[str, Any]] = None,
        policy_weights: Optional[Dict[str, float]] = None,
    ) -> PairRelationshipRecord:
        relation_id = self.relation_id(first, second)
        participants = relation_id.removeprefix("pair:").split("<->", 1)
        record = self.relationships.get(relation_id)
        if record is None:
            record = PairRelationshipRecord(
                participants=participants,
                first_met_step=int(created_step),
                last_interaction_step=int(created_step),
                provenance=deepcopy(provenance or {}),
            )
            self.relationships[relation_id] = record
        return record

    def get_metrics(self, source: Any, target: Any) -> Dict[str, float]:
        try:
            record = self.relationships.get(self.relation_id(source, target))
        except ValueError:
            return {}
        if record is None:
            return {}
        tracks = record.directed_tracks.get(self.direction_key(source, target), {})
        return {track_id: track.value for track_id, track in tracks.items()}

    def describe_direction(self, source: Any, target: Any) -> List[str]:
        """Return host-derived social categories without exposing track values."""
        metrics = self.get_metrics(source, target)
        trust = float(metrics.get("trust", 0.0) or 0.0)
        favor = float(metrics.get("favor", 0.0) or 0.0)
        malice = float(metrics.get("malice", 0.0) or 0.0)
        states = []
        hostile = malice >= 3.0 or trust <= -3.0
        wary = not hostile and (malice >= 1.0 or trust < 0.0)
        if hostile:
            states.append("hostile")
        elif wary:
            states.append("wary")
        elif malice < 1.0 and trust >= 0.0:
            states.append("non_hostile")
        if trust >= 2.0:
            states.append("trusted")
        if favor >= 1.0 and malice < 1.0:
            states.append("friendly")
        if favor >= 3.0 and trust >= 1.0 and malice < 1.0:
            states.append("close")
        return states or ["neutral"]

    def set_track(
        self,
        source: Any,
        target: Any,
        track_id: str,
        value: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        decay_per_step: float = 0.0,
        updated_step: int = 0,
        provenance: Optional[Dict[str, Any]] = None,
        policy_weights: Optional[Dict[str, float]] = None,
    ) -> RelationshipTrack:
        record = self.ensure(source, target, created_step=updated_step)
        track_key = str(track_id).strip()
        default_min, default_max = DEFAULT_TRACK_BOUNDS.get(track_key, (-5.0, 5.0))
        lower = default_min if minimum is None else float(minimum)
        upper = default_max if maximum is None else float(maximum)
        if lower > upper:
            raise ValueError(f"invalid relationship track bounds: {track_key}")
        track = RelationshipTrack(
            value=min(upper, max(lower, float(value))),
            minimum=lower,
            maximum=upper,
            decay_per_step=float(decay_per_step),
            updated_step=int(updated_step),
            provenance=deepcopy(provenance or {}),
            policy_weights=deepcopy(
                policy_weights
                if policy_weights is not None
                else DEFAULT_TRACK_POLICY_WEIGHTS.get(track_key, {})
            ),
        )
        record.directed_tracks.setdefault(
            self.direction_key(source, target), {}
        )[track_key] = track
        return track

    def apply_delta(
        self,
        source: Any,
        target: Any,
        *,
        current_step: int = 0,
        reason: str = "",
        provenance: Optional[Dict[str, Any]] = None,
        **deltas: Any,
    ) -> Dict[str, float]:
        record = self.ensure(source, target, created_step=current_step)
        direction = record.directed_tracks.setdefault(
            self.direction_key(source, target), {}
        )
        for key, raw in deltas.items():
            if not key.endswith("_delta") or raw is None:
                continue
            track_id = key.removesuffix("_delta")
            current = direction.get(track_id)
            if current is None:
                lower, upper = DEFAULT_TRACK_BOUNDS.get(track_id, (-5.0, 5.0))
                current = RelationshipTrack(
                    minimum=lower,
                    maximum=upper,
                    policy_weights=deepcopy(
                        DEFAULT_TRACK_POLICY_WEIGHTS.get(track_id, {})
                    ),
                )
                direction[track_id] = current
            current.value = min(
                current.maximum,
                max(current.minimum, current.value + float(raw)),
            )
            current.updated_step = int(current_step)
            if reason or provenance:
                current.provenance = deepcopy(provenance or {})
                if reason:
                    current.provenance["reason"] = reason
        record.last_interaction_step = max(record.last_interaction_step, int(current_step))
        return self.get_metrics(source, target)

    def get_visible_relations(
        self,
        viewer: Any,
        visible_actors: Iterable[str],
        actor_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        viewer_key = str(viewer or "").strip()
        results = []
        for actor in visible_actors or []:
            actor_key = str(actor or "").strip()
            if not actor_key or actor_key == viewer_key:
                continue
            state = (actor_states or {}).get(actor_key, {})
            record = self.relationships.get(self.relation_id(actor_key, viewer_key))
            results.append(
                {
                    "actor": actor_key,
                    "bias": state.get("bias") if isinstance(state, dict) else None,
                    "framing_style": state.get("framing_style") if isinstance(state, dict) else None,
                    "territorial": bool(state.get("territorial")) if isinstance(state, dict) else False,
                    "toward_viewer_states": self.describe_direction(
                        actor_key, viewer_key
                    ),
                    "viewer_toward_actor_states": self.describe_direction(
                        viewer_key, actor_key
                    ),
                    "relationship_bits": sorted(
                        bit_id
                        for bit_id, bit in (record.bits.items() if record else [])
                        if bit.visibility != "hidden"
                    ),
                    "relationship_id": (
                        self.relation_id(actor_key, viewer_key) if record else None
                    ),
                }
            )
        return results

    def get_track_records(
        self, source: Any, target: Any
    ) -> Dict[str, RelationshipTrack]:
        try:
            record = self.relationships.get(self.relation_id(source, target))
        except ValueError:
            return {}
        if record is None:
            return {}
        return record.directed_tracks.get(self.direction_key(source, target), {})

    def restore_from(self, snapshot: "RelationshipBook") -> None:
        self.relationships = deepcopy(snapshot.relationships)

    def advance_to(self, step: int) -> List[Dict[str, Any]]:
        transitions: List[Dict[str, Any]] = []
        target_step = int(step)
        for relation_id, record in self.relationships.items():
            for direction, tracks in record.directed_tracks.items():
                for track_id, track in tracks.items():
                    elapsed = target_step - int(track.updated_step)
                    if elapsed <= 0 or track.decay_per_step == 0:
                        continue
                    before = track.value
                    amount = abs(track.decay_per_step) * elapsed
                    if before > 0:
                        track.value = max(0.0, before - amount)
                    elif before < 0:
                        track.value = min(0.0, before + amount)
                    track.updated_step = target_step
                    if track.value != before:
                        transitions.append(
                            {
                                "relationship_id": relation_id,
                                "direction": direction,
                                "track_id": track_id,
                                "before": before,
                                "after": track.value,
                                "reason": "track_decay",
                            }
                        )
            expired = [
                bit_id
                for bit_id, bit in record.bits.items()
                if bit.expires_step is not None and target_step > bit.expires_step
            ]
            for bit_id in expired:
                record.bits.pop(bit_id, None)
                transitions.append(
                    {
                        "relationship_id": relation_id,
                        "bit_id": bit_id,
                        "status": "expired",
                    }
                )
        return transitions


@dataclass(frozen=True)
class RelationRegistrySnapshot:
    entities: tuple[tuple[str, str, str, tuple[tuple[type[Component], Dict[str, Any]], ...]], ...]


class SocialRelationRegistry:
    """Sparse registry for pair/group contexts and their child social entities."""

    def __init__(self) -> None:
        self._entities: Dict[str, Entity] = {}

    def get(self, relation_id: str) -> Entity | None:
        return self._entities.get(str(relation_id))

    def entities(self, relation_kind: str | None = None) -> Iterable[Entity]:
        values = tuple(self._entities.values())
        if relation_kind is None:
            return values
        return tuple(
            entity
            for entity in values
            if (
                relation := entity.get_component("SocialRelation")
            ) is not None
            and relation.relation_kind == relation_kind
        )

    def register(self, entity: Entity) -> None:
        relation = entity.get_component("SocialRelation")
        if relation is None:
            raise ValueError(f"relation entity lacks SocialRelation: {entity.name}")
        current = self._entities.get(relation.relation_id)
        if current is not None and current is not entity:
            raise ValueError(f"duplicate relation id: {relation.relation_id}")
        self._entities[relation.relation_id] = entity

    def binding_snapshot(self) -> Dict[str, str]:
        return {
            relation_id: entity.name
            for relation_id, entity in self._entities.items()
        }

    def restore_bindings(
        self,
        snapshot: Dict[str, str],
        world_entities: Dict[str, Entity],
    ) -> None:
        self._entities = {
            relation_id: world_entities[entity_name]
            for relation_id, entity_name in snapshot.items()
            if entity_name in world_entities
        }

    def remove(self, relation_id: str, world_entities: Dict[str, Entity] | None = None) -> None:
        entity = self._entities.pop(str(relation_id), None)
        if entity is not None and world_entities is not None:
            world_entities.pop(entity.name, None)

    def ensure_context(
        self,
        participants: Iterable[str],
        *,
        world_entities: Dict[str, Entity] | None = None,
        created_step: int = 0,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Entity:
        names = sorted({str(item).strip() for item in participants if str(item).strip()})
        if len(names) < 2:
            raise ValueError("social context requires at least two participants")
        if len(names) == 2:
            relation_id = RelationshipBook.relation_id(names[0], names[1])
            relation_kind = "pair"
            entity_name = f"Relationship:{names[0]}<->{names[1]}"
        else:
            joined = "+".join(names)
            relation_id = f"group:{joined}"
            relation_kind = "group"
            entity_name = f"RelationshipGroup:{joined}"
        entity = self._entities.get(relation_id)
        if entity is None:
            entity = Entity(
                entity_name,
                entity_id=str(uuid5(NAMESPACE_URL, f"story-engine:relation:{relation_id}")),
            )
            entity.add_component(
                SocialRelation(
                    relation_id=relation_id,
                    relation_kind=relation_kind,
                    participants=names,
                    participant_roles={
                        f"participant_{index}": participant
                        for index, participant in enumerate(names)
                    },
                    visibility="participants",
                    created_step=int(created_step),
                    provenance=deepcopy(provenance or {}),
                )
            )
            entity.add_component(RelationshipBits())
            entity.add_component(
                RelationshipTimeline(
                    first_met_step=int(created_step),
                    last_interaction_step=int(created_step),
                )
            )
            if relation_kind == "pair":
                entity.add_component(RelationshipTracks())
            self.register(entity)
        if world_entities is not None:
            collision = world_entities.get(entity.name)
            if collision is not None and collision is not entity:
                raise ValueError(f"relation entity name collision: {entity.name}")
            world_entities[entity.name] = entity
        return entity

    def to_relationship_book(self) -> RelationshipBook:
        records: Dict[str, PairRelationshipRecord] = {}
        for entity in self.entities("pair"):
            relation = entity.get_component("SocialRelation")
            tracks = entity.get_component("RelationshipTracks")
            bits = entity.get_component("RelationshipBits")
            timeline = entity.get_component("RelationshipTimeline")
            if not tracks or not bits or not timeline:
                raise ValueError(f"malformed pair relationship: {entity.name}")
            records[relation.relation_id] = PairRelationshipRecord(
                participants=list(relation.participants),
                directed_tracks=deepcopy(tracks.directed),
                bits=deepcopy(bits.bits),
                first_met_step=timeline.first_met_step,
                last_interaction_step=timeline.last_interaction_step,
                important_event_refs=deepcopy(timeline.important_event_refs),
                provenance=deepcopy(relation.provenance),
            )
        return RelationshipBook(relationships=records)

    def apply_relationship_book(
        self,
        book: RelationshipBook,
        world_entities: Dict[str, Entity] | None = None,
    ) -> None:
        incoming = set(book.relationships)
        for entity in tuple(self.entities("pair")):
            relation = entity.get_component("SocialRelation")
            if relation.relation_id not in incoming:
                self.remove(relation.relation_id, world_entities)
        for relation_id, record in book.relationships.items():
            canonical = RelationshipBook.relation_id(*record.participants)
            if relation_id != canonical:
                raise ValueError(f"pair relationship id mismatch: {relation_id}")
            entity = self.ensure_context(
                record.participants,
                world_entities=world_entities,
                created_step=record.first_met_step,
                provenance=record.provenance,
            )
            entity.get_component("RelationshipTracks").directed = deepcopy(
                record.directed_tracks
            )
            entity.get_component("RelationshipBits").bits = deepcopy(record.bits)
            timeline = entity.get_component("RelationshipTimeline")
            timeline.first_met_step = record.first_met_step
            timeline.last_interaction_step = record.last_interaction_step
            timeline.important_event_refs = deepcopy(record.important_event_refs)

    def snapshot(self) -> RelationRegistrySnapshot:
        rows = []
        for relation_id, entity in sorted(self._entities.items()):
            components = tuple(
                (component.__class__, deepcopy(component.model_dump()))
                for component in entity.components.values()
            )
            rows.append((relation_id, entity.name, entity.id, components))
        return RelationRegistrySnapshot(tuple(rows))

    def restore(
        self,
        snapshot: RelationRegistrySnapshot,
        world_entities: Dict[str, Entity] | None = None,
    ) -> None:
        if world_entities is not None:
            for entity in self._entities.values():
                world_entities.pop(entity.name, None)
        self._entities = {}
        for _, entity_name, entity_id, component_rows in snapshot.entities:
            entity = Entity(name=entity_name, entity_id=entity_id)
            for component_type, payload in component_rows:
                entity.add_component(component_type(**deepcopy(payload)))
            self.register(entity)
            if world_entities is not None:
                world_entities[entity_name] = entity
