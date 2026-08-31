from copy import deepcopy
from typing import Any, Dict, Iterable, List
from uuid import NAMESPACE_URL, uuid5

from src.story_engine.components.claim import ClaimConditions, ClaimEvidence, ClaimFact
from src.story_engine.core.entity import Entity


class ClaimRegistry:
    """Session-owned registry of objective Claim Entities."""

    def __init__(self) -> None:
        self._entities: Dict[str, Entity] = {}

    def get(self, claim_id: Any) -> Entity | None:
        return self._entities.get(str(claim_id or "").strip())

    def entities(self) -> Iterable[Entity]:
        return tuple(self._entities.values())

    def binding_snapshot(self) -> Dict[str, str]:
        return {
            claim_id: entity.name for claim_id, entity in self._entities.items()
        }

    def restore_bindings(
        self,
        snapshot: Dict[str, str],
        world_entities: Dict[str, Entity],
    ) -> None:
        self._entities = {
            claim_id: world_entities[entity_name]
            for claim_id, entity_name in snapshot.items()
            if entity_name in world_entities
        }

    def seed(
        self,
        configs: Iterable[Any],
        *,
        scene_state: Any,
        world_entities: Dict[str, Entity],
    ) -> None:
        known_subjects = set(scene_state.actor_states).union(scene_state.world_objects)
        for raw in configs or []:
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            claim_id = self._text(raw.get("claim_id"), 120)
            statement = self._text(raw.get("statement"), 800)
            if not claim_id or not statement:
                raise ValueError("claim requires claim_id and statement")
            if claim_id in self._entities:
                raise ValueError(f"duplicate claim id: {claim_id}")
            subjects = self._text_list(raw.get("subjects", []), 8, 120)
            unknown_subjects = set(subjects).difference(known_subjects)
            if unknown_subjects:
                raise ValueError(
                    f"claim {claim_id} references unknown subjects: "
                    f"{sorted(unknown_subjects)}"
                )
            supports = self._text_list(raw.get("supporting_evidence", []), 16, 120)
            refutes = self._text_list(raw.get("refuting_evidence", []), 16, 120)
            unknown_evidence = set(supports + refutes).difference(
                scene_state.world_objects
            )
            if unknown_evidence:
                raise ValueError(
                    f"claim {claim_id} references unknown evidence: "
                    f"{sorted(unknown_evidence)}"
                )
            entity_name = f"Claim:{claim_id}"
            if entity_name in world_entities:
                raise ValueError(f"claim entity name collision: {entity_name}")
            entity = Entity(
                entity_name,
                entity_id=str(uuid5(NAMESPACE_URL, f"story-engine:claim:{claim_id}")),
            )
            entity.add_component(
                ClaimFact(
                    claim_id=claim_id,
                    statement=statement,
                    truth_status=raw.get("initial_truth", "unknown"),
                    visibility=raw.get("visibility", "secret"),
                    subjects=subjects,
                    tags=self._text_list(raw.get("tags", []), 16, 60),
                )
            )
            entity.add_component(
                ClaimConditions(
                    truth_conditions=self._conditions(raw.get("truth_conditions", [])),
                    false_conditions=self._conditions(raw.get("false_conditions", [])),
                )
            )
            entity.add_component(
                ClaimEvidence(supports=supports, refutes=refutes)
            )
            self._entities[claim_id] = entity
            world_entities[entity_name] = entity

    def advance_to(
        self,
        *,
        step: int,
        scene_state: Any,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        transitions = []
        errors = []
        for entity in self.entities():
            fact = entity.get_component("ClaimFact")
            conditions = entity.get_component("ClaimConditions")
            if fact is None or conditions is None:
                continue
            try:
                is_true = bool(conditions.truth_conditions) and all(
                    scene_state.matches_condition(condition)
                    for condition in conditions.truth_conditions
                )
                is_false = bool(conditions.false_conditions) and all(
                    scene_state.matches_condition(condition)
                    for condition in conditions.false_conditions
                )
            except (TypeError, ValueError) as exc:
                errors.append(
                    f"claim condition evaluation failed:{fact.claim_id}:"
                    f"{type(exc).__name__}:{exc}"
                )
                continue
            if is_true and is_false:
                errors.append(
                    f"claim truth and false conditions are simultaneously true: "
                    f"{fact.claim_id}"
                )
                continue
            has_dynamic_conditions = bool(
                conditions.truth_conditions or conditions.false_conditions
            )
            next_status = (
                "true"
                if is_true
                else "false"
                if is_false
                else "unknown"
                if has_dynamic_conditions
                else ""
            )
            if not next_status or next_status == fact.truth_status:
                continue
            before = fact.truth_status
            fact.truth_status = next_status
            fact.updated_step = int(step)
            transitions.append(
                {
                    "claim_id": fact.claim_id,
                    "before": before,
                    "after": next_status,
                    "step": int(step),
                }
            )
        return transitions, errors

    def gm_catalog(self) -> List[Dict[str, Any]]:
        catalog = []
        for entity in self.entities():
            fact = entity.get_component("ClaimFact")
            evidence = entity.get_component("ClaimEvidence")
            catalog.append(
                {
                    "claim_id": fact.claim_id,
                    "statement": fact.statement,
                    "truth_status": fact.truth_status,
                    "visibility": fact.visibility,
                    "subjects": list(fact.subjects),
                    "supporting_evidence": list(evidence.supports),
                    "refuting_evidence": list(evidence.refutes),
                }
            )
        return catalog

    def private_snapshot(
        self,
        *,
        actor: str,
        knowledge_state: Any,
        scene_state: Any,
    ) -> Dict[str, Any]:
        claims = []
        leverage = []
        for record in knowledge_state.claims.values() if knowledge_state else []:
            entity = self.get(record.claim_id)
            if entity is None:
                continue
            fact = entity.get_component("ClaimFact")
            evidence = entity.get_component("ClaimEvidence")
            item = {
                "claim_id": fact.claim_id,
                "statement": fact.statement,
                "stance": record.stance,
                "confidence": record.confidence,
                "basis": record.basis,
                "source": record.source,
                "learned_step": record.learned_step,
                "updated_step": record.updated_step,
                "evidence_refs": list(record.evidence_refs),
                "subjects": list(fact.subjects),
                "public": fact.visibility == "public",
            }
            claims.append(item)
            owned_support = [
                ref
                for ref in evidence.supports
                if str(scene_state.get_object_state(ref).get("owner") or "") == actor
                and ref in record.evidence_refs
            ]
            targets = [subject for subject in fact.subjects if subject != actor]
            if record.stance == "supports" and targets:
                leverage.append(
                    {
                        "claim_id": fact.claim_id,
                        "targets": targets,
                        "confidence": record.confidence,
                        "owned_supporting_evidence": owned_support,
                        "evidence_backed": bool(owned_support),
                    }
                )
        claims.sort(key=lambda item: (-item["confidence"], item["claim_id"]))
        leverage.sort(
            key=lambda item: (
                not item["evidence_backed"],
                -item["confidence"],
                item["claim_id"],
            )
        )
        map_snapshot = (
            knowledge_state.get_map_snapshot()
            if knowledge_state and hasattr(knowledge_state, "get_map_snapshot")
            else {"known_locations": [], "known_routes": {}}
        )
        return {
            "claims": claims,
            "potential_leverage": leverage,
            "map": map_snapshot,
        }

    @staticmethod
    def _conditions(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            deepcopy(item.model_dump() if hasattr(item, "model_dump") else item)
            for item in value[:24]
            if isinstance(item, dict) or hasattr(item, "model_dump")
        ]

    @classmethod
    def _text_list(cls, value: Any, limit: int, item_limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return [
            text
            for raw in value[:limit]
            if (text := cls._text(raw, item_limit))
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
