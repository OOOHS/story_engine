import hashlib
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class GoalRecord(BaseModel):
    """One private character objective with optional host-verifiable locks."""

    goal_id: str
    title: str
    description: str = ""
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    status: Literal["active", "achieved", "failed", "abandoned"] = "active"
    completion_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    failure_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    created_step: int = 0
    refined_step: Optional[int] = None
    resolved_step: Optional[int] = None
    resolution_reason: str = ""
    origin: Literal["initial", "agent"] = "initial"
    source_kind: str = ""
    source_ref: str = ""


class GoalState(Component):
    """Host-registered goal watches resolved only from authoritative state.

    For legacy runtimes these records also guide Host candidate policy. For a
    persistent subject they do not constitute desire or attention: Hermes owns
    those internally and registers only goals whose progress, scheduling or
    completion should be observed by the Host. Natural-language registrations
    remain active until evidence is available; no runtime self-certifies success.
    """

    goals: Dict[str, GoalRecord] = Field(default_factory=dict)
    max_goals: int = Field(default=16, ge=0, le=64)

    @classmethod
    def from_initial(
        cls,
        titles: Iterable[Any] = (),
        structured: Iterable[Any] = (),
        *,
        created_step: int = 0,
    ) -> "GoalState":
        records: Dict[str, GoalRecord] = {}
        title_index: Dict[str, str] = {}
        for index, raw in enumerate(structured or []):
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            goal_id = cls._text(raw.get("goal_id"), 120)
            title = cls._text(raw.get("title"), 300)
            if not goal_id or not title or goal_id in records:
                continue
            record = GoalRecord(
                goal_id=goal_id,
                title=title,
                description=cls._text(raw.get("description"), 600),
                priority=raw.get("priority", 0.5),
                status="active",
                completion_conditions=cls._conditions(
                    raw.get("completion_conditions", [])
                ),
                failure_conditions=cls._conditions(raw.get("failure_conditions", [])),
                created_step=int(created_step),
            )
            records[goal_id] = record
            title_index[title.casefold()] = goal_id

        for index, raw in enumerate(titles or []):
            title = cls._text(raw, 300)
            if not title or title.casefold() in title_index:
                continue
            base_id = f"goal-{index + 1}"
            goal_id = base_id
            suffix = 2
            while goal_id in records:
                goal_id = f"{base_id}-{suffix}"
                suffix += 1
            records[goal_id] = GoalRecord(
                goal_id=goal_id,
                title=title,
                created_step=int(created_step),
            )
            title_index[title.casefold()] = goal_id
        return cls(goals=dict(list(records.items())[:16]))

    def advance_to(
        self,
        *,
        step: int,
        scene_state: Any,
        condition_matcher: Any = None,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        transitions: List[Dict[str, Any]] = []
        errors: List[str] = []
        if scene_state is None and condition_matcher is None:
            return transitions, errors
        for record in self.goals.values():
            if record.status != "active":
                continue
            try:
                matcher = condition_matcher or (
                    lambda condition: scene_state.matches_condition(condition)
                )
                completed = bool(record.completion_conditions) and all(
                    matcher(condition) for condition in record.completion_conditions
                )
                failed = bool(record.failure_conditions) and all(
                    matcher(condition) for condition in record.failure_conditions
                )
            except (TypeError, ValueError) as exc:
                errors.append(
                    f"goal condition evaluation failed: {record.goal_id}:"
                    f"{type(exc).__name__}:{exc}"
                )
                continue
            if completed and failed:
                errors.append(f"goal conditions are simultaneously true: {record.goal_id}")
                continue
            next_status = "achieved" if completed else "failed" if failed else ""
            if not next_status:
                continue
            record.status = next_status
            record.resolved_step = int(step)
            record.resolution_reason = (
                "authoritative completion conditions satisfied"
                if completed
                else "authoritative failure conditions satisfied"
            )
            transitions.append(
                {
                    "goal_id": record.goal_id,
                    "status": record.status,
                    "title": record.title,
                    "resolved_step": record.resolved_step,
                    "reason": record.resolution_reason,
                }
            )
        return transitions, errors

    def active_records(self) -> List[GoalRecord]:
        return sorted(
            (record for record in self.goals.values() if record.status == "active"),
            key=lambda record: (-record.priority, record.created_step, record.goal_id),
        )

    def adopt_agent_goal(
        self,
        *,
        title: Any,
        description: Any,
        source_kind: Any,
        source_ref: Any,
        priority: float,
        step: int,
        completion_conditions: Iterable[Dict[str, Any]] = (),
        failure_conditions: Iterable[Dict[str, Any]] = (),
    ) -> tuple[Dict[str, Any] | None, str]:
        clean_title = self._text(title, 300)
        clean_description = self._text(description, 600)
        clean_kind = self._text(source_kind, 40)
        clean_ref = self._text(source_ref, 160)
        if len(clean_title) < 2:
            return None, "agent goal title is required"
        if not clean_kind or not clean_ref:
            return None, "agent goal requires a validated source"
        if any(
            record.title.casefold() == clean_title.casefold()
            and not (
                clean_kind == "drive_need" and record.status != "active"
            )
            for record in self.goals.values()
        ):
            return None, "agent goal duplicates an existing goal"
        if any(
            record.origin == "agent"
            and record.source_kind == clean_kind
            and record.source_ref == clean_ref
            and not (
                clean_kind == "drive_need" and record.status != "active"
            )
            for record in self.goals.values()
        ):
            return None, "agent goal source has already produced a goal"
        active_agent_goals = [
            record
            for record in self.goals.values()
            if record.origin == "agent" and record.status == "active"
        ]
        if len(active_agent_goals) >= 3:
            return None, "agent goal active limit reached"
        if any(
            record.origin == "agent" and int(step) - record.created_step < 2
            for record in self.goals.values()
        ):
            return None, "agent goal adoption is cooling down"
        if len(self.goals) >= self.max_goals:
            removable = sorted(
                (
                    record
                    for record in self.goals.values()
                    if record.origin == "agent" and record.status != "active"
                ),
                key=lambda record: (
                    int(record.resolved_step or record.created_step),
                    record.goal_id,
                ),
            )
            if not removable:
                return None, "goal capacity reached"
            self.goals.pop(removable[0].goal_id, None)
        generation = f"|{int(step)}" if clean_kind == "drive_need" else ""
        digest = hashlib.sha256(
            (
                f"{clean_kind}|{clean_ref}|{clean_title.casefold()}"
                f"{generation}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        goal_id = f"agent-goal:{digest}"
        if goal_id in self.goals:
            return None, "agent goal already exists"
        record = GoalRecord(
            goal_id=goal_id,
            title=clean_title,
            description=clean_description,
            priority=min(1.0, max(0.0, float(priority))),
            created_step=int(step),
            origin="agent",
            source_kind=clean_kind,
            source_ref=clean_ref,
            completion_conditions=self._conditions(list(completion_conditions)),
            failure_conditions=self._conditions(list(failure_conditions)),
        )
        self.goals[goal_id] = record
        return {
            "goal_id": goal_id,
            "status": "adopted",
            "title": record.title,
            "created_step": record.created_step,
            "reason": f"agent adopted goal from {clean_kind}:{clean_ref}",
        }, ""

    def abandon_agent_goal(
        self,
        *,
        goal_id: Any,
        reason: Any,
        step: int,
    ) -> tuple[Dict[str, Any] | None, str]:
        clean_id = self._text(goal_id, 120)
        record = self.goals.get(clean_id)
        if record is None:
            return None, "agent goal does not exist"
        if record.origin != "agent":
            return None, "authored goals cannot be abandoned by an agent"
        if record.status != "active":
            return None, "only active agent goals can be abandoned"
        clean_reason = self._text(reason, 400)
        if not clean_reason:
            return None, "agent goal abandonment requires a reason"
        record.status = "abandoned"
        record.resolved_step = int(step)
        record.resolution_reason = clean_reason
        return {
            "goal_id": record.goal_id,
            "status": "abandoned",
            "title": record.title,
            "resolved_step": record.resolved_step,
            "reason": record.resolution_reason,
        }, ""

    def refine_agent_goal(
        self,
        *,
        goal_id: Any,
        step: int,
        completion_conditions: Iterable[Dict[str, Any]],
        failure_conditions: Iterable[Dict[str, Any]] = (),
    ) -> tuple[Dict[str, Any] | None, str]:
        clean_id = self._text(goal_id, 120)
        record = self.goals.get(clean_id)
        if record is None:
            return None, "agent goal does not exist"
        if record.origin != "agent":
            return None, "authored goals cannot be refined by an agent"
        if record.status != "active":
            return None, "only active agent goals can be refined"
        if record.completion_conditions or record.failure_conditions:
            return None, "agent goal already has authoritative resolution rules"
        completion = self._conditions(list(completion_conditions))
        failure = self._conditions(list(failure_conditions))
        if not completion:
            return None, "agent goal refinement requires a completion rule"
        record.completion_conditions = completion
        record.failure_conditions = failure
        record.refined_step = int(step)
        return {
            "goal_id": record.goal_id,
            "status": "refined",
            "title": record.title,
            "created_step": record.created_step,
            "refined_step": record.refined_step,
            "reason": "host compiled authoritative resolution rules",
        }, ""

    def get_private_snapshot(self) -> Dict[str, Any]:
        active = [self._public_record(record) for record in self.active_records()]
        history = [
            self._public_record(record)
            for record in self.goals.values()
            if record.status != "active"
        ]
        history.sort(
            key=lambda item: (
                int(item.get("resolved_step") or -1),
                str(item.get("goal_id", "")),
            )
        )
        return {
            "active": active,
            "recent_history": history[-12:],
            "active_count": len(active),
            "achieved_count": sum(
                record.status == "achieved" for record in self.goals.values()
            ),
            "failed_count": sum(
                record.status == "failed" for record in self.goals.values()
            ),
        }

    def restore_from(self, snapshot: "GoalState") -> None:
        self.goals = deepcopy(snapshot.goals)
        self.max_goals = snapshot.max_goals

    @staticmethod
    def _public_record(record: GoalRecord) -> Dict[str, Any]:
        # Exact locks stay on the host. Agents receive what they are pursuing
        # and what has been conclusively resolved, not a rule-engine exploit map.
        return {
            "goal_id": record.goal_id,
            "title": record.title,
            "description": record.description,
            "priority": record.priority,
            "status": record.status,
            "created_step": record.created_step,
            "refined_step": record.refined_step,
            "resolved_step": record.resolved_step,
            "resolution_reason": record.resolution_reason,
            "origin": record.origin,
            "source_kind": record.source_kind,
            "source_ref": record.source_ref,
            "has_completion_evidence_rule": bool(record.completion_conditions),
            "has_failure_evidence_rule": bool(record.failure_conditions),
        }

    @staticmethod
    def _conditions(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            deepcopy(item.model_dump() if hasattr(item, "model_dump") else item)
            for item in value[:24]
            if isinstance(item, dict) or hasattr(item, "model_dump")
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
