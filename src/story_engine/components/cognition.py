from copy import deepcopy
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from src.story_engine.attention import (
    ATTENTION_DELIVERY_LIMIT,
    HostAttentionPolicy,
)
from src.story_engine.core.component import Component


class AttentionRecord(BaseModel):
    attention_id: str
    kind: Literal["world_event", "event_response"]
    priority: int = Field(default=50, ge=0, le=100)
    step: int = 0


class Cognition(Component):
    """Host-side private epistemic receipts plus legacy mind compatibility fields.

    Event-backed beliefs and experiences prove what reached this actor's POV;
    they are private but Host-verifiable delivery records, not public truth.
    ``secrets``, ``commitments`` and ``current_focus`` remain for authored seeds
    and non-subject runtimes. A persistent Hermes subject owns their live
    equivalents in native memory and never writes them back here. Objective
    propositions use Claim Entity + KnowledgeState references. None of this is
    part of the authoritative physical ``SceneState``.
    """

    beliefs: List[Dict[str, Any]] = Field(default_factory=list)
    secrets: List[str] = Field(default_factory=list)
    commitments: List[str] = Field(default_factory=list)
    current_focus: str = ""
    experiences: List[Dict[str, Any]] = Field(default_factory=list)
    pending_world_events: List[str] = Field(default_factory=list)
    pending_event_responses: List[str] = Field(default_factory=list)
    world_event_attention: Dict[str, AttentionRecord] = Field(default_factory=dict)
    event_response_attention: Dict[str, AttentionRecord] = Field(default_factory=dict)

    def get_private_snapshot(self, current_step: int | None = None) -> Dict[str, Any]:
        return {
            "beliefs": deepcopy(self.beliefs[-40:]),
            "secrets": list(self.secrets[-20:]),
            "commitments": list(self.commitments[-20:]),
            "current_focus": self.current_focus,
            "recent_experiences": deepcopy(self.experiences[-20:]),
            "pending_world_events": list(
                self._ranked_world_events(current_step)[:ATTENTION_DELIVERY_LIMIT]
            ),
            "pending_event_responses": list(
                self._ranked_event_responses(current_step)[:ATTENTION_DELIVERY_LIMIT]
            ),
        }

    def record_experience(self, step: int, events: List[Dict[str, Any]]) -> None:
        cleaned_events = []
        for item in events or []:
            if not isinstance(item, dict):
                continue
            result = self._clean_text(item.get("result"), max_len=600)
            private_result = self._clean_text(
                item.get("private_result"), max_len=800
            )
            if not result and not private_result:
                continue
            cleaned_events.append(
                {
                    "actor": self._clean_text(item.get("actor"), max_len=120),
                    "intent": self._clean_text(item.get("intent"), max_len=500),
                    "outcome": self._clean_text(item.get("outcome"), max_len=40),
                    "result": result,
                    "private_result": private_result,
                    "action_kind": self._clean_text(
                        item.get("action_kind"), max_len=40
                    ),
                    "action_target": self._clean_text(
                        item.get("action_target"), max_len=160
                    ),
                    "observation_mode": self._clean_text(
                        item.get("observation_mode"), max_len=20
                    ) or "passive",
                    "witness_mode": self._clean_text(
                        item.get("witness_mode"), max_len=20
                    ),
                    "event_id": self._clean_text(
                        item.get("event_id"), max_len=180
                    ),
                    "response_id": self._clean_text(
                        item.get("response_id"), max_len=260
                    ),
                    "response_kind": self._clean_text(
                        item.get("response_kind"), max_len=20
                    ),
                    "location": self._clean_text(item.get("location"), max_len=160),
                    "visibility": self._clean_text(item.get("visibility"), max_len=40),
                    "personal": bool(item.get("personal", False)),
                }
            )
        if not cleaned_events:
            return
        self.experiences.append({"step": int(step), "events": cleaned_events})
        self.experiences = self.experiences[-60:]

    def knows(self, statement: Any) -> bool:
        normalized = self._clean_text(statement, max_len=500)
        if not normalized:
            return False
        if normalized in {self._clean_text(item, 500) for item in self.secrets}:
            return True
        return any(
            self._clean_text(item.get("statement"), 500) == normalized
            for item in self.beliefs
            if isinstance(item, dict)
        )

    def knows_event(self, event_id: Any) -> bool:
        key = self._clean_text(event_id, max_len=180)
        return bool(
            key
            and any(
                self._clean_text(item.get("event_id"), 180) == key
                for item in self.beliefs
                if isinstance(item, dict)
            )
        )

    def event_statement(self, event_id: Any) -> str:
        key = self._clean_text(event_id, max_len=180)
        for item in reversed(self.beliefs):
            if (
                isinstance(item, dict)
                and self._clean_text(item.get("event_id"), 180) == key
            ):
                return self._clean_text(item.get("statement"), 800)
        return ""

    def knows_event_response(self, response_id: Any) -> bool:
        key = self._clean_text(response_id, max_len=260)
        if not key:
            return False
        return any(
            self._clean_text(event.get("response_id"), 260) == key
            for experience in self.experiences
            if isinstance(experience, dict)
            for event in experience.get("events", [])
            if isinstance(event, dict)
        )

    def record_world_event(
        self,
        *,
        event_id: str,
        statement: str,
        step: int,
        location: str,
        witness_mode: str,
        confidence: float = 1.0,
        enqueue_attention: bool = True,
        attention_priority: int = 50,
    ) -> None:
        event_key = self._clean_text(event_id, max_len=180)
        statement_text = self._clean_text(statement, max_len=800)
        if not event_key or not statement_text:
            return
        already_known = self.knows_event(event_key)
        self.beliefs = [
            item
            for item in self.beliefs
            if self._clean_text(item.get("event_id"), 180) != event_key
        ]
        self.beliefs.append(
            {
                "event_id": event_key,
                "statement": statement_text,
                "confidence": min(1.0, max(0.0, float(confidence))),
                "source": f"{witness_mode}_world_event:{event_key}",
                "updated_step": int(step),
            }
        )
        self._bound_beliefs()
        if (
            enqueue_attention
            and not already_known
            and event_key not in self.pending_world_events
        ):
            self.pending_world_events.append(event_key)
            self.world_event_attention[event_key] = AttentionRecord(
                attention_id=event_key,
                kind="world_event",
                priority=self._priority(attention_priority),
                step=int(step),
            )
            self._reorder_attention(current_step=int(step))
        self.record_experience(
            step=int(step),
            events=[
                {
                    "actor": "World",
                    "intent": "",
                    "outcome": "occurred",
                    "result": statement_text,
                    "action_kind": "world_event",
                    "action_target": event_key,
                    "observation_mode": "passive",
                    "witness_mode": witness_mode,
                    "location": location,
                    "visibility": "local",
                    "personal": witness_mode == "self",
                }
            ],
        )

    def next_pending_world_event(self, current_step: int | None = None) -> str:
        ranked = self._ranked_world_events(current_step)
        return ranked[0] if ranked else ""

    def next_pending_event_response(self, current_step: int | None = None) -> str:
        ranked = self._ranked_event_responses(current_step)
        return ranked[0] if ranked else ""

    def next_pending_attention(
        self, current_step: int | None = None
    ) -> tuple[str, str]:
        ranked = HostAttentionPolicy.combined_ranked(
            [
                (
                    "world_event",
                    self.pending_world_events,
                    self.world_event_attention,
                ),
                (
                    "event_response",
                    self.pending_event_responses,
                    self.event_response_attention,
                ),
            ],
            current_step=current_step,
        )
        return ranked[0] if ranked else ("", "")

    def acknowledge_world_events(self, event_ids: List[str] | None = None) -> None:
        if event_ids is None:
            self.pending_world_events = []
            self.world_event_attention = {}
            return
        acknowledged = {
            self._clean_text(event_id, max_len=180)
            for event_id in event_ids
            if self._clean_text(event_id, max_len=180)
        }
        if acknowledged:
            self.pending_world_events = [
                event_id
                for event_id in self.pending_world_events
                if event_id not in acknowledged
            ]
            for event_id in acknowledged:
                self.world_event_attention.pop(event_id, None)

    def acknowledge_event_responses(
        self, response_ids: List[str] | None = None
    ) -> None:
        if response_ids is None:
            self.pending_event_responses = []
            self.event_response_attention = {}
            return
        acknowledged = {
            self._clean_text(response_id, max_len=260)
            for response_id in response_ids
            if self._clean_text(response_id, max_len=260)
        }
        if acknowledged:
            self.pending_event_responses = [
                response_id
                for response_id in self.pending_event_responses
                if response_id not in acknowledged
            ]
            for response_id in acknowledged:
                self.event_response_attention.pop(response_id, None)

    def record_event_response(
        self,
        *,
        response_id: str,
        event_id: str,
        source: str,
        response_kind: str,
        statement: str,
        step: int,
        location: str,
        enqueue_attention: bool = True,
        attention_priority: int = 75,
    ) -> None:
        clean_response = self._clean_text(response_id, max_len=260)
        clean_event = self._clean_text(event_id, max_len=180)
        clean_source = self._clean_text(source, max_len=120)
        clean_kind = self._clean_text(response_kind, max_len=20)
        clean_statement = self._clean_text(statement, max_len=800)
        if not clean_response or not clean_event or not clean_source or not clean_kind:
            return
        if enqueue_attention and clean_response not in self.pending_event_responses:
            self.pending_event_responses.append(clean_response)
            self.event_response_attention[clean_response] = AttentionRecord(
                attention_id=clean_response,
                kind="event_response",
                priority=self._priority(attention_priority),
                step=int(step),
            )
            self._reorder_attention(current_step=int(step))
        labels = {
            "report": "转述",
            "explain": "解释",
            "apologize": "道歉",
            "accuse": "指控",
            "request": "请求",
            "forgive": "宽恕表达",
            "acknowledge": "确认回应",
        }
        label = labels.get(clean_kind, clean_kind)
        result = f"{clean_source}围绕事件作出了{label}。"
        if clean_statement:
            result += f"相关事件：{clean_statement}"
        self.record_experience(
            step=int(step),
            events=[
                {
                    "actor": clean_source,
                    "intent": "",
                    "outcome": "occurred",
                    "result": result,
                    "action_kind": "communicate",
                    "action_target": clean_source,
                    "observation_mode": "passive",
                    "witness_mode": "recipient",
                    "location": location,
                    "visibility": "local",
                    "personal": False,
                    "event_id": clean_event,
                    "response_id": clean_response,
                    "response_kind": clean_kind,
                }
            ],
        )

    def _ranked_world_events(self, current_step: int | None = None) -> List[str]:
        return self._ranked(
            self.pending_world_events,
            self.world_event_attention,
            current_step=current_step,
        )

    def _ranked_event_responses(self, current_step: int | None = None) -> List[str]:
        return self._ranked(
            self.pending_event_responses,
            self.event_response_attention,
            current_step=current_step,
        )

    @staticmethod
    def _ranked(
        attention_ids: List[str],
        records: Dict[str, AttentionRecord],
        *,
        current_step: int | None = None,
    ) -> List[str]:
        return HostAttentionPolicy.ranked_ids(
            attention_ids,
            records,
            current_step=current_step,
        )

    def _reorder_attention(self, current_step: int | None = None) -> None:
        ranked_events = self._ranked_world_events(current_step)
        ranked_responses = self._ranked_event_responses(current_step)
        self.pending_world_events = HostAttentionPolicy.retain_ids(
            ranked_events, self.world_event_attention
        )
        self.pending_event_responses = HostAttentionPolicy.retain_ids(
            ranked_responses, self.event_response_attention
        )
        self.world_event_attention = {
            attention_id: self.world_event_attention[attention_id]
            for attention_id in self.pending_world_events
            if attention_id in self.world_event_attention
        }
        self.event_response_attention = {
            attention_id: self.event_response_attention[attention_id]
            for attention_id in self.pending_event_responses
            if attention_id in self.event_response_attention
        }

    @staticmethod
    def _priority(value: Any) -> int:
        return HostAttentionPolicy.clamp(value)

    def apply_agent_updates(self, metadata: Dict[str, Any], step: int) -> None:
        """Accept bounded updates from legacy non-subject runtimes."""
        if not isinstance(metadata, dict):
            return

        if metadata.get("clear_focus") is True:
            self.current_focus = ""
        focus = self._clean_text(metadata.get("focus"), max_len=240)
        if focus:
            self.current_focus = focus

        belief_updates = metadata.get("belief_updates", []) or []
        bounded_updates = belief_updates[:8] if isinstance(belief_updates, list) else []
        for raw in bounded_updates:
            if not isinstance(raw, dict):
                continue
            statement = self._clean_text(raw.get("statement"), max_len=500)
            if not statement:
                continue
            operation = self._clean_text(
                raw.get("operation"), max_len=20
            ).casefold()
            if operation == "retract":
                self.beliefs = [
                    item
                    for item in self.beliefs
                    if item.get("event_id")
                    or self._clean_text(item.get("statement"), 500) != statement
                ]
                continue
            if any(
                item.get("event_id")
                and self._clean_text(item.get("statement"), 500) == statement
                for item in self.beliefs
                if isinstance(item, dict)
            ):
                continue
            try:
                confidence = float(raw.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            record = {
                "statement": statement,
                "confidence": min(1.0, max(0.0, confidence)),
                "source": self._clean_text(raw.get("source"), max_len=120) or "agent_inference",
                "updated_step": int(step),
            }
            self.beliefs = [
                item
                for item in self.beliefs
                if item.get("event_id")
                or self._clean_text(item.get("statement"), 500) != statement
            ]
            self.beliefs.append(record)
        self._bound_beliefs()

        resolved = {
            self._clean_text(item, max_len=300)
            for item in (metadata.get("resolved_commitments", []) or [])
        }
        resolved.discard("")
        if resolved:
            self.commitments = [item for item in self.commitments if item not in resolved]

        for raw in metadata.get("commitments", []) or []:
            commitment = self._clean_text(raw, max_len=300)
            if commitment and commitment not in self.commitments:
                self.commitments.append(commitment)
        self.commitments = self.commitments[-40:]

    def _clean_text(self, value: Any, max_len: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        return text[:max_len]

    def _bound_beliefs(self, limit: int = 100) -> None:
        overflow = max(0, len(self.beliefs) - int(limit))
        if not overflow:
            return
        bounded = []
        for item in self.beliefs:
            if overflow and not item.get("event_id"):
                overflow -= 1
                continue
            bounded.append(item)
        self.beliefs = bounded[-int(limit):]
