import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal

from src.story_engine.agents.types import AgentPerception


SubjectMessageKind = Literal[
    "stimulus",
    "active_observation_result",
    "task_result",
    "world_signal",
    "ledger_update",
    "ledger_retraction",
    # A Host-queued, non-authoritative nudge. Never a fact and never a
    # substitute for the hard proposal_actors gate: the subject may accept,
    # reinterpret, or ignore it outright.
    "director_signal",
]

SUBJECT_BODY_STATE_FIELDS = frozenset({
    "location",
    "sub_location",
    "zone",
    "position",
    "stance",
    "posture",
    "appearance",
    "visible_condition",
    "activity",
    "public_status",
    "health",
    "injuries",
    "fatigue",
    "capabilities",
    "skills",
})


@dataclass(frozen=True)
class SubjectMessage:
    """One actor-private message delivered by the Host to a live subject."""

    message_id: str
    kind: SubjectMessageKind
    step: int
    payload: Dict[str, Any]
    priority: int = 50
    source_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "kind": self.kind,
            "step": int(self.step),
            "priority": int(self.priority),
            "source_ref": self.source_ref,
            "payload": dict(self.payload),
        }


class SubjectInbox:
    """Deduplicated mailbox whose messages are acknowledged after a valid turn."""

    def __init__(self) -> None:
        self._pending: Dict[str, SubjectMessage] = {}
        self._acknowledged: set[str] = set()

    def deliver(self, message: SubjectMessage) -> bool:
        message_id = str(message.message_id).strip()
        if not message_id:
            raise ValueError("subject messages require a message_id")
        if message_id in self._acknowledged or message_id in self._pending:
            return False
        self._pending[message_id] = message
        return True

    def pending(self, *, limit: int = 32) -> tuple[SubjectMessage, ...]:
        ordered = sorted(
            self._pending.values(),
            key=lambda item: (-int(item.priority), int(item.step), item.message_id),
        )
        return tuple(ordered[: max(0, int(limit))])

    def acknowledge(self, message_ids: Iterable[str]) -> None:
        for raw_id in message_ids:
            message_id = str(raw_id).strip()
            if not message_id:
                continue
            self._pending.pop(message_id, None)
            self._acknowledged.add(message_id)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "pending": [item.to_dict() for item in self.pending(limit=10_000)],
            "acknowledged_count": len(self._acknowledged),
        }


class SubjectLedgerProjector:
    """Project Host-verifiable private records as versioned subject messages.

    The projection deliberately excludes plans, focus, free-form reflection,
    retrieved memories and Host-derived sentiments. Those belong to the live
    subject. The Host retains only records needed for epistemic boundaries,
    scheduling, legality and authoritative completion checks.
    """

    _CATEGORY_PRIORITY = {
        "obligation": 90,
        "goal_registration": 85,
        "agreement": 85,
        "schedule_commitment": 80,
        "navigation_problem": 80,
        "claim_position": 75,
        "drive_signal": 70,
        "condition_signal": 70,
        "epistemic_record": 65,
        "known_map": 60,
        "visible_relationship": 55,
        "legacy_private_note": 50,
        "legacy_commitment": 50,
    }

    def __init__(self) -> None:
        self._current: Dict[tuple[str, str], str] = {}
        self._revisions: Dict[tuple[str, str], int] = {}

    def project(
        self,
        inbox: SubjectInbox,
        perception: AgentPerception,
    ) -> None:
        records = self._records(perception)
        next_keys = set(records)
        previous_keys = set(self._current)

        for key in sorted(previous_keys - next_keys):
            category, ref = key
            revision = self._next_revision(key)
            inbox.deliver(SubjectMessage(
                message_id=f"ledger:{category}:{ref}:r{revision}:removed",
                kind="ledger_retraction",
                step=int(perception.step),
                payload={
                    "category": category,
                    "ref": ref,
                    "revision": revision,
                    "status": "no_longer_active_or_available",
                },
                priority=self._CATEGORY_PRIORITY.get(category, 60),
                source_ref=ref,
            ))
            self._current.pop(key, None)

        for key in sorted(next_keys):
            category, ref = key
            payload = records[key]
            digest = self._digest(payload)
            if self._current.get(key) == digest:
                continue
            revision = self._next_revision(key)
            inbox.deliver(SubjectMessage(
                message_id=f"ledger:{category}:{ref}:r{revision}:{digest[:12]}",
                kind="ledger_update",
                step=int(perception.step),
                payload={
                    "category": category,
                    "ref": ref,
                    "revision": revision,
                    "record": payload,
                },
                priority=self._CATEGORY_PRIORITY.get(category, 60),
                source_ref=ref,
            ))
            self._current[key] = digest

    def snapshot(self) -> Dict[str, Any]:
        return {
            "active_records": [
                {
                    "category": category,
                    "ref": ref,
                    "digest": digest[:16],
                    "revision": self._revisions.get((category, ref), 0),
                }
                for (category, ref), digest in sorted(self._current.items())
            ]
        }

    def _next_revision(self, key: tuple[str, str]) -> int:
        revision = self._revisions.get(key, 0) + 1
        self._revisions[key] = revision
        return revision

    @classmethod
    def _records(
        cls,
        perception: AgentPerception,
    ) -> Dict[tuple[str, str], Dict[str, Any]]:
        records: Dict[tuple[str, str], Dict[str, Any]] = {}

        cognition = perception.private_cognition or {}
        cls._add_list(
            records,
            "epistemic_record",
            cognition.get("beliefs", []),
            ("event_id", "statement"),
        )
        cls._add_texts(
            records,
            "legacy_private_note",
            cognition.get("secrets", []),
        )
        cls._add_texts(
            records,
            "legacy_commitment",
            cognition.get("commitments", []),
        )

        drives = perception.private_drives or {}
        cls._add_mapping(records, "drive_signal", drives.get("needs", {}))
        cls._add_list(
            records,
            "condition_signal",
            (perception.private_modifiers or {}).get("active", []),
            ("modifier_id", "kind"),
        )

        goals = perception.private_goals or {}
        cls._add_list(
            records,
            "goal_registration",
            list(goals.get("active", []) or [])
            + list(goals.get("recent_history", []) or []),
            ("goal_id",),
        )
        obligations = perception.private_obligations or {}
        cls._add_list(
            records,
            "obligation",
            list(obligations.get("active", []) or [])
            + list(obligations.get("recent_history", []) or []),
            ("obligation_id",),
        )
        cls._add_list(
            records,
            "schedule_commitment",
            (perception.private_schedule or {}).get("active", []),
            ("commitment_id",),
        )
        cls._add_snapshot_groups(
            records,
            "agreement",
            perception.private_agreements or {},
            ("agreement_id",),
        )

        knowledge = perception.private_knowledge or {}
        cls._add_list(
            records,
            "claim_position",
            knowledge.get("claims", []),
            ("claim_id",),
        )
        known_map = knowledge.get("map", {})
        if isinstance(known_map, dict) and any(known_map.values()):
            records[("known_map", "known-map")] = dict(known_map)
        cls._add_list(
            records,
            "navigation_problem",
            (perception.private_navigation or {}).get("active", []),
            ("problem_id",),
        )
        cls._add_list(
            records,
            "visible_relationship",
            (perception.relationship_context or {}).get("visible_relations", []),
            ("relation_id", "other", "actor"),
        )
        return records

    @classmethod
    def _add_snapshot_groups(
        cls,
        records: Dict[tuple[str, str], Dict[str, Any]],
        category: str,
        snapshot: Dict[str, Any],
        id_fields: tuple[str, ...],
    ) -> None:
        for value in snapshot.values():
            if isinstance(value, list):
                cls._add_list(records, category, value, id_fields)

    @classmethod
    def _add_mapping(
        cls,
        records: Dict[tuple[str, str], Dict[str, Any]],
        category: str,
        values: Any,
    ) -> None:
        if not isinstance(values, dict):
            return
        for raw_ref, raw_record in sorted(values.items(), key=lambda item: str(item[0])):
            ref = _text(raw_ref, 160)
            if not ref or not isinstance(raw_record, dict):
                continue
            records[(category, ref)] = dict(raw_record)

    @classmethod
    def _add_list(
        cls,
        records: Dict[tuple[str, str], Dict[str, Any]],
        category: str,
        values: Any,
        id_fields: tuple[str, ...],
    ) -> None:
        if not isinstance(values, list):
            return
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                continue
            ref = next(
                (
                    candidate
                    for field in id_fields
                    if (candidate := _text(raw.get(field), 160))
                ),
                "",
            )
            if not ref:
                ref = f"record:{index}:{cls._digest(raw)[:16]}"
            records[(category, ref)] = dict(raw)

    @classmethod
    def _add_texts(
        cls,
        records: Dict[tuple[str, str], Dict[str, Any]],
        category: str,
        values: Any,
    ) -> None:
        if not isinstance(values, list):
            return
        for raw in values:
            value = _text(raw, 600)
            if not value:
                continue
            ref = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            records[(category, ref)] = {"text": value, "legacy_seed": True}

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deliver_perception_messages(
    inbox: SubjectInbox,
    perception: AgentPerception,
) -> None:
    """Project POV-safe observations into a subject mailbox without interpretation."""

    for index, item in enumerate(perception.passive_observations[-32:]):
        if not isinstance(item, dict):
            continue
        source_ref = _text(
            item.get("event_id") or item.get("response_id"), 160
        )
        inbox.deliver(SubjectMessage(
            message_id=source_ref or _stable_message_id(
                "passive", perception.step, index, item
            ),
            kind="stimulus",
            step=int(item.get("observed_step", perception.step) or perception.step),
            payload=dict(item),
            priority=_priority(item),
            source_ref=source_ref,
        ))
    for index, item in enumerate(perception.active_observation_results[-16:]):
        if not isinstance(item, dict):
            continue
        source_ref = _text(item.get("event_id"), 160)
        inbox.deliver(SubjectMessage(
            message_id=(
                f"active:{source_ref}" if source_ref else _stable_message_id(
                    "active", perception.step, index, item
                )
            ),
            kind="active_observation_result",
            step=int(item.get("step", perception.step) or perception.step),
            payload=dict(item),
            priority=max(60, _priority(item)),
            source_ref=source_ref,
        ))
    for index, item in enumerate(perception.world_signals[-16:]):
        if not isinstance(item, dict):
            continue
        inbox.deliver(SubjectMessage(
            message_id=_stable_message_id("signal", perception.step, index, item),
            kind="world_signal",
            step=int(perception.step),
            payload=dict(item),
            priority=_priority(item),
        ))
    for index, item in enumerate(perception.director_signals[-4:]):
        if not isinstance(item, dict):
            continue
        inbox.deliver(SubjectMessage(
            message_id=_stable_message_id("director", perception.step, index, item),
            kind="director_signal",
            step=int(perception.step),
            payload=dict(item),
            # Advisory, and deliberately quieter than a real world_signal or
            # ledger fact: it should be easy for a character to notice
            # without ever crowding out what actually happened.
            priority=40,
        ))


def build_subject_wake_packet(
    *,
    entity: Any,
    perception: AgentPerception,
    messages: Iterable[SubjectMessage],
    bootstrap: bool,
) -> Dict[str, Any]:
    identity = entity.get_component("Identity")
    packet = {
        "subject_protocol_version": 1,
        "subject_id": entity.id,
        "actor_name": perception.actor_name,
        "wake": {
            "step": int(perception.step),
            "activation_scope": perception.activation_scope,
            "body": {
                key: value
                for key, value in dict(perception.self_state).items()
                if key in SUBJECT_BODY_STATE_FIELDS
            },
            "visible_world": dict(perception.world_view),
            "affordance_opportunities": list(perception.affordance_opportunities),
            "ongoing_actions": list(perception.ongoing_actions),
        },
        "messages": [item.to_dict() for item in messages],
        "agent_contract": {
            "assigned_character": perception.actor_name,
            "role": (
                "You operate this character's next action. Persona, private "
                "knowledge and current evidence constrain what they would do. "
                "That is still this character's choice: only you may propose "
                "for this body. Advisory director_signals may be noticed, "
                "reinterpreted, or ignored."
            ),
            "diegesis": (
                "Speech, thought texture and in-world knowledge belong to the "
                "assigned character. They do not know about the Host, JSON, "
                "tools or this protocol."
            ),
            "not_a_director": (
                "Do not act for any other character, do not rewrite the world, "
                "and do not choose an action because it would make a better scene."
            ),
        },
        "ownership_contract": {
            "host_private_ledger": (
                "Messages of kind ledger_update/ledger_retraction are POV-safe, "
                "Host-verifiable records used for knowledge, schedules, obligations, "
                "agreements, registered goals and settlement. They are evidence or "
                "constraints, not a declaration of what this character feels, "
                "values or intends."
            ),
            "subject_mind": (
                "On behalf of this character, you retrieve memory, attend, "
                "appraise, plan and choose using your native conversation/memory "
                "tools. Do not ask the Host to mirror that mind state."
            ),
            "registrations": (
                "Use top-level goal_requests only when you want the Host to register "
                "or update an externally verifiable completion watch. Registration "
                "does not create the character's desire; it lets the world scheduler "
                "and rule engine observe progress without reading their mind."
            ),
        },
        "response_contract": {
            "direct": "Return exactly one executable action as a non-empty natural-language string.",
            "deliberation": "Deliberate privately, then return exactly one action string.",
            "optional_registrations": (
                "A top-level goal_requests list may contain at most one Host-verifiable "
                "adopt/refine/abandon request. Do not return plan, focus, emotion, "
                "belief_updates, commitments or memory state to the Host."
            ),
            "goal_request_schema": {
                "operation": "adopt | refine | abandon",
                "goal_id": "required for refine/abandon; copy a registered goal ref",
                "title": "required for adopt",
                "source_kind": (
                    "resolved_goal | claim | world_event | event_response | drive_need | "
                    "obligation | agreement | visible_object | visible_actor | "
                    "relationship | navigation_problem"
                ),
                "source_ref": "required for adopt; copy a real ledger/world ref",
                "reason": "brief subjective reason; never a completion claim",
                "resolution_kind": (
                    "optional Host-verifiable watch such as reach_location, "
                    "possess_object, deliver_object, fulfill_obligation, "
                    "settle_agreement, verify_claim or communicate_event"
                ),
                "resolution_target": "optional real world/ledger ref",
            },
        },
    }
    if bootstrap:
        controller = entity.get_component("AgentController")
        controller_config = (
            dict(getattr(controller, "config", {}) or {})
            if controller is not None
            else {}
        )
        persona_constraints = str(
            controller_config.get("system_instruction_extras", "") or ""
        ).strip()
        packet["identity_bootstrap"] = {
            "name": getattr(identity, "name", perception.actor_name),
            "role": getattr(identity, "role", ""),
            "personality": getattr(identity, "personality", ""),
            "background": getattr(identity, "background", None),
            "goals": list(getattr(identity, "goals", []) or []),
            "traits": dict(perception.private_traits),
            "persona_constraints": persona_constraints,
            "use": (
                "Assigned persona for the character you operate. Constrain the "
                "action you submit; do not treat this as a first-person "
                "identity you must inhabit while using tools."
            ),
        }
    return packet


def _priority(value: Dict[str, Any]) -> int:
    try:
        return max(0, min(100, int(value.get("priority", 50) or 50)))
    except (TypeError, ValueError):
        return 50


def _stable_message_id(prefix: str, step: int, index: int, value: Any) -> str:
    digest = hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{int(step)}:{int(index)}:{digest}"


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]
