from copy import deepcopy
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class ObligationRecord(BaseModel):
    obligation_id: str
    title: str
    summary: str = ""
    creditor: Optional[str] = None
    due_step: int = Field(ge=0)
    grace_steps: int = Field(default=0, ge=0, le=100)
    wake_before_steps: int = Field(default=1, ge=0, le=100)
    pressure_need: Optional[str] = None
    due_pressure_delta: float = Field(default=0.1, ge=0.0, le=0.5)
    breach_pressure_delta: float = Field(default=0.2, ge=0.0, le=0.5)
    status: str = "scheduled"
    created_step: int = 0
    source_kind: str = "scenario"
    source_ref: str = ""
    resolution_reason: str = ""
    completion_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    delegation_policy: Literal["forbidden", "bilateral", "creditor_consent"] = (
        "creditor_consent"
    )
    delegated_from: Optional[str] = None
    delegated_to: Optional[str] = None
    delegation_reason: str = ""


class ObligationState(Component):
    """Private, evidence-resolved duties owned by one character."""

    obligations: Dict[str, ObligationRecord] = Field(default_factory=dict)
    max_obligations: int = Field(default=24, ge=0, le=100)

    @classmethod
    def from_initial(cls, items: Iterable[Any] = ()) -> "ObligationState":
        records: Dict[str, ObligationRecord] = {}
        for raw in items or []:
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            obligation_id = cls._text(raw.get("obligation_id"), 120)
            title = cls._text(raw.get("title"), 240)
            if not obligation_id or not title or obligation_id in records:
                continue
            conditions = raw.get("completion_conditions", [])
            if not isinstance(conditions, list):
                conditions = []
            records[obligation_id] = ObligationRecord(
                obligation_id=obligation_id,
                title=title,
                summary=cls._text(raw.get("summary"), 500),
                creditor=cls._text(raw.get("creditor"), 120) or None,
                due_step=raw.get("due_step", 0),
                grace_steps=raw.get("grace_steps", 0),
                wake_before_steps=raw.get("wake_before_steps", 1),
                pressure_need=cls._text(raw.get("pressure_need"), 80) or None,
                due_pressure_delta=raw.get("due_pressure_delta", 0.1),
                breach_pressure_delta=raw.get("breach_pressure_delta", 0.2),
                status="scheduled",
                created_step=raw.get("created_step", 0),
                source_kind=cls._text(raw.get("source_kind"), 80) or "scenario",
                source_ref=cls._text(raw.get("source_ref"), 240)
                or obligation_id,
                completion_conditions=[
                    condition.model_dump()
                    if hasattr(condition, "model_dump")
                    else deepcopy(condition)
                    for condition in conditions
                    if isinstance(condition, dict) or hasattr(condition, "model_dump")
                ],
                delegation_policy=raw.get("delegation_policy", "creditor_consent"),
            )
        return cls(obligations=records)

    def get_private_snapshot(self, current_step: int) -> Dict[str, Any]:
        active = []
        history = []
        for record in self.obligations.values():
            item = record.model_dump()
            item["steps_remaining"] = record.due_step - int(current_step)
            item["effective_status"] = self.effective_status(record, current_step)
            if record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
                history.append(item)
            else:
                active.append(item)
        active.sort(key=lambda item: (item["due_step"], item["obligation_id"]))
        history.sort(key=lambda item: (item["due_step"], item["obligation_id"]))
        return {
            "active": active,
            "recent_history": history[-12:],
            "due_count": sum(
                item["effective_status"] == "due" for item in active
            ),
        }

    def advance_to(
        self,
        step: int,
        drive_state: Any = None,
        scene_state: Any = None,
        plot_state: Any = None,
    ) -> List[Dict[str, Any]]:
        transitions = []
        for record in self.obligations.values():
            if record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
                continue
            if int(step) > record.due_step + record.grace_steps:
                if record.status == "scheduled":
                    self._apply_pressure(
                        record,
                        drive_state,
                        record.due_pressure_delta,
                        current_step=int(step),
                    )
                record.status = "breached"
                record.resolution_reason = "deadline and grace period elapsed"
                self._apply_pressure(
                    record,
                    drive_state,
                    record.breach_pressure_delta,
                    current_step=int(step),
                )
                transitions.append(
                    {"obligation_id": record.obligation_id, "status": "breached"}
                )
            elif (
                record.completion_conditions
                and scene_state
                and all(
                    scene_state.matches_condition(condition, plot_state=plot_state)
                    for condition in record.completion_conditions
                )
            ):
                record.status = "fulfilled"
                record.resolution_reason = "authoritative completion conditions satisfied"
                transitions.append(
                    {"obligation_id": record.obligation_id, "status": "fulfilled"}
                )
            elif int(step) >= record.due_step and record.status == "scheduled":
                record.status = "due"
                self._apply_pressure(
                    record,
                    drive_state,
                    record.due_pressure_delta,
                    current_step=int(step),
                )
                transitions.append(
                    {"obligation_id": record.obligation_id, "status": "due"}
                )
        return transitions

    def next_wakeup(self, current_step: int) -> Optional[ObligationRecord]:
        candidates = [
            record
            for record in self.obligations.values()
            if record.status not in {"fulfilled", "breached", "cancelled", "delegated"}
            and int(current_step) >= record.due_step - record.wake_before_steps
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda record: (record.due_step, record.obligation_id))
        return candidates[0]

    def restore_from(self, snapshot: "ObligationState") -> None:
        self.obligations = deepcopy(snapshot.obligations)
        self.max_obligations = snapshot.max_obligations

    @staticmethod
    def effective_status(record: ObligationRecord, current_step: int) -> str:
        if record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
            return record.status
        if int(current_step) > record.due_step + record.grace_steps:
            return "breached"
        if int(current_step) >= record.due_step:
            return "due"
        return "scheduled"

    @staticmethod
    def _apply_pressure(
        record: ObligationRecord,
        drive_state: Any,
        delta: float,
        *,
        current_step: int,
    ) -> None:
        if (
            drive_state
            and record.pressure_need
            and record.pressure_need in drive_state.needs
        ):
            drive_state.apply_need_delta(
                record.pressure_need,
                delta,
                provenance={
                    "source_kind": "obligation",
                    "source_ref": record.obligation_id,
                    "step": int(current_step),
                },
            )

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
