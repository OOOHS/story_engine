import json
from typing import Any, Callable, Dict, Protocol

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.subject import (
    GumbelSubjectSampler,
    SubjectActionOption,
    SubjectInbox,
    SubjectLedgerProjector,
    build_subject_wake_packet,
    deliver_perception_messages,
)
from src.story_engine.agents.types import AgentDecision, AgentPerception
from src.story_engine.core.entity import Entity


class HermesConversation(Protocol):
    """Small boundary expected from a project-owned Hermes thin shell."""

    def run_conversation(self, prompt: str) -> Any:
        ...


class HermesCharacterAgent:
    """Long-lived character subject backed by an isolated Hermes conversation.

    ``conversation_factory`` belongs in the host application. It may construct
    ``AIAgent`` from the vendored runtime, a container RPC proxy, or a test
    double. Story Engine depends only on this narrow conversation boundary.

    Hermes owns deliberation and samples one intentional action internally.
    The Host receives no candidate distribution and remains responsible only
    for scheduling, legality, uncertainty and authoritative world settlement.
    """

    def __init__(
        self,
        conversation_factory: Callable[[Entity, Dict[str, Any]], HermesConversation],
        config: Dict[str, Any] | None = None,
    ) -> None:
        self._factory = conversation_factory
        self._config = config or {}
        self._conversations: Dict[str, HermesConversation] = {}
        self._inboxes: Dict[str, SubjectInbox] = {}
        self._ledger_projectors: Dict[str, SubjectLedgerProjector] = {}
        self._samplers: Dict[str, GumbelSubjectSampler] = {}
        self._bootstrapped: set[str] = set()
        self._turn_counts: Dict[str, int] = {}
        self._decision_ledgers: Dict[str, list[Dict[str, Any]]] = {}

    def decide(self, entity: Entity, perception: AgentPerception) -> AgentDecision:
        conversation = self._conversations.get(entity.id)
        if conversation is None:
            conversation = self._factory(entity, dict(self._config))
            self._conversations[entity.id] = conversation
        inbox = self._inboxes.setdefault(entity.id, SubjectInbox())
        deliver_perception_messages(inbox, perception)
        projector = self._ledger_projectors.setdefault(
            entity.id, SubjectLedgerProjector()
        )
        projector.project(inbox, perception)
        pending = inbox.pending(limit=int(self._config.get("inbox_limit", 32) or 32))
        packet = build_subject_wake_packet(
            entity=entity,
            perception=perception,
            messages=pending,
            bootstrap=entity.id not in self._bootstrapped,
        )
        run_subject_turn = getattr(conversation, "run_subject_turn", None)
        if callable(run_subject_turn):
            result = run_subject_turn(packet)
        else:
            result = conversation.run_conversation(
                json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            )
        if not isinstance(result, dict):
            raise ValueError("Hermes conversation must return a protocol envelope")
        if result.get("protocol_version") != 1:
            raise ValueError("Hermes conversation returned an unsupported protocol version")
        if str(result.get("agent_id", "")) != entity.id:
            raise ValueError("Hermes conversation returned a mismatched agent_id")
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Hermes conversation returned no protocol content")
        decision = self._parse_subject_decision(entity, perception, content)
        inbox.acknowledge(item.message_id for item in pending)
        self._bootstrapped.add(entity.id)
        return decision

    def subject_snapshot(self, entity_or_id: Entity | str) -> Dict[str, Any]:
        entity_id = entity_or_id.id if isinstance(entity_or_id, Entity) else str(entity_or_id)
        inbox = self._inboxes.get(entity_id)
        return {
            "bootstrapped": entity_id in self._bootstrapped,
            "turn_count": self._turn_counts.get(entity_id, 0),
            "inbox": inbox.snapshot() if inbox is not None else {
                "pending": [],
                "acknowledged_count": 0,
            },
            "host_private_ledger": (
                self._ledger_projectors[entity_id].snapshot()
                if entity_id in self._ledger_projectors
                else {"active_records": []}
            ),
            "decision_ledger": list(self._decision_ledgers.get(entity_id, [])),
        }

    def close(self) -> None:
        for conversation in self._conversations.values():
            close = getattr(conversation, "close", None)
            if callable(close):
                close()
        self._conversations.clear()
        self._ledger_projectors.clear()

    def _parse_subject_decision(
        self,
        entity: Entity,
        perception: AgentPerception,
        content: str,
    ) -> AgentDecision:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("agent response is not valid decision JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("agent decision JSON must be an object")
        raw_candidates = data.get("candidates", [])
        if isinstance(raw_candidates, list) and raw_candidates:
            options = tuple(
                SubjectActionOption.from_value(item, index=index)
                for index, item in enumerate(raw_candidates[:8])
            )
            if len(options) < 2:
                raise ValueError(
                    "Hermes subject deliberation requires at least two candidates; "
                    "return a direct action when only one path is reasonable"
                )
            count = self._turn_counts.get(entity.id, 0) + 1
            self._turn_counts[entity.id] = count
            decision_id = f"{entity.id}:{int(perception.step)}:{count}"
            sampler = self._samplers.get(entity.id)
            if sampler is None:
                configured_seed = self._config.get("character_seed")
                sampler = GumbelSubjectSampler(configured_seed)
                self._samplers[entity.id] = sampler
            choice = sampler.choose(
                options,
                decision_id=decision_id,
                temperature=float(
                    self._config.get("character_temperature", 0.8) or 0.8
                ),
            )
            ledger = self._decision_ledgers.setdefault(entity.id, [])
            ledger.append(choice.trace)
            del ledger[:-100]
            action = choice.selected.action
            return AgentDecision(
                action=action.detail,
                action_spec=action,
                candidates=(),
                thought="",
                metadata=self._subject_metadata(data),
            )

        action = AgentAction.from_value(data.get("action"), strict=True)
        if not action.detail:
            raise ValueError("Hermes subject returned no executable action")
        count = self._turn_counts.get(entity.id, 0) + 1
        self._turn_counts[entity.id] = count
        self._decision_ledgers.setdefault(entity.id, []).append({
            "decision_id": f"{entity.id}:{int(perception.step)}:{count}",
            "method": "direct",
            "selected_action": action.to_dict(),
        })
        return AgentDecision(
            action=action.detail,
            action_spec=action,
            candidates=(),
            thought="",
            metadata=self._subject_metadata(data),
        )

    @staticmethod
    def _subject_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
        """Expose registrations, never Hermes-owned mental state, to the Host."""

        requests = data.get("goal_requests", [])
        if not isinstance(requests, list):
            requests = []
        bounded = [
            dict(item)
            for item in requests[:1]
            if isinstance(item, dict)
        ]
        metadata: Dict[str, Any] = {"subject_runtime": True}
        if bounded:
            metadata["goal_requests"] = bounded
        return metadata
