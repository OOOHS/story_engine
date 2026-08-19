from typing import Dict, Any, List, Optional, Tuple
from pydantic import Field
from src.story_engine.core.component import Component
from src.story_engine.scenarios.config import PlotEntityConfig


RUNTIME_STORYLET_PREFIX = "runtime"
MAX_CANDIDATE_BEATS_PER_THREAD = 8


class PlotState(Component):
    """
    Stores the authoritative state of macro plots and their progress clocks.

    Each entry is a "thread": either authored up front via ScenarioConfig
    (``from_configs``), or opened at runtime via ``create_thread`` (the
    legislation-v2 entry point). Threads carry two extra pieces of state
    beyond the original clock/stage counter:

    - ``candidate_beats``: concrete, content- or host-derived stimuli this
      thread can currently cash out as (the role storylets played, now
      attached to the thread they advance instead of living in a separate,
      unconnected registry).
    - ``opened_reason`` / ``last_advanced_step`` / ``sunset_after_idle_steps``:
      provenance and a natural-decay budget, so a thread that nobody ever
      advances quietly sunsets instead of accumulating forever. Authored
      threads default ``sunset_after_idle_steps=None`` (never auto-sunset),
      so this is purely additive for existing scenarios.
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
                "opened_reason": "authored",
                "participants": [],
                "open_hooks": [],
                "candidate_beats": [],
                "last_advanced_step": -1,
                "sunset_after_idle_steps": None,
                "status": "active",
            }
        return cls(plots=plots)

    def get_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {plot_id: dict(data) for plot_id, data in self.plots.items()}

    def get_pressure_packets(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        for plot_id, data in self.plots.items():
            if data.get("status") == "sunset":
                continue
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
                    "candidate_beats": [
                        dict(beat) for beat in data.get("candidate_beats", [])
                    ],
                    "open_hooks": list(data.get("open_hooks", [])),
                }
            )
        return packets

    def apply_updates(
        self,
        updates: List[Dict[str, Any]],
        *,
        current_step: Optional[int] = None,
    ) -> None:
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
            if (advance or stage_shift) and current_step is not None:
                plot["last_advanced_step"] = int(current_step)

    # --- Runtime thread lifecycle (legislation v2) ---------------------
    #
    # This is deliberately just the primitive: it enforces the two hard
    # constraints the legislation design settled on (no permission
    # expansion, no retroactivity) and nothing else. Who is allowed to call
    # create_thread -- a deterministic detector, a proposer LLM, content
    # authoring -- is a policy decision for the caller, not for this class.
    # "Is this a good thread" is answered later by decay_idle_threads, not
    # by any check here: unrealized threads fade because nobody advances
    # them, not because a screening step rejected them.

    def create_thread(
        self,
        plot_id: str,
        title: str,
        description: str,
        *,
        opened_reason: str,
        current_step: int,
        participants: Optional[List[str]] = None,
        max_clock: int = 4,
        sunset_after_idle_steps: Optional[int] = 40,
    ) -> Dict[str, Any]:
        plot_id = str(plot_id or "").strip()
        if not plot_id:
            raise ValueError("create_thread requires a non-empty plot_id")
        if plot_id in self.plots:
            raise ValueError(f"plot_id already exists: {plot_id}")
        if not str(opened_reason or "").strip():
            raise ValueError("create_thread requires a non-empty opened_reason (provenance)")
        thread = {
            "title": str(title or plot_id),
            "description": str(description or ""),
            "clock": 0,
            "max_clock": max(1, int(max_clock)),
            "current_stage": 0,
            "stages": [],
            "tags": [],
            "opened_reason": str(opened_reason).strip(),
            "participants": list(participants or []),
            "open_hooks": [],
            "candidate_beats": [],
            # Never retroactive: a thread's history starts counting from the
            # step it was opened, it cannot claim credit for progress that
            # supposedly happened before it existed.
            "last_advanced_step": int(current_step),
            "sunset_after_idle_steps": (
                int(sunset_after_idle_steps) if sunset_after_idle_steps is not None else None
            ),
            "status": "active",
        }
        self.plots[plot_id] = thread
        return dict(thread)

    def register_candidate_beat(self, plot_id: str, beat: Dict[str, Any]) -> bool:
        """Attach a concrete, cashable stimulus to a thread.

        Returns False (no-op) for an unknown or sunset thread, or a beat_id
        already registered on this thread -- callers should treat this as
        "nothing to do", not as an error.
        """
        plot = self.plots.get(str(plot_id or ""))
        if not plot or plot.get("status") == "sunset":
            return False
        beat_id = str((beat or {}).get("beat_id", "")).strip()
        if not beat_id:
            raise ValueError("candidate beat requires beat_id")
        existing_ids = {
            str(item.get("beat_id", "")) for item in plot.get("candidate_beats", [])
        }
        if beat_id in existing_ids:
            return False
        plot.setdefault("candidate_beats", []).append(dict(beat))
        return True

    def consume_beat(
        self,
        plot_id: str,
        beat_id: str,
        *,
        current_step: Optional[int] = None,
    ) -> bool:
        plot = self.plots.get(str(plot_id or ""))
        if not plot:
            return False
        beats = plot.get("candidate_beats", [])
        remaining = [item for item in beats if str(item.get("beat_id", "")) != str(beat_id)]
        consumed = len(remaining) != len(beats)
        plot["candidate_beats"] = remaining
        if consumed and current_step is not None:
            plot["last_advanced_step"] = int(current_step)
            # Authored clocks still move only via causal plot rules. Runtime
            # threads have no authored rules, so realizing a beat is the host
            # edge that proves the thread is not idle.
            if str(plot.get("opened_reason", "")).strip() not in {"", "authored"}:
                plot["clock"] = min(
                    int(plot.get("max_clock", 0) or 0),
                    max(0, int(plot.get("clock", 0) or 0) + 1),
                )
        return consumed

    @staticmethod
    def runtime_storylet_id(plot_id: str, beat_id: str) -> str:
        return f"{RUNTIME_STORYLET_PREFIX}:{plot_id}:{beat_id}"

    @staticmethod
    def parse_runtime_storylet_id(storylet_id: str) -> Optional[Tuple[str, str]]:
        text = str(storylet_id or "").strip()
        prefix = f"{RUNTIME_STORYLET_PREFIX}:"
        if not text.startswith(prefix):
            return None
        rest = text[len(prefix):]
        plot_id, separator, beat_id = rest.partition(":")
        if not separator or not plot_id.strip() or not beat_id.strip():
            return None
        return plot_id.strip(), beat_id.strip()

    def apply_beat_proposals(
        self,
        proposals: List[Dict[str, Any]],
        *,
        current_step: int,
        known_actors: Optional[set] = None,
    ) -> List[str]:
        """Soft-apply compiled GM beat proposals. Never raises."""
        skipped: List[str] = []
        actors = {str(item).strip() for item in (known_actors or set()) if str(item).strip()}
        for index, proposal in enumerate(proposals or []):
            if not isinstance(proposal, dict):
                skipped.append(f"plot_beat_proposals[{index}]:not_an_object")
                continue
            plot_id = str(proposal.get("plot_id", "")).strip()
            beat_id = str(proposal.get("beat_id", "")).strip()
            if not plot_id or not beat_id:
                skipped.append(f"plot_beat_proposals[{index}]:missing_id")
                continue
            open_thread = proposal.get("open_thread")
            if isinstance(open_thread, dict):
                if plot_id in self.plots:
                    skipped.append(f"plot_beat_proposals[{index}]:plot_exists")
                    continue
                participants = [
                    str(item).strip()
                    for item in (open_thread.get("participants") or [])
                    if str(item).strip() and (not actors or str(item).strip() in actors)
                ]
                try:
                    self.create_thread(
                        plot_id,
                        str(open_thread.get("title", "") or plot_id),
                        str(open_thread.get("description", "")),
                        opened_reason=str(open_thread.get("opened_reason", "")),
                        current_step=int(current_step),
                        participants=participants,
                    )
                except ValueError:
                    skipped.append(f"plot_beat_proposals[{index}]:open_thread")
                    continue
            elif plot_id not in self.plots:
                skipped.append(f"plot_beat_proposals[{index}]:unknown_plot")
                continue
            plot = self.plots.get(plot_id) or {}
            if plot.get("status") == "sunset":
                skipped.append(f"plot_beat_proposals[{index}]:sunset")
                continue
            existing = plot.get("candidate_beats", []) or []
            if len(existing) >= MAX_CANDIDATE_BEATS_PER_THREAD:
                skipped.append(f"plot_beat_proposals[{index}]:beat_cap")
                continue
            beat = {
                "beat_id": beat_id,
                "intent": str(proposal.get("intent", "")).strip(),
                "kind": str(proposal.get("kind", "environment")).strip(),
                "conditions": [
                    dict(item)
                    for item in (proposal.get("conditions") or [])
                    if isinstance(item, dict)
                ],
                "one_shot": bool(proposal.get("one_shot", True)),
                "effect": dict(proposal.get("effect") or {})
                if isinstance(proposal.get("effect"), dict)
                else {},
                "opened_step": int(current_step),
            }
            if not self.register_candidate_beat(plot_id, beat):
                skipped.append(f"plot_beat_proposals[{index}]:duplicate_or_missing")
        return skipped

    def add_open_hook(self, plot_id: str, hook: str) -> bool:
        plot = self.plots.get(str(plot_id or ""))
        hook = str(hook or "").strip()
        if not plot or not hook:
            return False
        hooks = plot.setdefault("open_hooks", [])
        if hook in hooks:
            return False
        hooks.append(hook)
        return True

    def resolve_open_hook(self, plot_id: str, hook: str) -> bool:
        plot = self.plots.get(str(plot_id or ""))
        if not plot:
            return False
        hooks = plot.get("open_hooks", [])
        remaining = [item for item in hooks if item != hook]
        resolved = len(remaining) != len(hooks)
        plot["open_hooks"] = remaining
        return resolved

    def decay_idle_threads(self, current_step: int) -> List[str]:
        """Sunset runtime threads nobody has advanced within their budget.

        This is the "unrealized proposals fade on their own" half of the
        legislation v2 design: a thread earns no lasting authority just by
        being proposed, only by being advanced through committed world
        facts. Authored plots (``sunset_after_idle_steps=None``) are exempt,
        so this never changes behavior for existing scenario content.
        """
        decayed: List[str] = []
        for plot_id, plot in self.plots.items():
            if plot.get("status") == "sunset":
                continue
            budget = plot.get("sunset_after_idle_steps")
            if budget is None:
                continue
            last_advanced = int(plot.get("last_advanced_step", -1))
            if last_advanced < 0:
                continue
            if int(current_step) - last_advanced > int(budget):
                plot["status"] = "sunset"
                plot["candidate_beats"] = []
                decayed.append(plot_id)
        return decayed
