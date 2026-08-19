import hashlib
import json
import math
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Literal

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception


SubjectMessageKind = Literal[
    "stimulus",
    "active_observation_result",
    "task_result",
    "world_signal",
    "ledger_update",
    "ledger_retraction",
    # A Host-queued, non-authoritative nudge (e.g. an unrealized plot
    # thread trying to become salient). Never a fact and never a
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


@dataclass(frozen=True)
class IntentSignature:
    """Internal semantics used to reject wording-only option diversity."""

    motive_lens: str
    strategy: str
    stakes: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "IntentSignature":
        value = value if isinstance(value, dict) else {}
        motive_lens = _text(value.get("motive_lens"), 80)
        strategy = _text(value.get("strategy"), 120)
        raw_stakes = value.get("stakes", [])
        if not isinstance(raw_stakes, (list, tuple)):
            raw_stakes = []
        stakes = tuple(dict.fromkeys(
            item
            for raw in raw_stakes[:8]
            if (item := _text(raw, 80))
        ))
        if not motive_lens:
            raise ValueError("Hermes subject candidates require motive_lens")
        if not strategy:
            raise ValueError("Hermes subject candidates require strategy")
        return cls(motive_lens=motive_lens, strategy=strategy, stakes=stakes)

    def path_key(self, action: AgentAction) -> tuple[Any, ...]:
        return (
            self.strategy.casefold(),
            tuple(item.casefold() for item in self.stakes),
            action.kind,
            action.target.casefold(),
            action.affordance_id.casefold(),
            action.claim_id.casefold(),
            action.agreement_operation.casefold(),
            action.agreement_id.casefold(),
        )


@dataclass(frozen=True)
class SubjectActionOption:
    option_id: str
    action: AgentAction
    signature: IntentSignature
    utility: float = 0.0

    @classmethod
    def from_value(cls, value: Any, *, index: int) -> "SubjectActionOption":
        if not isinstance(value, dict):
            raise ValueError("Hermes subject candidate must be an object")
        action = AgentAction.from_value(value.get("action", value), strict=True)
        if not action.detail:
            raise ValueError("Hermes subject candidate has no executable action")
        signature_value = value.get("intent_signature", {})
        if isinstance(signature_value, dict) and "motive_lens" not in signature_value:
            signature_value = {
                **signature_value,
                "motive_lens": value.get("motive_lens", ""),
            }
        signature = IntentSignature.from_value(signature_value)
        try:
            utility = float(value.get("utility", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Hermes subject candidate utility must be numeric") from exc
        if not math.isfinite(utility):
            raise ValueError("Hermes subject candidate utility must be finite")
        option_id = _text(value.get("option_id"), 100) or f"option:{index}"
        return cls(
            option_id=option_id,
            action=action,
            signature=signature,
            utility=max(-20.0, min(20.0, utility)),
        )


@dataclass(frozen=True)
class SubjectChoice:
    selected: SubjectActionOption
    trace: Dict[str, Any] = field(default_factory=dict)


class GumbelSubjectSampler:
    """Actor-private true sampling with a replayable evaluation mode."""

    def __init__(self, seed: int | str | None = None) -> None:
        self._seed = str(seed if seed is not None else secrets.token_hex(32))
        self.seed_mode = "configured" if seed is not None else "random"
        self.seed_fingerprint = hashlib.sha256(
            self._seed.encode("utf-8")
        ).hexdigest()[:16]

    def choose(
        self,
        options: Iterable[SubjectActionOption],
        *,
        decision_id: str,
        temperature: float = 0.8,
    ) -> SubjectChoice:
        option_list = tuple(options)
        if not option_list:
            raise ValueError("Hermes subject sampling requires at least one option")
        self._validate_diversity(option_list)
        temperature = max(0.05, min(5.0, float(temperature)))
        by_lens: Dict[str, list[SubjectActionOption]] = {}
        for option in option_list:
            by_lens.setdefault(option.signature.motive_lens, []).append(option)
        lens_scores = []
        for lens, lens_options in by_lens.items():
            lens_utility = _log_mean_exp(item.utility for item in lens_options)
            uniform = self._uniform(decision_id, "lens", lens)
            gumbel = -math.log(-math.log(uniform))
            lens_scores.append((lens, lens_utility + temperature * gumbel, uniform))
        selected_lens, _, _ = max(
            lens_scores,
            key=lambda item: (item[1], item[0]),
        )
        scored = []
        for option in by_lens[selected_lens]:
            uniform = self._uniform(decision_id, "action", option.option_id)
            gumbel = -math.log(-math.log(uniform))
            sampled_score = option.utility + temperature * gumbel
            scored.append((option, sampled_score, uniform))
        selected, _, _ = max(
            scored,
            key=lambda item: (item[1], item[0].option_id),
        )
        return SubjectChoice(
            selected=selected,
            trace={
                "decision_id": decision_id,
                "method": "hierarchical_gumbel",
                "seed_mode": self.seed_mode,
                "seed_fingerprint": self.seed_fingerprint,
                "temperature": temperature,
                "selected_option_id": selected.option_id,
                "selected_motive_lens": selected_lens,
                "motive_lenses": [
                    {
                        "motive_lens": lens,
                        "sampled_score": round(sampled_score, 12),
                        "uniform_draw": round(uniform, 12),
                    }
                    for lens, sampled_score, uniform in lens_scores
                ],
                "options": [
                    {
                        "option_id": option.option_id,
                        "motive_lens": option.signature.motive_lens,
                        "strategy": option.signature.strategy,
                        "stakes": list(option.signature.stakes),
                        "action": option.action.to_dict(),
                        "utility": option.utility,
                        "sampled_score": round(sampled_score, 12),
                        "uniform_draw": round(uniform, 12),
                    }
                    for option, sampled_score, uniform in scored
                ],
            },
        )

    def _uniform(self, *parts: object) -> float:
        key = "|".join(str(part) for part in parts)
        digest = hashlib.sha256(
            f"{self._seed}|{key}".encode("utf-8")
        ).digest()
        integer = int.from_bytes(digest[:8], "big")
        return min(1.0 - 1e-15, max(1e-15, (integer + 0.5) / float(1 << 64)))

    @staticmethod
    def _validate_diversity(options: tuple[SubjectActionOption, ...]) -> None:
        option_ids = [item.option_id for item in options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("Hermes subject candidate option_id values must be unique")
        if len(options) == 1:
            return
        lenses = {item.signature.motive_lens.casefold() for item in options}
        if len(lenses) < 2:
            raise ValueError(
                "Hermes subject candidates must activate at least two motive lenses"
            )
        path_keys = {item.signature.path_key(item.action) for item in options}
        if len(path_keys) != len(options):
            raise ValueError(
                "Hermes subject candidates must represent distinct intent paths"
            )


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
            "direct": "Return one executable action when no deliberation is needed.",
            "deliberation": (
                "Otherwise return two or more candidates with option_id, utility, "
                "motive_lens, intent_signature.strategy, intent_signature.stakes, "
                "and action. The subject runtime samples internally and exposes "
                "only the selected action to the Host."
            ),
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


def _log_mean_exp(values: Iterable[float]) -> float:
    items = tuple(float(value) for value in values)
    if not items:
        return 0.0
    maximum = max(items)
    return maximum + math.log(
        sum(math.exp(item - maximum) for item in items) / len(items)
    )
