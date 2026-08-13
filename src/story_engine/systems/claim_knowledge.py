from copy import deepcopy
from typing import Any, Dict, List

from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.common.observation_window import shares_action_location
from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class ClaimKnowledgeSystem(System):
    """Apply evidence discoveries and claim communication to private knowledge."""

    MAX_DISCOVERIES = 12
    MAX_TRANSFERS = 12

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        live_states = {
            name: state
            for name, entity in entities.items()
            if (state := entity.get_component("KnowledgeState")) is not None
        }
        transaction = context.get("state_transaction", {})
        result = context.get("simulation_result", {})
        if not transaction.get("committed"):
            context["claim_knowledge_updates"] = []
            context["claim_knowledge_errors"] = []
            return
        discoveries = result.get("claim_discoveries", [])
        transfers = [
            item
            for item in result.get("knowledge_updates", []) or []
            if isinstance(item, dict) and str(item.get("claim_id", "")).strip()
        ]
        if not discoveries and not transfers:
            context["claim_knowledge_updates"] = []
            context["claim_knowledge_errors"] = []
            return
        staged = {
            name: KnowledgeState(**deepcopy(state.model_dump()))
            for name, state in live_states.items()
        }
        clock = context.get("clock")
        applied, errors = self._apply(
            states=staged,
            scene_state=self._component(entities, "SceneState"),
            claim_registry=context.get("claim_registry"),
            relation_registry=context.get("relation_registry"),
            actions=[
                item for item in result.get("resolved_actions", [])
                if isinstance(item, dict)
            ],
            discoveries=discoveries,
            transfers=transfers,
            step=clock.current_step if clock else 0,
            observation_windows=context.get("actor_observation_windows", {}),
        )
        if not errors:
            snapshots = {
                name: KnowledgeState(**deepcopy(state.model_dump()))
                for name, state in live_states.items()
            }
            try:
                for name, state in staged.items():
                    live_states[name].restore_from(state)
            except Exception as exc:
                for name, snapshot in snapshots.items():
                    live_states[name].restore_from(snapshot)
                applied = []
                errors = [
                    f"claim knowledge publication failed:{type(exc).__name__}:{exc}"
                ]
        context["claim_knowledge_updates"] = applied if not errors else []
        context["claim_knowledge_errors"] = errors

    def _apply(
        self,
        *,
        states: Dict[str, KnowledgeState],
        scene_state: Any,
        claim_registry: Any,
        relation_registry: Any,
        actions: List[Dict[str, Any]],
        discoveries: Any,
        transfers: List[Dict[str, Any]],
        step: int,
        observation_windows: Any = None,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if claim_registry is None:
            return [], ["claim knowledge updates require ClaimRegistry"]
        if not isinstance(discoveries, list):
            return [], ["claim_discoveries must be a list"]
        if len(discoveries) > self.MAX_DISCOVERIES:
            return [], [
                f"claim_discoveries cannot exceed {self.MAX_DISCOVERIES} per turn"
            ]
        if len(transfers) > self.MAX_TRANSFERS:
            return [], [
                f"claim knowledge transfers cannot exceed {self.MAX_TRANSFERS} per turn"
            ]
        applied: List[Dict[str, Any]] = []
        errors: List[str] = []
        for index, discovery in enumerate(discoveries):
            prefix = f"claim_discoveries[{index}]"
            if not isinstance(discovery, dict):
                errors.append(f"{prefix} must be an object")
                continue
            forbidden = set(discovery).intersection(
                {"truth", "truth_status", "stance", "confidence"}
            )
            if forbidden:
                errors.append(
                    f"{prefix} contains host-owned fields: {sorted(forbidden)}"
                )
            actor = self._text(discovery.get("actor"), 120)
            claim_id = self._text(discovery.get("claim_id"), 120)
            evidence_ref = self._text(discovery.get("evidence_ref"), 120)
            reason = self._text(discovery.get("reason"), 500)
            claim = claim_registry.get(claim_id)
            if actor not in states:
                errors.append(f"{prefix} actor has no KnowledgeState: {actor}")
            if claim is None:
                errors.append(f"{prefix} references unknown claim: {claim_id}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            stance = ""
            if claim is not None:
                evidence = claim.get_component("ClaimEvidence")
                if evidence_ref in evidence.supports:
                    stance = "supports"
                elif evidence_ref in evidence.refutes:
                    stance = "rejects"
                else:
                    errors.append(
                        f"{prefix} evidence is not linked to claim: {evidence_ref}"
                    )
            observable = (
                evidence_ref in scene_state.get_visible_objects(actor)
                if scene_state and actor
                else False
            )
            if not observable:
                errors.append(f"{prefix} evidence is not visible to actor: {evidence_ref}")
            action = next(
                (
                    item
                    for item in actions
                    if str(item.get("actor", "")).strip() == actor
                    and str(item.get("action_kind", "")) == "observe"
                    and str(item.get("outcome", ""))
                    in {"success", "partial", "complication"}
                    and str(item.get("private_result", "")).strip()
                ),
                None,
            )
            if action is None:
                errors.append(f"{prefix} lacks a resolved active observation")
            if any(error.startswith(prefix) for error in errors):
                continue
            record = states[actor].learn(
                claim_id=claim_id,
                stance=stance,
                confidence=0.9,
                basis="observed",
                source=f"evidence:{evidence_ref}",
                step=step,
                evidence_refs=[evidence_ref],
            )
            applied.append(
                {
                    "operation": "discover",
                    "actor": actor,
                    "claim_id": claim_id,
                    "stance": record.stance,
                    "confidence": record.confidence,
                    "evidence_ref": evidence_ref,
                    "reason": reason,
                }
            )

        relationship_book = (
            relation_registry.to_relationship_book() if relation_registry else None
        )
        for index, transfer in enumerate(transfers):
            prefix = f"knowledge_updates.claim[{index}]"
            forbidden = set(transfer).intersection(
                {"truth", "truth_status", "confidence", "evidence_refs"}
            )
            if forbidden:
                errors.append(
                    f"{prefix} contains host-owned fields: {sorted(forbidden)}"
                )
            source = self._text(transfer.get("source"), 120)
            target = self._text(transfer.get("target"), 120)
            claim_id = self._text(transfer.get("claim_id"), 120)
            asserted_stance = self._text(
                transfer.get("asserted_stance"), 20
            ) or ""
            reason = self._text(transfer.get("reason"), 500)
            cited_evidence = self._text_list(
                transfer.get("cited_evidence", []), 8, 120
            )
            claim = claim_registry.get(claim_id)
            if source not in states or target not in states or source == target:
                errors.append(f"{prefix} requires two distinct character states")
            if claim is None:
                errors.append(f"{prefix} references unknown claim: {claim_id}")
            source_record = states.get(source).claims.get(claim_id) if source in states else None
            if source_record is None:
                errors.append(f"{prefix} source does not know claim: {claim_id}")
            if asserted_stance and asserted_stance not in {
                "supports",
                "rejects",
                "uncertain",
            }:
                errors.append(f"{prefix} has invalid asserted_stance")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            action = next(
                (
                    item
                    for item in actions
                    if str(item.get("actor", "")).strip() == source
                    and str(item.get("action_kind", "")) == "communicate"
                    and str(item.get("outcome", ""))
                    in {"success", "partial", "complication"}
                ),
                None,
            )
            if action is None:
                errors.append(f"{prefix} lacks a resolved communication action")
            action_location = str(
                action.get("location", "") if action else ""
            ).strip()
            if action is not None and not shares_action_location(
                source,
                target,
                action_location,
                scene_state,
                observation_windows,
            ):
                errors.append(
                    f"{prefix} communication location is not shared this turn"
                )
            if source_record is not None:
                unknown_citations = set(cited_evidence).difference(
                    source_record.evidence_refs
                )
                if unknown_citations:
                    errors.append(
                        f"{prefix} source cannot cite unknown evidence: "
                        f"{sorted(unknown_citations)}"
                    )
            shown_evidence = []
            for ref in cited_evidence:
                state = scene_state.get_object_state(ref) if scene_state else {}
                if str(state.get("owner") or "") == source or ref in (
                    scene_state.get_visible_objects(target) if scene_state else {}
                ):
                    shown_evidence.append(ref)
                else:
                    errors.append(f"{prefix} cited evidence is not presentable: {ref}")
            if any(error.startswith(prefix) for error in errors):
                continue
            trust = 0.0
            if relationship_book is not None:
                trust = float(
                    relationship_book.get_metrics(target, source).get("trust", 0.0)
                    or 0.0
                )
            confidence = 0.85 if shown_evidence else min(
                0.9, max(0.2, 0.6 + trust * 0.05)
            )
            stance = asserted_stance or source_record.stance
            record = states[target].learn(
                claim_id=claim_id,
                stance=stance,
                confidence=confidence,
                basis="reported",
                source=source,
                step=step,
                evidence_refs=shown_evidence,
            )
            applied.append(
                {
                    "operation": "communicate",
                    "source": source,
                    "target": target,
                    "claim_id": claim_id,
                    "asserted_stance": stance,
                    "confidence": record.confidence,
                    "cited_evidence": shown_evidence,
                    "reason": reason,
                }
            )
        return applied, errors

    @staticmethod
    def _component(entities: Dict[str, Entity], component_name: str) -> Any:
        for entity in entities.values():
            component = entity.get_component(component_name)
            if component is not None:
                return component
        return None

    @classmethod
    def _text_list(cls, value: Any, limit: int, item_limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return [
            text for raw in value[:limit]
            if (text := cls._text(raw, item_limit))
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
