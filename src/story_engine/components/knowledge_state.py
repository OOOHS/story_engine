from copy import deepcopy
from typing import Any, Dict, Iterable, Literal

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class ClaimKnowledgeRecord(BaseModel):
    claim_id: str
    stance: Literal["supports", "rejects", "uncertain"] = "uncertain"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: Literal["reported", "observed", "inferred", "public"] = "reported"
    source: str = ""
    learned_step: int = 0
    updated_step: int = 0
    evidence_refs: list[str] = Field(default_factory=list)


class KnowledgeState(Component):
    """One character's private positions on objective Claim Entities."""

    claims: Dict[str, ClaimKnowledgeRecord] = Field(default_factory=dict)
    max_claims: int = Field(default=128, ge=0, le=512)
    known_locations: list[str] = Field(default_factory=list)
    known_routes: Dict[str, list[str]] = Field(default_factory=dict)
    route_provenance: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def from_initial(cls, items: Iterable[Any] = ()) -> "KnowledgeState":
        state = cls()
        for raw in items or []:
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            claim_id = cls._text(raw.get("claim_id"), 120)
            if not claim_id:
                continue
            state.learn(
                claim_id=claim_id,
                stance=raw.get("stance", "uncertain"),
                confidence=raw.get("confidence", 0.5),
                basis=raw.get("basis", "reported"),
                source=cls._text(raw.get("source"), 120),
                step=int(raw.get("learned_step", 0) or 0),
                evidence_refs=raw.get("evidence_refs", []),
            )
        return state

    def learn(
        self,
        *,
        claim_id: str,
        stance: str,
        confidence: float,
        basis: str,
        source: str,
        step: int,
        evidence_refs: Iterable[Any] = (),
    ) -> ClaimKnowledgeRecord:
        claim_key = self._text(claim_id, 120)
        if not claim_key:
            raise ValueError("claim_id is required")
        if stance not in {"supports", "rejects", "uncertain"}:
            raise ValueError(f"invalid claim stance: {stance}")
        if basis not in {"reported", "observed", "inferred", "public"}:
            raise ValueError(f"invalid claim basis: {basis}")
        bounded_confidence = min(1.0, max(0.0, float(confidence)))
        refs = []
        for raw in evidence_refs or []:
            ref = self._text(raw, 120)
            if ref and ref not in refs:
                refs.append(ref)
        record = self.claims.get(claim_key)
        if record is None:
            if len(self.claims) >= self.max_claims:
                oldest = min(
                    self.claims.values(),
                    key=lambda item: (item.updated_step, item.claim_id),
                )
                self.claims.pop(oldest.claim_id, None)
            record = ClaimKnowledgeRecord(
                claim_id=claim_key,
                stance=stance,
                confidence=bounded_confidence,
                basis=basis,
                source=self._text(source, 120),
                learned_step=int(step),
                updated_step=int(step),
                evidence_refs=refs,
            )
            self.claims[claim_key] = record
            return record
        # New evidence can strengthen a position, but a contradictory report
        # remains explicit instead of silently overwriting a high-confidence view.
        next_stance = record.stance
        next_confidence = record.confidence
        if stance == record.stance:
            next_confidence = max(record.confidence, bounded_confidence)
        elif bounded_confidence > record.confidence:
            next_stance = stance
            next_confidence = bounded_confidence
        else:
            next_stance = "uncertain"
            next_confidence = max(
                0.1, abs(record.confidence - bounded_confidence)
            )
        next_source = self._text(source, 120)
        next_refs = list(dict.fromkeys(record.evidence_refs + refs))[:32]
        if (
            next_stance == record.stance
            and next_confidence == record.confidence
            and basis == record.basis
            and next_source == record.source
            and next_refs == record.evidence_refs
        ):
            return record
        record.stance = next_stance
        record.confidence = next_confidence
        record.basis = basis
        record.source = next_source
        record.updated_step = int(step)
        record.evidence_refs = next_refs
        return record

    def knows(self, claim_id: Any) -> bool:
        return self._text(claim_id, 120) in self.claims

    def observe_location(self, scene_state: Any, location: Any) -> None:
        location_id = self._text(location, 120)
        if not location_id or location_id not in scene_state.get_known_locations():
            return
        if location_id not in self.known_locations:
            self.known_locations.append(location_id)
        neighbors = []
        for raw in scene_state.get_object_state(location_id).get("connected_to", []) or []:
            neighbor = self._text(raw, 120)
            if neighbor and neighbor in scene_state.get_known_locations():
                neighbors.append(neighbor)
                if neighbor not in self.known_locations:
                    self.known_locations.append(neighbor)
        self.known_locations = sorted(set(self.known_locations))[:256]
        # Seeing today's exits adds evidence; a missing exit does not by itself
        # prove that every remembered or reported road has ceased to exist.
        self.known_routes[location_id] = sorted(
            set(self.known_routes.get(location_id, []) + neighbors)
        )[:32]
        for neighbor in neighbors:
            self.route_provenance[f"{location_id}->{neighbor}"] = {
                "basis": "observed",
                "source": "self",
            }

    def learn_reported_route(
        self,
        source: str,
        target: str,
        *,
        reporter: str,
        step: int,
    ) -> None:
        source_id = self._text(source, 120)
        target_id = self._text(target, 120)
        if not source_id or not target_id or source_id == target_id:
            return
        self.known_locations = sorted(
            set(self.known_locations + [source_id, target_id])
        )[:256]
        self.known_routes[source_id] = sorted(
            set(self.known_routes.get(source_id, []) + [target_id])
        )[:32]
        self.route_provenance[f"{source_id}->{target_id}"] = {
            "basis": "reported",
            "source": self._text(reporter, 120),
            "learned_step": int(step),
        }

    def get_map_snapshot(self) -> Dict[str, Any]:
        known = set(self.known_locations)
        return {
            "known_locations": sorted(known),
            "known_routes": {
                source: [target for target in targets if target in known]
                for source, targets in sorted(self.known_routes.items())
                if source in known
            },
            "route_provenance": deepcopy(self.route_provenance),
        }

    def restore_from(self, snapshot: "KnowledgeState") -> None:
        self.claims = deepcopy(snapshot.claims)
        self.max_claims = snapshot.max_claims
        self.known_locations = list(snapshot.known_locations)
        self.known_routes = deepcopy(snapshot.known_routes)
        self.route_provenance = deepcopy(snapshot.route_provenance)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
