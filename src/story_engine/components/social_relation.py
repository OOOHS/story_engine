from typing import Any, Dict, List, Literal

from pydantic import Field

from src.story_engine.core.component import Component


class SocialRelation(Component):
    """Reusable identity and participant boundary for social relation entities."""

    relation_id: str
    relation_kind: str
    participants: List[str] = Field(default_factory=list)
    participant_roles: Dict[str, str] = Field(default_factory=dict)
    initiator: str = ""
    visibility: Literal["participants", "public", "hidden"] = "participants"
    created_step: int = 0
    parent_relation_id: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)
