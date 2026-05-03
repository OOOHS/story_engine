import copy
import io
from contextlib import redirect_stdout
from threading import Lock
from typing import Any, Dict, List, Optional

from src.story_engine.session import create_session, Session
from src.story_engine.scenarios.config import ScenarioConfig


class WebGameAdapter:
    """
    Thin application adapter that turns a Session into a browser-friendly JSON API.
    The engine remains unaware of HTTP, templates, and frontend concerns.
    """

    def __init__(self, scenario: ScenarioConfig, title: Optional[str] = None):
        self._scenario = scenario
        self._title = title or scenario.name
        self._lock = Lock()
        self._session: Session
        self._history: List[Dict[str, Any]]
        self._boot_log = ""
        self._reset_locked()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._build_state_payload()

    def submit_turn(self, command: str = "", inject_event: str = "") -> Dict[str, Any]:
        command = (command or "").strip()
        inject_event = (inject_event or "").strip()

        with self._lock:
            overrides: Dict[str, str] = {}
            player_name = self._session.player_character_name
            if command and player_name:
                overrides[player_name] = command

            inject_events = [inject_event] if inject_event else None
            phase_trace: Dict[str, Any] = {}

            def capture_phase(phase_name: str, context: Dict[str, Any], entities: Dict[str, Any]) -> None:
                del entities
                if phase_name == "InputSystem":
                    player_name_local = context.get("player_name")
                    player_intent = next(
                        (
                            item for item in context.get("intents", [])
                            if isinstance(item, dict) and item.get("actor") == player_name_local
                        ),
                        None,
                    )
                    if isinstance(player_intent, dict):
                        phase_trace["player_intent"] = str(player_intent.get("intent", "")).strip()
                        phase_trace["player_intent_source"] = str(player_intent.get("source", "")).strip()
                if phase_name == "RenderingSystem":
                    phase_trace["rendered_text"] = context.get("rendered_text", "")

            with redirect_stdout(io.StringIO()):
                self._session.run_step(
                    overrides=overrides,
                    inject_events=inject_events,
                    on_phase_done=capture_phase,
                )

            step_entry = self._build_history_entry(
                phase_trace=phase_trace,
                player_command=command,
                inject_event=inject_event,
            )
            self._history.append(step_entry)
            self._history = self._history[-40:]
            return self._build_state_payload(last_step=step_entry)

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._reset_locked()
            return self._build_state_payload()

    def _reset_locked(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self._session = create_session(self._scenario)
        self._boot_log = output.getvalue().strip()
        self._history = [
            self._to_public_entry({
                "kind": "prologue",
                "step": 0,
                "title": "开场",
                "narration": self._scenario.initial_state,
                "intents": [],
                "simulation_result": {},
                "active_storylets": [],
                "director_packet": {},
                "state_snapshot": self._get_scene_snapshot(),
                "spawned_characters": [],
                "debug_log": self._boot_log,
            })
        ]

    def _build_history_entry(
        self,
        phase_trace: Dict[str, Any],
        player_command: str,
        inject_event: str,
    ) -> Dict[str, Any]:
        narration = phase_trace.get("rendered_text") or "局面没有显著变化。"
        step = self._session.step_count

        return self._to_public_entry({
            "kind": "turn",
            "step": step,
            "title": f"第 {step} 步",
            "player_command": player_command or phase_trace.get("player_intent", ""),
            "player_command_source": "manual" if player_command else (phase_trace.get("player_intent_source", "") or "none"),
            "inject_event": inject_event,
            "narration": narration,
        })

    def _to_public_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        public_entry = {
            "kind": entry.get("kind", "turn"),
            "step": entry.get("step", 0),
            "title": entry.get("title", ""),
            "narration": entry.get("narration", ""),
        }
        player_command = (entry.get("player_command") or "").strip()
        inject_event = (entry.get("inject_event") or "").strip()
        if public_entry["kind"] == "turn":
            public_entry["player_command"] = player_command or "本轮你没有明确表态，也没有明显动作。"
            public_entry["player_command_source"] = str(entry.get("player_command_source", "none")).strip() or "none"
        if inject_event:
            public_entry["inject_event"] = inject_event
        return public_entry

    def _build_state_payload(self, last_step: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        player_name = self._session.player_character_name
        player_role = self._get_player_role(player_name)

        return {
            "title": self._title,
            "scenario": {
                "name": self._scenario.name,
                "description": self._scenario.description,
                "initial_state": self._scenario.initial_state,
            },
            "player": {
                "name": player_name,
                "role": player_role,
                "available": bool(player_name),
            },
            "step_count": self._session.step_count,
            "history": copy.deepcopy(self._history),
            "last_step": copy.deepcopy(last_step) if last_step else None,
        }

    def _get_player_role(self, player_name: Optional[str]) -> str:
        if not player_name:
            return ""
        for character in self._scenario.characters:
            if character.name == player_name:
                return character.role
        return ""

    def _get_gm_entity(self) -> Optional[Any]:
        for entity in self._session.entities.values():
            if entity.get_component("SimulationControl"):
                return entity
        return None

    def _get_scene_snapshot(self) -> Dict[str, Any]:
        gm = self._get_gm_entity()
        if not gm:
            return {}
        scene = gm.get_component("SceneState")
        if not scene:
            return {}
        return copy.deepcopy(scene.get_snapshot())

    def _get_plot_snapshot(self) -> Dict[str, Any]:
        gm = self._get_gm_entity()
        if not gm:
            return {}
        plot_state = gm.get_component("PlotState")
        if not plot_state:
            return {}
        return copy.deepcopy(plot_state.get_snapshot())

    def _get_drama_snapshot(self) -> Dict[str, Any]:
        gm = self._get_gm_entity()
        if not gm:
            return {}
        drama_state = gm.get_component("DramaState")
        if not drama_state:
            return {}
        return {
            "tension": drama_state.tension,
            "target_min": drama_state.target_min,
            "target_max": drama_state.target_max,
            "crisis_threshold": drama_state.crisis_threshold,
            "last_directive": drama_state.last_directive,
            "recent_forces": list(drama_state.recent_forces),
        }
