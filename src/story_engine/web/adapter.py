import copy
import io
from contextlib import redirect_stdout
from threading import Lock
from typing import Any, Dict, List, Optional

from src.story_engine.agents import default_hermes_runtime_factories
from src.story_engine.session import create_session, Session
from src.story_engine.scenarios.config import ScenarioConfig


class WebGameAdapter:
    """
    Thin application adapter that turns a Session into a browser-friendly JSON API.
    The engine remains unaware of HTTP, templates, and frontend concerns.
    """

    def __init__(
        self,
        scenario: ScenarioConfig,
        title: Optional[str] = None,
        *,
        agent_runtime_factories: Optional[Dict[str, Any]] = None,
    ):
        self._scenario = scenario
        self._title = title or scenario.name
        # Defaults to the real Hermes container runtime; callers (e.g. tests)
        # may override with a stub without reintroducing a silent fallback.
        self._agent_runtime_factories = (
            agent_runtime_factories
            if agent_runtime_factories is not None
            else default_hermes_runtime_factories()
        )
        self._lock = Lock()
        self._session: Session
        self._history: List[Dict[str, Any]]
        self._boot_log = ""
        self._reset_locked()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._build_state_payload()

    def submit_turn(self, command: str = "", inject_event: str = "") -> Dict[str, Any]:
        if not isinstance(command, str):
            raise ValueError("command must be a natural-language string")
        if not isinstance(inject_event, str):
            raise ValueError("inject_event must be a natural-language string")
        command = command.strip()
        inject_event = inject_event.strip()

        with self._lock:
            if self._session.delivery_pending:
                return self._build_state_payload(
                    last_step=(copy.deepcopy(self._history[-1]) if self._history else None),
                    submission_blocked="pending_delivery_retry",
                )
            overrides: Dict[str, str] = {}
            player_name = self._session.player_character_name
            player_ready = bool(
                player_name and self._session.is_actor_ready(player_name)
            )
            accepted_command = command if player_ready else ""
            if accepted_command and player_name:
                overrides[player_name] = accepted_command

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
                step_context = self._session.run_step(
                    overrides=overrides,
                    inject_events=inject_events,
                    on_phase_done=capture_phase,
                )

            step_entry = self._build_history_entry(
                phase_trace=phase_trace,
                player_command=accepted_command,
                inject_event=inject_event,
                step_context=step_context,
            )
            self._history.append(step_entry)
            self._history = self._history[-40:]
            return self._build_state_payload(last_step=step_entry)

    def retry_delivery(self) -> Dict[str, Any]:
        with self._lock:
            if not self._session.delivery_pending:
                return self._build_state_payload(
                    last_step=(copy.deepcopy(self._history[-1]) if self._history else None)
                )
            phase_trace: Dict[str, Any] = {}

            def capture_phase(phase_name, context, entities):
                del entities
                if phase_name == "RenderingSystem":
                    phase_trace["rendered_text"] = context.get("rendered_text", "")

            with redirect_stdout(io.StringIO()):
                context = self._session.retry_delivery(on_phase_done=capture_phase)
            status = self._session.public_step_status(context)
            previous = copy.deepcopy(self._history[-1]) if self._history else {}
            if status["status"] == "committed":
                previous.update(
                    {
                        "kind": "turn",
                        "status": "committed",
                        "committed": True,
                        "narration": phase_trace.get("rendered_text")
                        or context.get("rendered_text")
                        or previous.get("narration", "世界已经推进。"),
                    }
                )
                previous.pop("failure_phase", None)
                previous.pop("failure_type", None)
            else:
                previous.update(
                    {
                        "status": "delivery_failed",
                        "committed": True,
                        "failure_phase": status.get("failure_phase", ""),
                        "failure_type": status.get("failure_type", ""),
                    }
                )
            if self._history:
                self._history[-1] = previous
            return self._build_state_payload(last_step=previous)

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._reset_locked()
            return self._build_state_payload()

    def close(self) -> None:
        """Release the current session and its persistent agent runtimes."""

        with self._lock:
            session = getattr(self, "_session", None)
            if session is not None:
                session.close()

    def _reset_locked(self) -> None:
        previous = getattr(self, "_session", None)
        if previous is not None:
            previous.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self._session = create_session(
                self._scenario,
                agent_runtime_factories=self._agent_runtime_factories,
            )
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
        step_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        status = (
            self._session.public_step_status(step_context)
            if step_context is not None
            else {
                "status": "committed",
                "committed": True,
                "failure_phase": "",
                "failure_type": "",
            }
        )
        step = self._session.step_count
        if status["status"] == "aborted":
            return self._to_public_entry(
                {
                    "kind": "system",
                    "step": step,
                    "title": "步骤未开始",
                    "narration": "本次宿主输入未通过验证，世界没有推进。",
                    **status,
                }
            )
        if status["status"] == "rolled_back":
            return self._to_public_entry(
                {
                    "kind": "system",
                    "step": step,
                    "title": "步骤已回滚",
                    "narration": "权威系统发生内部故障，本次行动没有进入故事历史。",
                    **status,
                }
            )
        narration = phase_trace.get("rendered_text")
        if status["status"] == "delivery_failed" and not narration:
            narration = "世界状态已经推进，但本轮叙事文本交付失败。"
        narration = narration or "局面没有显著变化。"

        return self._to_public_entry({
            "kind": "turn",
            "step": step,
            "title": f"第 {step} 步",
            "player_command": player_command or phase_trace.get("player_intent", ""),
            "player_command_source": "manual" if player_command else (phase_trace.get("player_intent_source", "") or "none"),
            "inject_event": inject_event,
            "narration": narration,
            **status,
        })

    def _to_public_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        public_entry = {
            "kind": entry.get("kind", "turn"),
            "step": entry.get("step", 0),
            "title": entry.get("title", ""),
            "narration": entry.get("narration", ""),
            "status": entry.get("status", "committed"),
            "committed": bool(entry.get("committed", True)),
        }
        if entry.get("failure_phase"):
            public_entry["failure_phase"] = str(entry.get("failure_phase"))[:120]
        if entry.get("failure_type"):
            public_entry["failure_type"] = str(entry.get("failure_type"))[:120]
        player_command = (entry.get("player_command") or "").strip()
        inject_event = (entry.get("inject_event") or "").strip()
        if public_entry["kind"] == "turn":
            public_entry["player_command"] = player_command or "本轮你没有明确表态，也没有明显动作。"
            public_entry["player_command_source"] = str(entry.get("player_command_source", "none")).strip() or "none"
        if inject_event:
            public_entry["inject_event"] = inject_event
        return public_entry

    def _build_state_payload(
        self,
        last_step: Optional[Dict[str, Any]] = None,
        submission_blocked: str = "",
    ) -> Dict[str, Any]:
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
                "ready": bool(
                    player_name and self._session.is_actor_ready(player_name)
                ),
                "pending_action": (
                    self._session.pending_action(player_name) if player_name else {}
                ),
                "decision_context": (
                    self._session.player_decision_context()
                    if player_name
                    else {}
                ),
            },
            "step_count": self._session.step_count,
            "simulation_time": self._session.simulation_time,
            "delivery_pending": self._session.delivery_pending,
            "submission_blocked": submission_blocked,
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
