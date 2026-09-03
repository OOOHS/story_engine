import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal

from src.story_engine.agents.types import AgentPerception

# A property of the message itself -- what happened, and to whom -- not of
# whether the receiving actor happens to be scheduled foreground or
# background right now. See HostAttentionPolicy.message_urgency for the
# authoritative classification this mirrors.
#
# - "critical": severe, unignorable consequences on the event's own merit.
#   Surfaced separately in the wake packet's critical_signals array.
# - "direct": personally addressed to or caused by this actor. Guaranteed to
#   be seen, but presented as ordinary content.
# - "ambient": passively witnessed/reported routine content.
#
# Host-verifiable ledger messages (ledger_update/ledger_retraction) do not
# carry an urgency at all -- their delivery timing is structural (bootstrap,
# location change, dormant refresh, or a changed record), not
# priority-driven -- so SubjectMessage.urgency is None for those.
SubjectMessageUrgency = Literal["critical", "direct", "ambient"]


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
    """One actor-private message delivered by the Host to a live subject.

    ``urgency`` is advisory metadata, not a legality gate. It is ``None``
    for Host-verifiable ledger messages, which are not priority-classified
    (see ``SubjectMessageUrgency``).
    """

    message_id: str
    kind: SubjectMessageKind
    step: int
    payload: Dict[str, Any]
    priority: int = 50
    source_ref: str = ""
    urgency: SubjectMessageUrgency | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "message_id": self.message_id,
            "kind": self.kind,
            "step": int(self.step),
            "priority": int(self.priority),
            "source_ref": self.source_ref,
        }
        if self.urgency is not None:
            data["urgency"] = self.urgency
        data["payload"] = dict(self.payload)
        return data


class SubjectLedgerProjector:
    """Project Host-verifiable private records as versioned subject messages.

    The projection deliberately excludes plans, focus, free-form reflection,
    retrieved memories and Host-derived sentiments. Those belong to the live
    subject. The Host retains only records needed for epistemic boundaries,
    scheduling, legality and authoritative completion checks.

    ``project()`` never mutates committed state: it stages the messages it
    would send against the *last committed* baseline and returns them.
    Callers must call ``commit()`` only after the subject turn that received
    those messages actually succeeds. If the turn fails and ``project()`` is
    called again on retry, it recomputes from the same unchanged baseline and
    reproduces the identical messages (same revisions, same message_ids) --
    nothing is skipped as "already sent" and nothing double-increments.
    """

    _CATEGORY_PRIORITY = {
        "goal_registration": 85,
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

    # A POV snapshot (self_body + visible_world) is resent as a full unit --
    # never diffed field by field -- only when the actor bootstraps, changes
    # location, or has gone unrefreshed for this many steps. Anything else
    # relies on stimulus/active_observation_result/ledger deltas to describe
    # what changed since the last snapshot.
    POV_DORMANT_STEPS = 6
    _POV_KEY = ("pov_snapshot", "self")

    def __init__(self) -> None:
        self._current: Dict[tuple[str, str], str] = {}
        self._revisions: Dict[tuple[str, str], int] = {}
        self._staged: Dict[tuple[str, str], tuple[str | None, int]] = {}
        self._pov_last_location: Any = None
        self._pov_last_step: int | None = None
        self._pov_staged: tuple[str, int, Any, int] | None = None

    def project(
        self,
        perception: AgentPerception,
        *,
        bootstrap: bool = False,
    ) -> list["SubjectMessage"]:
        messages = list(self._project_ledger(perception))
        pov_message = self._project_pov_snapshot(perception, bootstrap=bootstrap)
        if pov_message is not None:
            messages.append(pov_message)
        return messages

    def commit(self) -> None:
        for key, (digest, revision) in self._staged.items():
            if digest is None:
                self._current.pop(key, None)
            else:
                self._current[key] = digest
            self._revisions[key] = revision
        self._staged = {}
        if self._pov_staged is not None:
            digest, revision, location, step = self._pov_staged
            self._current[self._POV_KEY] = digest
            self._revisions[self._POV_KEY] = revision
            self._pov_last_location = location
            self._pov_last_step = step
            self._pov_staged = None

    def _project_ledger(
        self, perception: AgentPerception
    ) -> list["SubjectMessage"]:
        records = self._records(perception)
        next_keys = set(records)
        previous_keys = set(self._current) - {self._POV_KEY}
        staged: Dict[tuple[str, str], tuple[str | None, int]] = {}
        messages: list[SubjectMessage] = []

        for key in sorted(previous_keys - next_keys):
            category, ref = key
            revision = self._revisions.get(key, 0) + 1
            messages.append(SubjectMessage(
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
            staged[key] = (None, revision)

        for key in sorted(next_keys):
            category, ref = key
            payload = records[key]
            digest = self._digest(payload)
            if self._current.get(key) == digest:
                continue
            revision = self._revisions.get(key, 0) + 1
            messages.append(SubjectMessage(
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
            staged[key] = (digest, revision)

        self._staged = staged
        return messages

    def _project_pov_snapshot(
        self,
        perception: AgentPerception,
        *,
        bootstrap: bool,
    ) -> "SubjectMessage | None":
        location = (
            dict(perception.self_state or {}).get("location")
            if perception.self_state
            else None
        )
        step = int(perception.step)
        first_snapshot = self._pov_last_step is None
        location_changed = (
            not first_snapshot and location != self._pov_last_location
        )
        dormant = (
            not first_snapshot
            and (step - int(self._pov_last_step)) >= self.POV_DORMANT_STEPS
        )
        if not (bootstrap or first_snapshot or location_changed or dormant):
            return None

        body = {
            key: value
            for key, value in dict(perception.self_state or {}).items()
            if key in SUBJECT_BODY_STATE_FIELDS
        }
        visible_world = dict(perception.world_view or {})
        payload = {"self_body": body, "visible_world": visible_world}
        digest = self._digest(payload)
        revision = self._revisions.get(self._POV_KEY, 0) + 1
        message = SubjectMessage(
            message_id=f"ledger:pov_snapshot:self:r{revision}:{digest[:12]}",
            kind="ledger_update",
            step=step,
            payload={
                "category": "pov_snapshot",
                "ref": "self",
                "revision": revision,
                "reason": (
                    "bootstrap" if bootstrap
                    else "first_wake" if first_snapshot
                    else "location_changed" if location_changed
                    else "dormant_refresh"
                ),
                "record": payload,
            },
            priority=85,
            source_ref="self",
        )
        self._pov_staged = (digest, revision, location, step)
        return message

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
        cls._add_list(
            records,
            "schedule_commitment",
            (perception.private_schedule or {}).get("active", []),
            ("commitment_id",),
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


def build_subject_messages(
    perception: AgentPerception,
    *,
    limit: int = 32,
) -> list[SubjectMessage]:
    """Build this turn's event-like messages straight from Cognition's own
    ranked queues -- no separate dedup buffer needed.

    ``pending_world_event_records``/``pending_event_response_records`` are
    exactly what ``Cognition.acknowledge_world_events``/
    ``acknowledge_event_responses`` will clear once the turn succeeds, so a
    failed and retried turn simply recomputes the identical message set from
    the identical still-pending source of truth. ``world_signal``/
    ``director_signal`` are generated and consumed within the same step and
    never need cross-turn dedup.
    """

    # Cognition already classified each record's urgency (critical/direct/
    # ambient) via HostAttentionPolicy.message_urgency when it was queued;
    # this just carries that classification through to the wire message
    # instead of recomputing it.
    urgency_priority = {"critical": 95, "direct": 90, "ambient": 45}
    messages: list[SubjectMessage] = []
    for record in perception.private_cognition.get(
        "pending_world_event_records", []
    ) or []:
        if not isinstance(record, dict):
            continue
        event_id = _text(record.get("event_id"), 160)
        if not event_id:
            continue
        urgency = str(record.get("urgency") or "ambient")
        messages.append(SubjectMessage(
            message_id=f"stimulus:{event_id}",
            kind="stimulus",
            step=int(record.get("updated_step", perception.step) or perception.step),
            payload={
                key: value for key, value in record.items() if key != "urgency"
            },
            priority=urgency_priority.get(urgency, 45),
            source_ref=event_id,
            urgency=urgency,
        ))
    for record in perception.private_cognition.get(
        "pending_event_response_records", []
    ) or []:
        if not isinstance(record, dict):
            continue
        response_id = _text(record.get("response_id"), 260)
        if not response_id:
            continue
        urgency = str(record.get("urgency") or "ambient")
        messages.append(SubjectMessage(
            message_id=f"active:{response_id}",
            kind="active_observation_result",
            step=int(record.get("step", perception.step) or perception.step),
            payload={
                key: value for key, value in record.items() if key != "urgency"
            },
            priority=urgency_priority.get(urgency, 45),
            source_ref=response_id,
            urgency=urgency,
        ))
    for index, item in enumerate(perception.world_signals[-16:]):
        if not isinstance(item, dict):
            continue
        messages.append(SubjectMessage(
            message_id=_stable_message_id("signal", perception.step, index, item),
            kind="world_signal",
            step=int(perception.step),
            payload=dict(item),
            priority=_priority(item),
            # A GM proposal happening at this actor's own location this very
            # step is worth immediate, prominent attention -- it never
            # lingers to be picked up on some later, unrelated wake.
            urgency="critical",
        ))
    for index, item in enumerate(perception.director_signals[-4:]):
        if not isinstance(item, dict):
            continue
        messages.append(SubjectMessage(
            message_id=_stable_message_id("director", perception.step, index, item),
            kind="director_signal",
            step=int(perception.step),
            payload=dict(item),
            # Advisory, and deliberately quieter than a real world_signal or
            # ledger fact: it should be easy for a character to notice
            # without ever crowding out what actually happened.
            priority=40,
            urgency="ambient",
        ))
    ordered = sorted(
        messages,
        key=lambda item: (-int(item.priority), int(item.step), item.message_id),
    )
    return ordered[: max(0, int(limit))]


def build_subject_wake_packet(
    *,
    entity: Any,
    perception: AgentPerception,
    messages: Iterable[SubjectMessage],
    bootstrap: bool,
) -> Dict[str, Any]:
    identity = entity.get_component("Identity")
    all_messages = list(messages)
    critical_signals = [
        item.to_dict() for item in all_messages if item.urgency == "critical"
    ]
    ordinary_messages = [
        item.to_dict() for item in all_messages if item.urgency != "critical"
    ]
    packet = {
        "subject_protocol_version": 1,
        "subject_id": entity.id,
        "actor_name": perception.actor_name,
        "wake": {
            "step": int(perception.step),
            "activation_scope": perception.activation_scope,
            # No unconditional body/visible_world here: a full POV snapshot
            # only arrives as a ledger_update message (category
            # pov_snapshot), sent on bootstrap, on a location change, or
            # after a long-enough dormant stretch. Otherwise the subject
            # already holds the last snapshot and learns what changed from
            # stimulus/active_observation_result/ledger messages instead.
            "affordance_opportunities": list(perception.affordance_opportunities),
            "ongoing_actions": list(perception.ongoing_actions),
        },
        # Severe, unignorable consequences (see SubjectMessageUrgency) are
        # surfaced here, separate from routine content, so they are never
        # mistaken for background noise. Everything else -- direct and
        # ambient content, plus all ledger messages (which carry no urgency
        # at all) -- stays in the ordinary messages list.
        "critical_signals": critical_signals,
        "messages": ordinary_messages,
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
                "Host-verifiable records used for knowledge, schedules, "
                "registered goals and settlement. They are evidence or "
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
                    "visible_object | visible_actor | "
                    "relationship | navigation_problem"
                ),
                "source_ref": "required for adopt; copy a real ledger/world ref",
                "reason": "brief subjective reason; never a completion claim",
                "resolution_kind": (
                    "optional Host-verifiable watch such as reach_location, "
                    "possess_object, deliver_object, "
                    "verify_claim or communicate_event"
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
