"""Host-owned attention policy for character cognition.

The policy consumes only committed event facts and host time.  Neither an
Agent runtime nor a semantic resolver may provide salience, urgency, aging or
capacity decisions.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence, TypeVar


ATTENTION_CAPACITY = 40
ATTENTION_DELIVERY_LIMIT = 20
ATTENTION_FAIRNESS_RESERVE = 4
ATTENTION_AGING_INTERVAL = 4

# A world_event whose own base priority (kind-only, before any waiting boost)
# clears this line carries severe, unignorable consequences on its own merit
# (breach/destroy/alarm/missed/route_closed) -- regardless of whether this
# particular actor happens to be its target. This is what makes a message
# "critical" rather than "ambient"; it has nothing to do with whether the
# actor is currently scheduled foreground or background.
ATTENTION_CRITICAL_PRIORITY_THRESHOLD = 80

# An ambient item that has waited this many steps still gets pushed through
# rather than left to the ordinary aging boost. This bounds worst-case
# latency for actors with a large background interval and no other trigger:
# a witnessed ambient event still reaches them within a short, deterministic
# window (tuned for anti-starvation, not for being itself urgent).
ATTENTION_AMBIENT_MAX_WAIT_STEPS = 2

RecordT = TypeVar("RecordT")


class HostAttentionPolicy:
    """One deterministic catalog and queue policy for all passive stimuli."""

    @staticmethod
    def event_priority(fact: Any, actor: str) -> int:
        kind = str(getattr(fact, "kind", "") or "").strip().lower()
        metadata = getattr(fact, "metadata", {}) or {}
        changed_paths = {
            str(path).strip().lower()
            for path in metadata.get("changed_paths", []) or []
        }
        if "breach" in kind or "destroy" in kind:
            priority = 95
        elif kind == "route_closed":
            priority = 85
        elif kind == "scene_state_changed" and "alarm" in changed_paths:
            priority = 90
        elif "missed" in kind:
            priority = 80
        elif kind == "exchange_completed":
            priority = 70
        elif kind == "route_opened":
            priority = 60
        elif kind.startswith("object_") and kind != "object_state_changed":
            priority = 55
        elif kind == "object_state_changed":
            priority = 50
        elif kind == "action_interrupted":
            # Someone breaking off mid-action is routine outward news, on a par
            # with watching them walk away. It must also stay far below
            # ATTENTION_CRITICAL_PRIORITY_THRESHOLD: if witnessing one
            # interruption were itself critical, one alarm would preempt every
            # co-located actor, whose interruptions would preempt the next ring
            # of witnesses, and a single event would stall the whole scene.
            priority = 30
        elif kind == "actor_moved":
            priority = 25
        elif kind == "scene_phase_changed":
            priority = 20
        else:
            priority = 50
        if actor in set(getattr(fact, "subjects", []) or []):
            priority += 5
        return HostAttentionPolicy.clamp(priority)

    @staticmethod
    def response_priority(response_kind: Any) -> int:
        return {
            "apologize": 90,
            "accuse": 90,
            "request": 90,
            "explain": 85,
            "forgive": 85,
            "report": 75,
            "acknowledge": 65,
        }.get(str(response_kind or "report").strip().lower(), 75)

    @staticmethod
    def clamp(value: Any, default: int = 50) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def ranked_ids(
        cls,
        attention_ids: Iterable[str],
        records: Mapping[str, RecordT],
        *,
        current_step: int | None = None,
    ) -> list[str]:
        unique = list(dict.fromkeys(str(item) for item in attention_ids if str(item)))
        now = cls._current_step(records, current_step)
        return sorted(
            unique,
            key=lambda attention_id: cls._sort_key(
                attention_id,
                records.get(attention_id),
                now,
            ),
        )

    @classmethod
    def retain_ids(
        cls,
        ranked_ids: Sequence[str],
        records: Mapping[str, RecordT],
        *,
        capacity: int = ATTENTION_CAPACITY,
    ) -> list[str]:
        """Keep important stimuli plus a small deterministic oldest-first reserve.

        The reserve prevents a continuous stream from erasing every long-waiting
        low-value item before aging can make it schedulable.  It does not change
        the delivery order and can never consume more than four queue slots.
        """

        ordered = list(ranked_ids)
        if len(ordered) <= capacity:
            return ordered
        reserve_size = min(ATTENTION_FAIRNESS_RESERVE, capacity)
        primary_size = capacity - reserve_size
        retained = list(ordered[:primary_size])
        retained_set = set(retained)
        remainder = [item for item in ordered if item not in retained_set]
        oldest = sorted(
            remainder,
            key=lambda attention_id: (
                int(getattr(records.get(attention_id), "step", 0) or 0),
                attention_id,
            ),
        )[:reserve_size]
        retained.extend(oldest)
        retained_set = set(retained)
        return [item for item in ordered if item in retained_set]

    @classmethod
    def combined_ranked(
        cls,
        groups: Iterable[tuple[str, Iterable[str], Mapping[str, RecordT]]],
        *,
        current_step: int | None = None,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str, RecordT | None]] = []
        all_records: dict[str, RecordT] = {}
        for kind, attention_ids, records in groups:
            for attention_id in dict.fromkeys(
                str(item) for item in attention_ids if str(item)
            ):
                record = records.get(attention_id)
                candidates.append((kind, attention_id, record))
                if record is not None:
                    all_records[f"{kind}:{attention_id}"] = record
        now = cls._current_step(all_records, current_step)
        return [
            (kind, attention_id)
            for kind, attention_id, _ in sorted(
                candidates,
                key=lambda item: (
                    *cls._sort_key(item[1], item[2], now)[:-1],
                    item[0],
                    item[1],
                ),
            )
        ]

    @classmethod
    def effective_priority(
        cls,
        record: RecordT | None,
        current_step: int,
    ) -> tuple[int, int, int]:
        """Return ``(effective_priority, boost, step)`` for one record.

        ``effective_priority`` already includes the deterministic waiting
        boost, so a long-queued low-priority item can eventually earn the
        same off-schedule wake a fresh high-priority one gets immediately.
        """
        priority = cls.clamp(getattr(record, "priority", 0), default=0)
        step = int(getattr(record, "step", 0) or 0)
        age = max(0, int(current_step) - step)
        boost = min(100 - priority, age // ATTENTION_AGING_INTERVAL)
        return priority + boost, boost, step

    @classmethod
    def message_urgency(
        cls,
        kind: str,
        record: RecordT | None,
        current_step: int,
    ) -> str:
        """Classify one pending item's urgency: ``"critical"``, ``"direct"``
        or ``"ambient"``.

        This is a property of the message itself -- what happened, and to
        whom -- not of whether the actor receiving it happens to be
        scheduled foreground or background right now. It answers exactly two
        questions: should this force an off-schedule wake, and how
        prominently should it be placed once delivered.

        - ``"critical"``: the event's own kind carries severe,
          unignorable consequences on its own merit (breach/destroy/alarm/
          missed/route_closed -- the same high bucket
          ``ATTENTION_CRITICAL_PRIORITY_THRESHOLD`` names). Always forces a
          wake and is meant to be surfaced most prominently in the wake
          packet, separate from routine content.
        - ``"direct"``: either an ``event_response`` (someone
          apologized/explained/asked/accused this actor personally) or a
          ``world_event`` this actor personally caused or experienced
          (``record.self_witnessed``). Always forces a wake too, but as
          ordinary content -- its urgency is "this will not be missed", not
          "drop everything".
        - ``"ambient"``: a passively witnessed or reported routine world
          event. Never forces a wake by itself; it waits in the ranked queue
          for the actor's next activation for any reason. Once it has
          waited ``ATTENTION_AMBIENT_MAX_WAIT_STEPS``, it is still pushed
          through as ``"direct"`` so a background actor with no other
          trigger is not starved forever.
        """
        if kind == "event_response":
            return "direct"
        if record is None:
            return "ambient"
        if getattr(record, "self_witnessed", False):
            return "direct"
        base_priority = cls.clamp(getattr(record, "priority", 0), default=0)
        if base_priority >= ATTENTION_CRITICAL_PRIORITY_THRESHOLD:
            return "critical"
        _effective, _boost, step = cls.effective_priority(record, current_step)
        age = max(0, int(current_step) - step)
        if age >= ATTENTION_AMBIENT_MAX_WAIT_STEPS:
            return "direct"
        return "ambient"

    @classmethod
    def _sort_key(
        cls,
        attention_id: str,
        record: RecordT | None,
        current_step: int,
    ) -> tuple[int, int, int, str]:
        effective, boost, step = cls.effective_priority(record, current_step)
        # A boost wins an effective-priority tie, while equally fresh records
        # retain the existing newer-first behavior.
        return (-effective, -boost, -step, attention_id)

    @staticmethod
    def _current_step(
        records: Mapping[str, RecordT], current_step: int | None
    ) -> int:
        if current_step is not None:
            return int(current_step)
        return max(
            (int(getattr(record, "step", 0) or 0) for record in records.values()),
            default=0,
        )
