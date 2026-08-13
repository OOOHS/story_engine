from typing import Any, ClassVar, Dict, List, Literal

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class WorldEventImpact(BaseModel):
    """One host-derived authoritative fact family affected by an event.

    Impacts are reactive invalidation keys, not narrative tags.  They let the
    host wake a goal when a fact it depends on may have changed without asking
    an LLM to judge semantic relevance.
    """

    scope: Literal[
        "actor",
        "world_object",
        "scene",
        "relationship",
        "social_relation",
        "obligation",
        "agreement",
        "claim",
        "knowledge",
        "world_event",
    ]
    target: str
    path: str = "*"


class WorldEventFact(Component):
    """An objective occurrence that exists independently of anyone's belief."""

    event_id: str
    kind: str
    title: str
    statement: str
    occurred_step: int
    location: str = ""
    subjects: List[str] = Field(default_factory=list)
    objects: List[str] = Field(default_factory=list)
    source_type: str = "timeline"
    source_ref: str = ""
    visibility: Literal["public", "local", "hidden"] = "local"
    impacts: List[WorldEventImpact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorldEventWitnesses(Component):
    """Only direct epistemic entry points; later beliefs remain character-local."""

    direct_witnesses: List[str] = Field(default_factory=list)
    self_witnesses: List[str] = Field(default_factory=list)
    attention_recipients: List[str] = Field(default_factory=list)


class WorldEventCommunication(BaseModel):
    """One host-verified communication of the canonical event fact."""

    source: str
    target: str
    step: int
    mode: Literal["reported"] = "reported"
    response_kind: Literal[
        "report",
        "explain",
        "apologize",
        "accuse",
        "request",
        "forgive",
        "acknowledge",
    ] = "report"
    response_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}->{self.target}"

    @property
    def response_key(self) -> str:
        return f"{self.key}:{self.response_kind}"


class WorldEventResponses(Component):
    """Committed social responses that can resolve event-derived goals."""

    communications: List[WorldEventCommunication] = Field(default_factory=list)

    RESPONSE_KINDS: ClassVar[set[str]] = {
        "report",
        "explain",
        "apologize",
        "accuse",
        "request",
        "forgive",
        "acknowledge",
    }

    def record_communication(
        self,
        source: str,
        target: str,
        step: int,
        response_kind: str = "report",
        event_id: str = "",
    ) -> bool:
        clean_source = " ".join(str(source or "").split()).strip()[:120]
        clean_target = " ".join(str(target or "").split()).strip()[:120]
        if not clean_source or not clean_target or clean_source == clean_target:
            return False
        clean_kind = str(response_kind or "report").strip().lower()
        if clean_kind not in self.RESPONSE_KINDS:
            clean_kind = "report"
        response_key = f"{clean_source}->{clean_target}:{clean_kind}"
        if any(record.response_key == response_key for record in self.communications):
            return False
        self.communications.append(
            WorldEventCommunication(
                source=clean_source,
                target=clean_target,
                step=int(step),
                response_kind=clean_kind,
                response_id=self.response_id_for(
                    event_id,
                    clean_source,
                    clean_target,
                    clean_kind,
                ),
            )
        )
        self.communications = self.communications[-80:]
        return True

    def communication_keys(self) -> List[str]:
        return list(dict.fromkeys(record.key for record in self.communications))

    def response_keys(self) -> List[str]:
        return [record.response_key for record in self.communications]

    @staticmethod
    def response_id_for(
        event_id: str,
        source: str,
        target: str,
        response_kind: str,
    ) -> str:
        clean_event = " ".join(str(event_id or "").split()).strip()[:180]
        return (
            f"event-response:{clean_event}:"
            f"{str(source).strip()}->{str(target).strip()}:"
            f"{str(response_kind).strip().lower()}"
        )
