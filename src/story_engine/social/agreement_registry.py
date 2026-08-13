from copy import deepcopy
from typing import Any, ClassVar, Dict, Iterable, List
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from src.story_engine.components.agreement import AgreementLifecycle, AgreementTerms
from src.story_engine.components.social_relation import SocialRelation
from src.story_engine.core.entity import Entity
from src.story_engine.social.relation_registry import SocialRelationRegistry


class AgreementRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agreement_id: str = Field(default="", validation_alias="contract_id")
    proposer: str
    parties: List[str] = Field(min_length=2, max_length=3)
    title: str = ""
    summary: str = ""
    proposal_reason: str = ""
    transfers: List[Dict[str, Any]] = Field(default_factory=list)
    delegations: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    escrows: List[Dict[str, Any]] = Field(default_factory=list)
    escrow_lots: List[Dict[str, Any]] = Field(default_factory=list)
    accepted_by: List[str] = Field(default_factory=list)
    status: str = "pending"
    created_step: int = 0
    source_kind: str = ""
    source_ref: str = ""
    expires_step: int = 0
    resolution_reason: str = ""
    resolution_source_kind: str = ""
    resolution_source_ref: str = ""
    performance_status: str = "none"
    performance_obligations: List[Dict[str, Any]] = Field(default_factory=list)
    performance_reason: str = ""
    countered_from: str = ""
    superseded_by: str = ""

    @property
    def contract_id(self) -> str:
        return self.agreement_id


class AgreementBook(BaseModel):
    """Transient transaction view reconstructed from Agreement Entities."""

    agreements: Dict[str, AgreementRecord] = Field(default_factory=dict)
    max_agreements: int = Field(default=48, ge=0, le=200)

    TERMINAL_STATUSES: ClassVar[set[str]] = {
        "settled",
        "rejected",
        "withdrawn",
        "expired",
        "countered",
    }

    @property
    def contracts(self) -> Dict[str, AgreementRecord]:
        return self.agreements

    @contracts.setter
    def contracts(self, value: Dict[str, AgreementRecord]) -> None:
        self.agreements = value

    @property
    def max_contracts(self) -> int:
        return self.max_agreements

    @max_contracts.setter
    def max_contracts(self, value: int) -> None:
        self.max_agreements = value

    def get_private_snapshot(self, actor: str, current_step: int) -> Dict[str, Any]:
        pending = []
        history = []
        for record in self.agreements.values():
            if actor not in record.parties:
                continue
            item = record.model_dump()
            item["steps_until_expiry"] = record.expires_step - int(current_step)
            item["awaiting_actor"] = (
                record.status == "pending" and actor not in record.accepted_by
            )
            if record.status in self.TERMINAL_STATUSES:
                history.append(item)
            else:
                pending.append(item)
        pending.sort(key=lambda item: (item["expires_step"], item["agreement_id"]))
        history.sort(key=lambda item: (item["expires_step"], item["agreement_id"]))
        counterparties: Dict[str, Dict[str, int]] = {}
        own_performance = {
            "pending": 0,
            "fulfilled": 0,
            "breached": 0,
            "cancelled": 0,
        }
        for record in self.agreements.values():
            if actor not in record.parties or not record.performance_obligations:
                continue
            for link in record.performance_obligations:
                responsible = str(link.get("actor", ""))
                status = str(link.get("resolved_status", "pending"))
                if status not in {"fulfilled", "breached", "cancelled"}:
                    status = "pending"
                if responsible == actor:
                    own_performance[status] += 1
                else:
                    counters = counterparties.setdefault(
                        responsible,
                        {"pending": 0, "fulfilled": 0, "breached": 0, "cancelled": 0},
                    )
                    counters[status] += 1
        return {
            "pending": pending,
            "recent_history": history[-12:],
            "awaiting_count": sum(item["awaiting_actor"] for item in pending),
            "counterparty_performance": counterparties,
            "own_performance": own_performance,
        }

    def next_wakeup(self, actor: str, current_step: int, horizon: int = 1) -> str:
        candidates = [
            record
            for record in self.agreements.values()
            if actor in record.parties
            and record.status == "pending"
            and int(current_step) >= record.expires_step - int(horizon)
        ]
        if not candidates:
            return ""
        candidates.sort(key=lambda record: (record.expires_step, record.agreement_id))
        return candidates[0].agreement_id

    def advance_to(self, current_step: int) -> List[Dict[str, Any]]:
        transitions = []
        for record in self.agreements.values():
            if record.status == "pending" and int(current_step) > record.expires_step:
                record.status = "expired"
                record.resolution_reason = "offer expired before complete acceptance"
                record.resolution_source_kind = "clock"
                record.resolution_source_ref = f"step:{int(current_step)}"
                transitions.append(
                    {"contract_id": record.agreement_id, "status": "expired"}
                )
        return transitions

    def refresh_performance(self, obligation_states: Dict[str, Any]) -> List[Dict[str, Any]]:
        transitions = []
        for record in self.agreements.values():
            if record.status != "settled" or not record.performance_obligations:
                continue
            statuses = []
            resolved_links = []
            for link in record.performance_obligations:
                status, current_actor = self._follow_obligation(
                    obligation_states,
                    str(link.get("actor", "")),
                    str(link.get("obligation_id", "")),
                )
                statuses.append(status)
                resolved_links.append(
                    {**link, "current_actor": current_actor, "resolved_status": status}
                )
            record.performance_obligations = resolved_links
            next_status = "pending"
            if statuses and any(status == "breached" for status in statuses):
                next_status = "breached"
            elif statuses and any(status == "cancelled" for status in statuses):
                next_status = "cancelled"
            elif statuses and all(status == "fulfilled" for status in statuses):
                next_status = "fulfilled"
            if next_status == "pending" or next_status == record.performance_status:
                continue
            record.performance_status = next_status
            record.performance_reason = f"linked service obligations resolved as {next_status}"
            transitions.append(
                {
                    "contract_id": record.agreement_id,
                    "performance_status": next_status,
                }
            )
        return transitions

    @staticmethod
    def _follow_obligation(
        obligation_states: Dict[str, Any], actor: str, obligation_id: str
    ) -> tuple[str, str]:
        current_actor = actor
        visited = set()
        while current_actor and current_actor not in visited:
            visited.add(current_actor)
            state = obligation_states.get(current_actor)
            record = state.obligations.get(obligation_id) if state else None
            if record is None:
                return "missing", current_actor
            if record.status == "delegated" and record.delegated_to:
                current_actor = record.delegated_to
                continue
            return record.status, current_actor
        return "missing", current_actor

    def restore_from(self, snapshot: "AgreementBook") -> None:
        self.agreements = deepcopy(snapshot.agreements)
        self.max_agreements = snapshot.max_agreements


class AgreementRegistry:
    """Agreement-specific view over the unified SocialRelationRegistry."""

    def __init__(
        self,
        relation_registry: SocialRelationRegistry | None = None,
        max_agreements: int = 48,
    ) -> None:
        self.max_agreements = max(0, min(200, int(max_agreements)))
        self.relation_registry = relation_registry or SocialRelationRegistry()

    def get(self, agreement_id: str) -> Entity | None:
        entity = self.relation_registry.get(str(agreement_id))
        relation = entity.get_component("SocialRelation") if entity else None
        return entity if relation and relation.relation_kind == "agreement" else None

    def entities(self) -> Iterable[Entity]:
        return self.relation_registry.entities("agreement")

    def to_book(self) -> AgreementBook:
        return AgreementBook(
            agreements={
                entity.get_component("SocialRelation").relation_id: self._record_from_entity(entity)
                for entity in self.entities()
            },
            max_agreements=self.max_agreements,
        )

    def apply_book(
        self,
        book: AgreementBook,
        world_entities: Dict[str, Entity] | None = None,
    ) -> None:
        incoming = set(book.agreements)
        for entity in tuple(self.entities()):
            relation = entity.get_component("SocialRelation")
            if relation.relation_id in incoming:
                continue
            self.relation_registry.remove(relation.relation_id, world_entities)
        for agreement_id, record in book.agreements.items():
            entity = self.get(agreement_id)
            if entity is None:
                entity_name = f"Agreement:{agreement_id}"
                if (
                    world_entities is not None
                    and entity_name in world_entities
                    and world_entities[entity_name] not in self.entities()
                ):
                    raise ValueError(
                        f"agreement entity name collides with existing entity: {entity_name}"
                    )
                entity = Entity(
                    name=entity_name,
                    entity_id=str(uuid5(NAMESPACE_URL, f"story-engine:agreement:{agreement_id}")),
                )
            context_entity = self.relation_registry.ensure_context(
                record.parties,
                world_entities=world_entities,
                created_step=record.created_step,
                provenance={"created_for_agreement": agreement_id},
            )
            parent_relation_id = context_entity.get_component(
                "SocialRelation"
            ).relation_id
            self._write_record(entity, record, parent_relation_id)
            self.relation_registry.register(entity)
            if world_entities is not None:
                world_entities[entity.name] = entity
        self.max_agreements = book.max_agreements

    def get_private_snapshot(self, actor: str, current_step: int) -> Dict[str, Any]:
        return self.to_book().get_private_snapshot(actor, current_step)

    def next_wakeup(self, actor: str, current_step: int, horizon: int = 1) -> str:
        return self.to_book().next_wakeup(actor, current_step, horizon)

    @staticmethod
    def _write_record(
        entity: Entity,
        record: AgreementRecord,
        parent_relation_id: str,
    ) -> None:
        entity.components = {}
        entity.add_component(
            SocialRelation(
                relation_id=record.agreement_id,
                relation_kind="agreement",
                participants=list(record.parties),
                participant_roles={
                    "proposer": record.proposer,
                    **{
                        f"party_{index}": party
                        for index, party in enumerate(record.parties)
                    },
                },
                initiator=record.proposer,
                visibility="participants",
                created_step=record.created_step,
                parent_relation_id=parent_relation_id,
                provenance={
                    "proposal_reason": record.proposal_reason,
                    "source_kind": record.source_kind,
                    "source_ref": record.source_ref,
                },
            )
        )
        entity.add_component(
            AgreementTerms(
                title=record.title,
                summary=record.summary,
                proposal_reason=record.proposal_reason,
                transfers=deepcopy(record.transfers),
                delegations=deepcopy(record.delegations),
                services=deepcopy(record.services),
                escrows=deepcopy(record.escrows),
            )
        )
        entity.add_component(
            AgreementLifecycle(
                accepted_by=list(record.accepted_by),
                status=record.status,
                expires_step=record.expires_step,
                resolution_reason=record.resolution_reason,
                resolution_source_kind=record.resolution_source_kind,
                resolution_source_ref=record.resolution_source_ref,
                countered_from=record.countered_from,
                superseded_by=record.superseded_by,
                performance_status=record.performance_status,
                performance_obligations=deepcopy(record.performance_obligations),
                performance_reason=record.performance_reason,
                escrow_lots=deepcopy(record.escrow_lots),
            )
        )

    @staticmethod
    def _record_from_entity(entity: Entity) -> AgreementRecord:
        relation = entity.get_component("SocialRelation")
        terms = entity.get_component("AgreementTerms")
        lifecycle = entity.get_component("AgreementLifecycle")
        if not relation or not terms or not lifecycle:
            raise ValueError(f"malformed Agreement Entity: {entity.name}")
        return AgreementRecord(
            agreement_id=relation.relation_id,
            proposer=relation.initiator,
            parties=list(relation.participants),
            created_step=relation.created_step,
            source_kind=str(relation.provenance.get("source_kind", "")),
            source_ref=str(relation.provenance.get("source_ref", "")),
            title=terms.title,
            summary=terms.summary,
            proposal_reason=terms.proposal_reason,
            transfers=deepcopy(terms.transfers),
            delegations=deepcopy(terms.delegations),
            services=deepcopy(terms.services),
            escrows=deepcopy(terms.escrows),
            accepted_by=list(lifecycle.accepted_by),
            status=lifecycle.status,
            expires_step=lifecycle.expires_step,
            resolution_reason=lifecycle.resolution_reason,
            resolution_source_kind=lifecycle.resolution_source_kind,
            resolution_source_ref=lifecycle.resolution_source_ref,
            countered_from=lifecycle.countered_from,
            superseded_by=lifecycle.superseded_by,
            performance_status=lifecycle.performance_status,
            performance_obligations=deepcopy(lifecycle.performance_obligations),
            performance_reason=lifecycle.performance_reason,
            escrow_lots=deepcopy(lifecycle.escrow_lots),
        )
