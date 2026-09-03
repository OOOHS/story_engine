from copy import deepcopy
from dataclasses import dataclass, field
import heapq
from typing import Any, Dict, List, Set

from src.story_engine.agents.actions import AgentAction


@dataclass(order=True)
class ScheduledAction:
    completes_at: int
    sequence: int
    event_id: str = field(compare=False)
    actor: str = field(compare=False)
    starts_at: int = field(compare=False)
    duration: int = field(compare=False)
    proposal: Dict[str, Any] = field(compare=False, default_factory=dict)
    host_metadata: Dict[str, Any] = field(compare=False, default_factory=dict)
    # Set on whatever an actor schedules right after one of her actions was
    # preempted. Without it a character standing in a stream of critical
    # signals would restart and lose an action every step, never completing
    # anything. See ActionEventQueue.preempt.
    preempt_immune: bool = field(compare=False, default=False)


class ActionDurationPolicy:
    """Environment-owned coarse durations for the five action kinds.

    The values are intentionally simple. They establish semi-Markov/event
    semantics without asking agents or the semantic GM to award themselves
    shorter actions.
    """

    DEFAULTS = {
        "observe": 1,
        "move": 2,
        "interact": 2,
        "communicate": 1,
        "wait": 1,
    }

    def duration_for(self, action: AgentAction) -> int:
        return int(self.DEFAULTS.get(action.kind, 2))


class ActionEventQueue:
    """Session-local discrete-event queue for character action completions."""

    def __init__(
        self,
        duration_policy: ActionDurationPolicy | None = None,
        *,
        start_time: int = 0,
    ) -> None:
        self.current_time = max(0, int(start_time))
        self._duration_policy = duration_policy or ActionDurationPolicy()
        self._heap: List[ScheduledAction] = []
        self._sequence = 0
        self._busy: Dict[str, ScheduledAction] = {}
        self._owed_immunity: Set[str] = set()

    def is_busy(self, actor: str) -> bool:
        event = self._busy.get(str(actor))
        return bool(event and event.completes_at > self.current_time)

    def busy_until(self, actor: str) -> int | None:
        event = self._busy.get(str(actor))
        return int(event.completes_at) if event else None

    def pending_for(self, actor: str) -> Dict[str, Any]:
        event = self._busy.get(str(actor))
        if not event:
            return {}
        return self._event_snapshot(event)

    def schedule(
        self,
        proposal: Dict[str, Any],
        *,
        host_metadata: Dict[str, Any] | None = None,
    ) -> ScheduledAction:
        actor = self._text(proposal.get("actor"), 120)
        if not actor:
            raise ValueError("scheduled action requires actor")
        if actor != "World" and self.is_busy(actor):
            raise ValueError(f"actor already has an action in progress: {actor}")
        action = AgentAction.from_value(
            proposal.get("action") or proposal.get("intent", "")
        )
        duration = self._duration_policy.duration_for(action)
        immune = actor in self._owed_immunity
        self._owed_immunity.discard(actor)
        self._sequence += 1
        event = ScheduledAction(
            completes_at=self.current_time + max(1, int(duration)),
            sequence=self._sequence,
            event_id=f"action:{self._sequence}",
            actor=actor,
            starts_at=self.current_time,
            duration=max(1, int(duration)),
            proposal={
                **deepcopy(proposal),
                "intent": action.detail,
                "action": action.to_dict(),
                "action_kind": action.kind,
                "action_target": action.target,
            },
            host_metadata=deepcopy(host_metadata or {}),
            preempt_immune=immune,
        )
        heapq.heappush(self._heap, event)
        if actor != "World":
            self._busy[actor] = event
        return event

    def preempt(self, actor: str, *, reason: str) -> Dict[str, Any] | None:
        """Abort one actor's in-flight action and return an interruption receipt.

        Returns ``None`` when there is nothing in flight or the action is
        immune, in which case the actor stays busy exactly as before.

        Two properties make this safe for a state-authoritative engine. The
        decision to preempt is taken from committed Host state at a step
        boundary, never from how long an agent runtime happened to take, so
        replaying a snapshot aborts the same actions. And the receipt is
        deliberately narrow: an aborted action produces none of its intended
        effects, so there is no partial progress to invent, and the actor's
        private ``intent`` detail and ``thought`` stay out of it because
        witnesses are only ever entitled to the outward action kind and target.
        """
        name = self._text(actor, 120)
        event = self._busy.get(name)
        if event is None or event.completes_at <= self.current_time:
            return None
        if event.preempt_immune:
            return None
        self._heap = [item for item in self._heap if item is not event]
        heapq.heapify(self._heap)
        self._busy.pop(name, None)
        self._owed_immunity.add(name)
        action = event.proposal.get("action", {})
        return {
            "event_id": event.event_id,
            "actor": event.actor,
            "action_kind": self._text(
                event.proposal.get("action_kind") or action.get("kind"), 40
            ),
            "action_target": self._text(
                event.proposal.get("action_target") or action.get("target"), 120
            ),
            "location": self._text(event.proposal.get("location"), 160),
            "started_at": event.starts_at,
            "interrupted_at": self.current_time,
            "planned_completion": event.completes_at,
            "duration": event.duration,
            "reason": self._text(reason, 200),
        }

    def pop_next_batch(self) -> List[Dict[str, Any]]:
        if not self._heap:
            self.current_time += 1
            return []
        next_time = int(self._heap[0].completes_at)
        self.current_time = max(self.current_time, next_time)
        due: List[ScheduledAction] = []
        while self._heap and int(self._heap[0].completes_at) == next_time:
            due.append(heapq.heappop(self._heap))
        completed: List[Dict[str, Any]] = []
        for event in sorted(due, key=lambda item: (item.actor, item.sequence)):
            if self._busy.get(event.actor) is event:
                self._busy.pop(event.actor, None)
            completed.append(
                {
                    **deepcopy(event.proposal),
                    "event_id": event.event_id,
                    "action_phase": "completed",
                    "action_started_at": event.starts_at,
                    "action_completed_at": event.completes_at,
                    "action_duration": event.duration,
                    "proposal_batch_step": event.completes_at,
                    # Removed by ActionSchedulingSystem before the completed
                    # proposal becomes semantic-GM input. It exists only to
                    # carry private Host policy evidence across event time.
                    "_host_metadata": deepcopy(event.host_metadata),
                }
            )
        return completed

    def snapshot(self) -> Dict[str, Any]:
        return {
            "current_time": self.current_time,
            "pending": [
                self._event_snapshot(event)
                for event in sorted(self._heap)
            ],
        }

    def checkpoint(self) -> Any:
        """Opaque in-process snapshot preserving heap/busy object identity."""
        return deepcopy(
            (
                self.current_time,
                self._heap,
                self._sequence,
                self._busy,
                self._owed_immunity,
            )
        )

    def restore(self, checkpoint: Any) -> None:
        current_time, heap, sequence, busy, owed_immunity = deepcopy(checkpoint)
        self.current_time = int(current_time)
        self._heap = heap
        self._sequence = int(sequence)
        self._busy = busy
        self._owed_immunity = set(owed_immunity)

    @staticmethod
    def _event_snapshot(event: ScheduledAction) -> Dict[str, Any]:
        return {
            "event_id": event.event_id,
            "actor": event.actor,
            "starts_at": event.starts_at,
            "completes_at": event.completes_at,
            "duration": event.duration,
            "action": deepcopy(event.proposal.get("action", {})),
            "location": event.proposal.get("location"),
        }

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
