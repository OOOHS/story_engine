from typing import Any, Dict, List

from pydantic import Field

from src.story_engine.core.component import Component


class AgreementTerms(Component):
    """Accepted or proposed clauses carried by an Agreement Entity."""

    title: str = ""
    summary: str = ""
    proposal_reason: str = ""
    transfers: List[Dict[str, Any]] = Field(default_factory=list)
    delegations: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    escrows: List[Dict[str, Any]] = Field(default_factory=list)


class AgreementLifecycle(Component):
    """Negotiation, expiry, lineage and execution state of an agreement."""

    accepted_by: List[str] = Field(default_factory=list)
    status: str = "pending"
    expires_step: int = 0
    resolution_reason: str = ""
    resolution_source_kind: str = ""
    resolution_source_ref: str = ""
    countered_from: str = ""
    superseded_by: str = ""
    performance_status: str = "none"
    performance_obligations: List[Dict[str, Any]] = Field(default_factory=list)
    performance_reason: str = ""
    escrow_lots: List[Dict[str, Any]] = Field(default_factory=list)
