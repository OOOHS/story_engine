from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

from pydantic import Field

from src.story_engine.core.component import Component
from src.story_engine.components.drive_state import NeedMeter


class PublicPressureState(Component):
    """公共叙事压力，不属于任何单一角色。

    用于承载"民怨"、"战争阴云"这类跨角色的集体压力。结构跟 DriveState
    类似，但简化掉私有性（risk_tolerance、need_provenance 的私有历史），
    强调可观测性和来源追踪。
    """

    pressures: Dict[str, NeedMeter] = Field(default_factory=dict)
    # 每个压力的来源记录，用于审计和衰减判断
    pressure_sources: Dict[str, list[Dict[str, Any]]] = Field(default_factory=dict)
    last_advanced_step: int = -1

    @classmethod
    def from_initial(cls, pressures: Iterable[Any] = ()) -> "PublicPressureState":
        meters: Dict[str, NeedMeter] = {}
        for raw in pressures or []:
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
        return cls(pressures=meters)

    def get_snapshot(self) -> Dict[str, Any]:
        ordered = sorted(
            self.pressures.items(),
            key=lambda item: (-item[1].pressure, item[0]),
        )
        return {
            "pressures": {
                name: {
                    **meter.model_dump(),
                    "critical": meter.pressure >= meter.critical_threshold,
                }
                for name, meter in ordered
            },
            "highest_pressure": ordered[0][0] if ordered else None,
        }

    def apply_delta(
        self,
        name: str,
        delta: float,
        *,
        source: Optional[Dict[str, Any]] = None,
    ) -> float:
        meter = self.pressures.get(str(name))
        if meter is None:
            raise KeyError(f"unknown public pressure: {name}")
        before = meter.pressure
        meter.pressure = min(1.0, max(0.0, meter.pressure + float(delta)))
        if meter.pressure != before and source:
            history = self.pressure_sources.setdefault(str(name), [])
            history.append(
                {
                    **deepcopy(source),
                    "before": before,
                    "after": meter.pressure,
                    "delta": meter.pressure - before,
                }
            )
            del history[:-24]
        return meter.pressure

    def create_pressure(
        self,
        name: str,
        *,
        drift_per_turn: float = 0.0,
        critical_threshold: float = 0.8,
        description: str = "",
        source: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """运行时创建新的公共压力。

        压力永远从 0.0 开始，防止 resolver 直接创建一个已经临界的状态。
        """
        clean_name = " ".join(str(name).split()).strip()[:80]
        if not clean_name or clean_name in self.pressures:
            return False
        self.pressures[clean_name] = NeedMeter(
            pressure=0.0,
            drift_per_turn=drift_per_turn,
            critical_threshold=critical_threshold,
            description=" ".join(str(description).split()).strip()[:300],
        )
        if source:
            history = self.pressure_sources.setdefault(clean_name, [])
            history.append(
                {
                    **deepcopy(source),
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
        for name, meter in self.pressures.items():
            before = meter.pressure
            meter.pressure = min(
                1.0,
                max(0.0, before + meter.drift_per_turn * elapsed),
            )
            if meter.pressure != before:
                changed[name] = meter.pressure
                history = self.pressure_sources.setdefault(name, [])
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

    def is_critical(self, name: str) -> bool:
        meter = self.pressures.get(str(name))
        return meter is not None and meter.pressure >= meter.critical_threshold

    def get_critical_pressures(self) -> list[str]:
        return [
            name
            for name, meter in self.pressures.items()
            if meter.pressure >= meter.critical_threshold
        ]
