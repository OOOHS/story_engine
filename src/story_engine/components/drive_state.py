from copy import deepcopy
from typing import Any, Dict, Iterable

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class NeedMeter(BaseModel):
    pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    drift_per_turn: float = Field(default=0.0, ge=-1.0, le=1.0)
    critical_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    description: str = ""


class DriveState(Component):
    """Private, structured pressures that persist beneath character prose."""

    needs: Dict[str, NeedMeter] = Field(default_factory=dict)
    need_provenance: Dict[str, list[Dict[str, Any]]] = Field(default_factory=dict)
    risk_tolerance: float = Field(default=0.5, ge=0.0, le=1.0)
    last_advanced_step: int = -1
    # Counts only needs created at runtime via create_need, never those from
    # from_initial. This is what emergent_meter_budget is checked against.
    created_count: int = 0

    @classmethod
    def from_initial(
        cls,
        needs: Iterable[Any] = (),
        *,
        risk_tolerance: float = 0.5,
    ) -> "DriveState":
        meters: Dict[str, NeedMeter] = {}
        for raw in needs or []:
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            name = " ".join(str(raw.get("name", "")).split()).strip()[:80]
            if not name or name in meters:
                continue
            meters[name] = NeedMeter(
                pressure=raw.get("pressure", 0.0),
                drift_per_turn=raw.get("drift_per_turn", 0.0),
                critical_threshold=raw.get("critical_threshold", 0.8),
                description=" ".join(str(raw.get("description", "")).split()).strip()[:300],
            )
        return cls(needs=meters, risk_tolerance=risk_tolerance)

    def get_private_snapshot(self) -> Dict[str, Any]:
        ordered = sorted(
            self.needs.items(),
            key=lambda item: (-item[1].pressure, item[0]),
        )
        return {
            "risk_tolerance": self.risk_tolerance,
            "needs": {
                name: {
                    **meter.model_dump(),
                    "critical": meter.pressure >= meter.critical_threshold,
                }
                for name, meter in ordered
            },
            "highest_pressure_need": ordered[0][0] if ordered else None,
        }

    def apply_need_delta(
        self,
        need: str,
        delta: float,
        *,
        provenance: Dict[str, Any] | None = None,
    ) -> float:
        meter = self.needs.get(str(need))
        if meter is None:
            raise KeyError(f"unknown need: {need}")
        before = meter.pressure
        meter.pressure = min(1.0, max(0.0, meter.pressure + float(delta)))
        if meter.pressure != before and provenance:
            history = self.need_provenance.setdefault(str(need), [])
            history.append(
                {
                    **deepcopy(provenance),
                    "before": before,
                    "after": meter.pressure,
                    "delta": meter.pressure - before,
                }
            )
            del history[:-24]
        return meter.pressure

    def create_need(
        self,
        name: str,
        *,
        drift_per_turn: float = 0.0,
        critical_threshold: float = 0.8,
        description: str = "",
        provenance: Dict[str, Any] | None = None,
    ) -> bool:
        """Create a brand-new need at runtime.

        Pressure always starts at 0.0 regardless of caller input: a resolver
        cannot create an already-critical meter to force drama. The name must
        not already exist; renaming/overwriting an existing need is not
        creation and must go through apply_need_delta instead.
        """
        clean_name = " ".join(str(name).split()).strip()[:80]
        if not clean_name or clean_name in self.needs:
            return False
        self.needs[clean_name] = NeedMeter(
            pressure=0.0,
            drift_per_turn=drift_per_turn,
            critical_threshold=critical_threshold,
            description=" ".join(str(description).split()).strip()[:300],
        )
        self.created_count += 1
        if provenance:
            history = self.need_provenance.setdefault(clean_name, [])
            history.append(
                {
                    **deepcopy(provenance),
                    "before": 0.0,
                    "after": 0.0,
                    "delta": 0.0,
                    "created": True,
                }
            )
            del history[:-24]
        return True

    def advance_to(self, step: int) -> Dict[str, float]:
        target = int(step)
        if self.last_advanced_step < 0:
            self.last_advanced_step = target
            return {}
        elapsed = target - self.last_advanced_step
        if elapsed <= 0:
            return {}
        changed: Dict[str, float] = {}
        for name, meter in self.needs.items():
            before = meter.pressure
            meter.pressure = min(
                1.0,
                max(0.0, before + meter.drift_per_turn * elapsed),
            )
            if meter.pressure != before:
                changed[name] = meter.pressure
                history = self.need_provenance.setdefault(name, [])
                history.append(
                    {
                        "source_kind": "clock",
                        "source_ref": f"step:{target}",
                        "before": before,
                        "after": meter.pressure,
                        "delta": meter.pressure - before,
                    }
                )
                del history[:-24]
        self.last_advanced_step = target
        return changed

    def restore_from(self, snapshot: "DriveState") -> None:
        self.needs = deepcopy(snapshot.needs)
        self.need_provenance = deepcopy(snapshot.need_provenance)
        self.risk_tolerance = snapshot.risk_tolerance
        self.last_advanced_step = snapshot.last_advanced_step
        self.created_count = snapshot.created_count
