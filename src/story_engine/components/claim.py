from typing import Any, Dict, List, Literal

from pydantic import Field

from src.story_engine.core.component import Component


class ClaimFact(Component):
    """An objective proposition entity; truth is host-only authoritative state."""

    claim_id: str
    statement: str
    truth_status: Literal["true", "false", "unknown"] = "unknown"
    visibility: Literal["public", "secret"] = "secret"
    subjects: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    updated_step: int = 0


class ClaimConditions(Component):
    """Optional state locks that keep a claim's truth synchronized with the world."""

    truth_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    false_conditions: List[Dict[str, Any]] = Field(default_factory=list)


class ClaimEvidence(Component):
    """World-object references that support or refute a claim."""

    supports: List[str] = Field(default_factory=list)
    refutes: List[str] = Field(default_factory=list)
