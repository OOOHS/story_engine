import secrets
from uuid import uuid4
from typing import List, Dict, Any, Optional, Callable
from src.story_engine.clocks.game_clock import GameClock
from src.story_engine.core.entity import Entity
from src.story_engine.core.logger import logger
from src.story_engine.environment.dispatcher import Dispatcher
from src.story_engine.systems import (
    System,
    CognitionSystem,
    DriveSystem,
    ObligationSystem,
    AgreementSystem,
    GoalSystem,
    ModifierSystem,
    ClaimSystem,
    ClaimKnowledgeSystem,
    RouteKnowledgeSystem,
    NavigationSystem,
    ActionSchedulingSystem,
    InputSystem,
    RenderingSystem,
    RelationshipSystem,
    SentimentSystem,
    SimulationSystem,
    WorldEventSystem,
)
from src.story_engine.systems.memory import MemorySystem
from src.story_engine.agents import AgentRegistry, CharacterAgentRuntime
from src.story_engine.environment.action_queue import ActionEventQueue
from src.story_engine.environment.host_mutations import HostMutationTransaction
from src.story_engine.environment.step_checkpoint import RunnerStepCheckpoint
from src.story_engine.environment.delivery import DeliveryReceipt, clone_delivery_context
from src.story_engine.social import AgreementRegistry, SocialRelationRegistry
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.simulation.checks import HostCheckResolver
from src.story_engine.knowledge import ClaimRegistry


def _default_phase_order() -> List[str]:
    return [
        "InputSystem",
        "ActionSchedulingSystem",
        "SimulationSystem",
        "ObligationSystem",
        "AgreementSystem",
        "ClaimSystem",
        "GoalSystem",
        "ModifierSystem",
        "RelationshipSystem",
        "DriveSystem",
        "ClaimKnowledgeSystem",
        "RouteKnowledgeSystem",
        "NavigationSystem",
        "CognitionSystem",
        "SentimentSystem",
        "WorldEventSystem",
        "RenderingSystem",
        "MemorySystem",
    ]

AgentRuntimeFactory = Callable[[Entity, Dict[str, Any]], CharacterAgentRuntime]


class Runner:
    """
    The main engine runner that orchestrates the simulation loop.
    It manages entities and executes systems in a defined order.
    Supports optional human-in-the-loop: world_edits, inject_events, on_phase_done callback.
    """
    def __init__(
        self,
        clock: GameClock = None,
        agent_runtime_factories: Optional[Dict[str, AgentRuntimeFactory]] = None,
        random_seed: int | str | None = None,
        sentiment_definitions: Optional[Dict[str, Any]] = None,
        modifier_definitions: Optional[Dict[str, Any]] = None,
        memory_namespace: Optional[str] = None,
    ):
        self.clock = clock or GameClock()
        self.entities: Dict[str, Entity] = {}
        self.dispatcher = Dispatcher()
        self.agent_registry = AgentRegistry()
        self.action_queue = ActionEventQueue(start_time=self.clock.current_step)
        self.relation_registry = SocialRelationRegistry()
        self.agreement_registry = AgreementRegistry(self.relation_registry)
        self.claim_registry = ClaimRegistry()
        self.scenario = None
        self.memory_namespace = (
            str(memory_namespace).strip()
            if memory_namespace is not None and str(memory_namespace).strip()
            else f"session-{uuid4().hex}"
        )
        self.random_seed = random_seed if random_seed is not None else secrets.randbits(64)
        self.random_streams = DeterministicRandomStreams(self.random_seed)
        self.check_resolver = HostCheckResolver(self.random_streams)
        self.host_mutation_transaction = HostMutationTransaction()
        self._pending_delivery: DeliveryReceipt | None = None
        # Deliberately no built-in default runtime. A character with no
        # matching entry in this table fails loudly in register_agent()
        # instead of silently falling back to any particular runtime.
        self.agent_runtime_factories: Dict[str, AgentRuntimeFactory] = {}
        self.agent_runtime_factories.update(agent_runtime_factories or {})
        self.logger = logger
        self.modifier_system = ModifierSystem(modifier_definitions)
        self.systems: List[System] = [
            InputSystem(),
            ActionSchedulingSystem(),
            SimulationSystem(),
            ObligationSystem(),
            AgreementSystem(),
            ClaimSystem(),
            GoalSystem(),
            self.modifier_system,
            RelationshipSystem(),
            DriveSystem(),
            ClaimKnowledgeSystem(),
            RouteKnowledgeSystem(),
            NavigationSystem(),
            CognitionSystem(),
            SentimentSystem(sentiment_definitions),
            WorldEventSystem(),
            RenderingSystem(),
            MemorySystem(),
        ]
        self._phase_order = _default_phase_order()

    def add_entity(self, entity: Entity):
        existing = self.entities.get(entity.name)
        if existing is not None and existing is not entity:
            raise ValueError(f"entity name collision: {entity.name}")
        self.entities[entity.name] = entity
        self.logger.info(f"Registered entity: {entity.name}")

    def register_agent(self, entity: Entity) -> None:
        """Attach the runtime declared by an entity's AgentController."""
        controller = entity.get_component("AgentController")
        if not controller:
            raise ValueError(
                f"Cannot register entity without AgentController: {entity.name}"
            )
        factory = self.agent_runtime_factories.get(controller.runtime)
        if factory is None:
            raise ValueError(
                f"No runtime factory registered for '{controller.runtime}' "
                f"(character: {entity.name})."
            )
        runtime = factory(entity, dict(controller.config))
        self.agent_registry.register(entity, runtime)

    def agent_boundary_errors(self) -> List[str]:
        """Audit the one-to-one Scene actor ↔ ECS Agent ↔ live runtime invariant."""
        scene_state = next(
            (
                state
                for entity in self.entities.values()
                if (state := entity.get_component("SceneState")) is not None
            ),
            None,
        )
        if scene_state is None:
            return ["missing SceneState"]
        errors = []
        actor_names = set(scene_state.actor_states)
        controller_names = {
            name
            for name, entity in self.entities.items()
            if entity.get_component("AgentController") is not None
        }
        for name in sorted(actor_names):
            entity = self.entities.get(name)
            if entity is None:
                errors.append(f"actor has no ECS Entity:{name}")
                continue
            if entity.get_component("AgentController") is None:
                errors.append(f"actor has no AgentController:{name}")
                continue
            registered = self.agent_registry.get(entity)
            if registered is None or registered.entity is not entity:
                errors.append(f"actor has no live runtime:{name}")
        for name in sorted(controller_names.difference(actor_names)):
            errors.append(f"Agent Entity has no Scene actor state:{name}")
        for registered in self.agent_registry.agents():
            name = registered.entity.name
            if self.entities.get(name) is not registered.entity:
                errors.append(f"registered runtime is detached from ECS Entity:{name}")
            elif name not in actor_names:
                errors.append(f"registered runtime has no Scene actor state:{name}")
        return errors

    def unregister_agent(self, entity: Entity) -> None:
        self.agent_registry.unregister(entity)

    def close(self) -> None:
        """Close all live character runtimes owned by this Runner.

        Runner instances are session-scoped.  Explicit closure is important
        for persistent Hermes subject processes, especially when a Web
        adapter resets or a server shuts down.
        """

        self.agent_registry.close()

    def get_agent_perception(self, actor_name: str) -> Any:
        """Build a read-only decision packet for a human or other UI runtime."""
        entity = self.entities.get(str(actor_name or "").strip())
        if entity is None or entity.get_component("AgentController") is None:
            return None
        scene_state = next(
            (
                state
                for candidate in self.entities.values()
                if (state := candidate.get_component("SceneState")) is not None
            ),
            None,
        )
        input_system = next(
            (
                system
                for system in self.systems
                if isinstance(system, InputSystem)
            ),
            None,
        )
        if input_system is None:
            return None
        preview_context = {
            "clock": self.clock,
            "player_name": entity.name,
            "action_queue": self.action_queue,
            "agreement_registry": self.agreement_registry,
            "relation_registry": self.relation_registry,
            "claim_registry": self.claim_registry,
            "_relationship_book_view": self.relation_registry.to_relationship_book(),
            "scenario": self.scenario,
        }
        return input_system.build_agent_perception(
            entity=entity,
            scene_state=scene_state,
            intents_buffer=[],
            context=preview_context,
            activation_scope="manual",
        )

    def get_agent_decision_context(self, actor_name: str) -> Dict[str, Any]:
        perception = self.get_agent_perception(actor_name)
        return perception.manual_decision_context() if perception is not None else {}

    def has_pending_delivery(self) -> bool:
        return self._pending_delivery is not None

    def retry_delivery(
        self,
        on_phase_done: Optional[
            Callable[[str, Dict[str, Any], Dict[str, Entity]], Optional[Dict[str, Any]]]
        ] = None,
    ) -> Dict[str, Any]:
        receipt = self._pending_delivery
        if receipt is None:
            return {
                "step_failed": False,
                "authoritative_step_failed": False,
                "step_committed": True,
                "delivery_retried": False,
                "delivery_pending": False,
                "delivery_retry_status": "nothing_pending",
            }
        context = clone_delivery_context(receipt.context)
        context.pop("phase_errors", None)
        context.pop("step_failure_reason", None)
        context["step_failed"] = False
        context["authoritative_step_failed"] = False
        context["step_committed"] = True
        context["delivery_retried"] = True
        context["delivery_retry_attempt"] = int(receipt.attempts) + 1
        retry_trace = []
        for index in range(int(receipt.start_index), len(self.systems)):
            system = self.systems[index]
            system_name = system.__class__.__name__
            if system_name == "WorldEventSystem":
                raise RuntimeError("delivery receipt cannot rerun authoritative phases")
            phase_checkpoint = RunnerStepCheckpoint.capture(self)
            phase_context_keys = set(context)
            try:
                system.update(self.entities, context)
                if on_phase_done:
                    out = on_phase_done(system_name, context, self.entities)
                    if isinstance(out, dict):
                        context.update(out)
            except Exception as exc:
                phase_checkpoint.restore(self)
                for key in set(context).difference(phase_context_keys):
                    context.pop(key, None)
                error = {
                    "phase": system_name,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
                retry_trace.append({"phase": system_name, "status": "failed"})
                context["delivery_retry_trace"] = retry_trace
                context["phase_errors"] = [error]
                context["step_failed"] = True
                context["step_failure_reason"] = "delivery_phase_exception"
                context["delivery_pending"] = True
                self._pending_delivery = DeliveryReceipt.capture(
                    start_index=index,
                    context=context,
                    attempts=int(receipt.attempts) + 1,
                )
                return context
            retry_trace.append({"phase": system_name, "status": "completed"})
        context["delivery_retry_trace"] = retry_trace
        context["delivery_pending"] = False
        context["delivery_retry_status"] = "completed"
        self._pending_delivery = None
        return context

    def run_step(
        self,
        overrides: Dict[str, str] = None,
        world_edits: Optional[List[tuple]] = None,
        topology_changes: Optional[List[Dict[str, Any]]] = None,
        inject_events: Optional[List[Any]] = None,
        player_name: Optional[str] = None,
        on_phase_done: Optional[Callable[[str, Dict[str, Any], Dict[str, Entity]], Optional[Dict[str, Any]]]] = None,
    ):
        """
        Executes one simulation step.

        - overrides: entity_name -> intent string, or GM render override string for this step.
        - world_edits: list of (object_name, {property: value}) to apply to GM's SceneState before the step.
        - topology_changes: host-authorized connect/disconnect commands applied atomically before Agent perception.
        - inject_events: list of strings added to the Input phase as world-originated intents/events.
        - on_phase_done: callback(phase_name, context, entities) called after each system; return dict to merge into context for remaining systems.
        """
        overrides = overrides or {}
        if self._pending_delivery is not None:
            return {
                "step_aborted": True,
                "step_abort_reason": "pending_delivery_retry",
                "step_failed": False,
                "authoritative_step_failed": False,
                "step_committed": False,
                "delivery_pending": True,
            }
        has_scene_state = any(
            entity.get_component("SceneState") is not None
            for entity in self.entities.values()
        )
        if has_scene_state:
            boundary_errors = self.agent_boundary_errors()
            if boundary_errors:
                return {
                    "step_aborted": True,
                    "step_abort_reason": "invalid_agent_boundary",
                    "step_failed": False,
                    "authoritative_step_failed": False,
                    "step_committed": False,
                    "agent_boundary_errors": boundary_errors,
                }
        step_checkpoint = RunnerStepCheckpoint.capture(self)
        context = {
            "dispatcher": self.dispatcher,
            "overrides": overrides,
            "clock": self.clock,
            "player_name": player_name,
            "inject_events": list(inject_events) if inject_events else [],
            "intents": [],
            "agent_registry": self.agent_registry,
            "action_queue": self.action_queue,
            "agreement_registry": self.agreement_registry,
            "relation_registry": self.relation_registry,
            "random_seed": self.random_seed,
            "random_streams": self.random_streams,
            "check_resolver": self.check_resolver,
            "register_agent": self.register_agent,
            "unregister_agent": self.unregister_agent,
            "modifier_catalog": self.modifier_system.dynamics.public_catalog(),
            "claim_registry": self.claim_registry,
            "memory_namespace": self.memory_namespace,
            "scenario": self.scenario,
        }

        scene_state = next(
            (
                state
                for entity in self.entities.values()
                if (state := entity.get_component("SceneState")) is not None
            ),
            None,
        )
        host_mutation_outcome = self.host_mutation_transaction.apply(
            scene_state,
            world_edits=world_edits,
            topology_changes=topology_changes,
            current_step=self.clock.current_step,
        )
        context["world_edit_transaction"] = {
            "committed": host_mutation_outcome.committed,
            "errors": list(host_mutation_outcome.world_edit_errors),
        }
        context["host_object_state_changes"] = list(
            host_mutation_outcome.object_changes
        )
        context["topology_transaction"] = {
            "committed": host_mutation_outcome.committed,
            "errors": list(host_mutation_outcome.topology_errors),
        }
        context["topology_changes"] = list(host_mutation_outcome.topology_changes)
        context["host_mutation_transaction"] = {
            "committed": host_mutation_outcome.committed,
            "errors": list(host_mutation_outcome.errors),
        }
        if not host_mutation_outcome.committed:
            context["step_aborted"] = True
            context["step_abort_reason"] = "host_mutation_rejected"
            context["step_failed"] = False
            context["authoritative_step_failed"] = False
            context["step_committed"] = False
            self.logger.error(
                "Step rejected before Agent execution: %s",
                "; ".join(host_mutation_outcome.errors),
            )
            return context
        context["step_aborted"] = False
        context["step_failed"] = False
        context["authoritative_step_failed"] = False
        context["step_committed"] = False
        context["phase_trace"] = []
        base_context = {
            **context,
            "overrides": dict(context.get("overrides", {})),
            "inject_events": list(context.get("inject_events", [])),
            "intents": [],
            "host_object_state_changes": list(
                context.get("host_object_state_changes", [])
            ),
            "topology_changes": list(context.get("topology_changes", [])),
        }
        self.dispatcher.begin_transaction()

        print(f"\n=== Step {self.clock.current_step} : {self.clock.get_time_display()} ===")
        self.logger.info(f"Starting Step {self.clock.current_step}")
        authoritative_committed = False
        for system_index, system in enumerate(self.systems):
            system_name = system.__class__.__name__
            print(f"  [System] {system_name} running...")
            delivery_checkpoint = (
                RunnerStepCheckpoint.capture(self)
                if authoritative_committed
                else None
            )
            phase_context_keys = set(context)
            try:
                system.update(self.entities, context)
                if on_phase_done:
                    out = on_phase_done(system_name, context, self.entities)
                    if isinstance(out, dict):
                        overrides_update = out.pop("overrides", None)
                        context.update(out)
                        if overrides_update:
                            context.setdefault("overrides", {}).update(
                                overrides_update
                            )
            except Exception as exc:
                error = {
                    "phase": system_name,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
                self.logger.error(
                    f"Error in system {system_name}: {exc}", exc_info=True
                )
                context.setdefault("phase_trace", []).append(
                    {"phase": system_name, "status": "failed"}
                )
                if not authoritative_committed:
                    self.dispatcher.rollback_transaction()
                    step_checkpoint.restore(self)
                    trace = list(context.get("phase_trace", []))
                    context.clear()
                    context.update(base_context)
                    context["host_object_state_changes"] = []
                    context["topology_changes"] = []
                    context["world_edit_transaction"] = {
                        "committed": False,
                        "errors": ["rolled back after authoritative phase failure"],
                    }
                    context["topology_transaction"] = {
                        "committed": False,
                        "errors": ["rolled back after authoritative phase failure"],
                    }
                    context["host_mutation_transaction"] = {
                        "committed": False,
                        "errors": ["rolled back after authoritative phase failure"],
                    }
                    context["phase_trace"] = trace
                    context["phase_errors"] = [error]
                    context["step_failed"] = True
                    context["authoritative_step_failed"] = True
                    context["step_committed"] = False
                    context["step_failure_reason"] = "authoritative_phase_exception"
                    print(
                        f"=== Step {self.clock.current_step} Rolled Back "
                        f"({system_name}) ===\n"
                    )
                    return context
                if delivery_checkpoint is not None:
                    delivery_checkpoint.restore(self)
                for key in set(context).difference(phase_context_keys):
                    context.pop(key, None)
                context["phase_errors"] = [error]
                context["step_failed"] = True
                context["authoritative_step_failed"] = False
                context["step_committed"] = True
                context["step_failure_reason"] = "delivery_phase_exception"
                context["delivery_pending"] = True
                self._pending_delivery = DeliveryReceipt.capture(
                    start_index=system_index,
                    context=context,
                )
                print(
                    f"=== Step {self.clock.current_step} Committed With Delivery "
                    f"Failure ({system_name}) ===\n"
                )
                return context

            context.setdefault("phase_trace", []).append(
                {"phase": system_name, "status": "completed"}
            )
            if system_name == "WorldEventSystem":
                try:
                    self.dispatcher.commit_transaction()
                except Exception as exc:
                    context["phase_errors"] = [
                        {
                            "phase": "DispatcherCommit",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                        }
                    ]
                    context["step_failed"] = True
                    context["authoritative_step_failed"] = False
                    context["step_committed"] = True
                    context["step_failure_reason"] = "delivery_phase_exception"
                    return context
                authoritative_committed = True
                context["step_committed"] = True

        if not authoritative_committed:
            # Custom Runner phase lists without WorldEvent still receive a
            # deterministic commit barrier at the end of their chain.
            self.dispatcher.commit_transaction()
            context["step_committed"] = True

        context["delivery_pending"] = False
        self._pending_delivery = None

        print(f"=== Step {self.clock.current_step} Complete ===\n")
        return context
