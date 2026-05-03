from typing import Dict, Any, List
from pydantic import Field
from src.story_engine.core.component import Component
from src.story_engine.scenarios.config import PlotEntityConfig


class PlotState(Component):
    """
    Stores the authoritative state of macro plots and their progress clocks.
    """
    plots: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def from_configs(cls, configs: List[PlotEntityConfig]) -> "PlotState":
        plots: Dict[str, Dict[str, Any]] = {}
        for item in configs:
            plots[item.plot_id] = {
                "title": item.title,
                "description": item.description,
                "clock": item.clock,
                "max_clock": item.max_clock,
                "current_stage": item.current_stage,
                "stages": [stage.model_dump() for stage in item.stages],
                "tags": list(item.tags),
            }
        return cls(plots=plots)

    def get_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {plot_id: dict(data) for plot_id, data in self.plots.items()}

    def get_pressure_packets(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        for plot_id, data in self.plots.items():
            stages = data.get("stages", [])
            stage_idx = min(data.get("current_stage", 0), max(len(stages) - 1, 0))
            stage = stages[stage_idx] if stages else {}
            packets.append(
                {
                    "plot_id": plot_id,
                    "title": data.get("title", plot_id),
                    "clock": data.get("clock", 0),
                    "max_clock": data.get("max_clock", 0),
                    "stage": stage.get("label", ""),
                    "summary": stage.get("summary", data.get("description", "")),
                    "pressure_hint": stage.get("pressure_hint", ""),
                    "tags": list(data.get("tags", [])),
                }
            )
        return packets

    def apply_updates(self, updates: List[Dict[str, Any]]) -> None:
        for update in updates or []:
            plot_id = update.get("plot_id")
            if not plot_id or plot_id not in self.plots:
                continue

            plot = self.plots[plot_id]
            advance = int(update.get("advance", 0))
            stage_shift = int(update.get("stage_shift", 0))
            note = update.get("note")

            plot["clock"] = min(plot.get("max_clock", 0), max(0, plot.get("clock", 0) + advance))
            stages = plot.get("stages", [])
            if stages:
                plot["current_stage"] = min(
                    len(stages) - 1,
                    max(0, plot.get("current_stage", 0) + stage_shift),
                )
            if note:
                plot["last_note"] = note
