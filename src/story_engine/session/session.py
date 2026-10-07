"""
Session: one runnable sandbox instance (Runner + Scenario + step count).
Decouples "what to run" from "how the human interacts" (Driver).
"""
from typing import Dict, Optional, List, Any
from src.story_engine.environment.runner import Runner
from src.story_engine.scenarios.config import ScenarioConfig
from .scenario_loader import setup_scenario
from .semantic_seed_compiler import compile_play_seed
from .step_status import public_step_status


class Session:
    """
    A single play session: one Runner bound to one Scenario.
    Step execution is delegated to Runner; Session tracks step count and exposes runner/scenario for drivers.
    """
    def __init__(self, runner: Runner, scenario: ScenarioConfig):
        self.runner = runner
        self.scenario = scenario
        self.step_count = 0
        self._closed = False
        self.autosave_path = None
        self.presentation_state = {}

    def save(self, path):
        from .savegame import save_session
        return save_session(self, path)

    def close(self) -> None:
        """Release live agent runtimes owned by this session."""

        if self._closed:
            return
        try:
            if self.autosave_path:
                self.save(self.autosave_path)
        finally:
            self._closed = True
            close = getattr(self.runner, "close", None)
            if callable(close):
                close()

    @property
    def entities(self):
        return self.runner.entities

    @property
    def player_character_name(self) -> Optional[str]:
        return self.scenario.player_character_name

    def run_step(
        self,
        overrides: Optional[Dict[str, str]] = None,
        world_edits: Optional[List[tuple]] = None,
        topology_changes: Optional[List[Dict[str, Any]]] = None,
        inject_events: Optional[List[Any]] = None,
        on_phase_done: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run one simulation step; rejected pre-step host input consumes none."""
        if self._closed:
            raise RuntimeError("session is closed")
        context = self.runner.run_step(
            overrides=overrides or {},
            world_edits=world_edits,
            topology_changes=topology_changes,
            inject_events=inject_events,
            player_name=self.player_character_name,
            on_phase_done=on_phase_done,
        )
        if not context.get("step_aborted", False) and not context.get(
            "authoritative_step_failed", False
        ):
            self.step_count += 1
        if self.autosave_path:
            self.save(self.autosave_path)
        return context

    def is_actor_ready(self, actor_name: str) -> bool:
        return not self.runner.action_queue.is_busy(actor_name)

    def pending_action(self, actor_name: str) -> Dict[str, Any]:
        return self.runner.action_queue.pending_for(actor_name)

    def actor_decision_context(self, actor_name: str) -> Dict[str, Any]:
        return self.runner.get_agent_decision_context(actor_name)

    def player_decision_context(self) -> Dict[str, Any]:
        player = self.player_character_name
        return self.actor_decision_context(player) if player else {}

    @staticmethod
    def public_step_status(context: Dict[str, Any]) -> Dict[str, Any]:
        return public_step_status(context)

    @property
    def delivery_pending(self) -> bool:
        return self.runner.has_pending_delivery()

    def retry_delivery(self, on_phase_done: Optional[Any] = None) -> Dict[str, Any]:
        """Retry post-commit Rendering/Memory without consuming another step."""
        result = self.runner.retry_delivery(on_phase_done=on_phase_done)
        if self.autosave_path:
            self.save(self.autosave_path)
        return result

    @property
    def simulation_time(self) -> int:
        return int(self.runner.action_queue.current_time)

    @property
    def random_seed(self) -> int | str:
        """Seed required to replay host policy and probability checks."""
        return self.runner.random_seed


def create_session(
    scenario: ScenarioConfig,
    agent_runtime_factories: Optional[Dict[str, Any]] = None,
    random_seed: int | str | None = None,
    sentiment_definitions: Optional[Dict[str, Any]] = None,
    modifier_definitions: Optional[Dict[str, Any]] = None,
    memory_namespace: Optional[str] = None,
) -> Session:
    """Create a new Session: Runner + scenario loaded (GM and characters)."""
    runner = Runner(
        agent_runtime_factories=agent_runtime_factories,
        random_seed=random_seed,
        sentiment_definitions=sentiment_definitions,
        modifier_definitions=modifier_definitions,
        memory_namespace=memory_namespace,
    )
    try:
        setup_scenario(runner, scenario)
    except Exception:
        runner.close()
        raise
    return Session(runner, scenario)


def create_session_from_seed(
    seed: ScenarioConfig | Dict[str, Any] | str,
    agent_runtime_factories: Optional[Dict[str, Any]] = None,
    random_seed: int | str | None = None,
    sentiment_definitions: Optional[Dict[str, Any]] = None,
    modifier_definitions: Optional[Dict[str, Any]] = None,
    memory_namespace: Optional[str] = None,
    *,
    runtime: str = "hermes",
    simulation_mode: str | None = None,
    narration_mode: str | None = None,
    profile: str = "production",
    seed_provider: Any | None = None,
) -> Session:
    """Compile an author seed and immediately create a runnable Session.

    This is the product-facing onboarding path.  It deliberately delegates
    structural validation to ``compile_scenario_seed`` and the existing
    ``setup_scenario`` boundary, so a malformed seed fails before any agent
    process is started.
    """

    scenario = compile_play_seed(
        seed,
        profile=profile,
        provider=seed_provider,
        runtime=runtime,
        simulation_mode=simulation_mode,
        narration_mode=narration_mode,
    )
    if profile == "offline":
        from .play_profile import bind_play_profile

        scenario = bind_play_profile(scenario, "offline")
    if agent_runtime_factories is None:
        from .play_profile import runtime_factories_for_profile

        agent_runtime_factories = runtime_factories_for_profile(profile)
    return create_session(
        scenario,
        agent_runtime_factories=agent_runtime_factories,
        random_seed=random_seed,
        sentiment_definitions=sentiment_definitions,
        modifier_definitions=modifier_definitions,
        memory_namespace=memory_namespace,
    )
