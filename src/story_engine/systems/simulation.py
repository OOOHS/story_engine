from copy import deepcopy
from src.story_engine.components.simulation_control import SettlementRejected, SimulationControl
from src.story_engine.components.host_rule_simulation import HostRuleSimulationControl
from src.story_engine.environment.delivery import clone_delivery_context
from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.environment.character_lifecycle import CharacterLifecycle
from src.story_engine.environment.character_entries import CharacterEntryAuthority
from src.story_engine.environment.narrative_candidates import (
    record_candidate_audit,
)
from src.story_engine.environment.world_transaction import (
    TransactionResult,
    WorldStateTransaction,
)
from src.story_engine.narrative import (
    ConflictPressure,
    StoryletEngine,
)
from src.story_engine.narrative.storylet_execution import StoryletExecution
from src.story_engine.narrative.storylet_definitions import (
    StoryletDefinitionAuthority,
    StoryletDefinitionLifecycle,
)
from src.story_engine.environment.topology_candidates import (
    TopologyCandidateAuthority,
    TopologyCandidateLifecycle,
)
from src.story_engine.rules import LegalityEngine
from src.story_engine.social import SocialDynamics
from src.story_engine.simulation import (
    AffordanceActionResolver,
    EvidenceObservationResolver,
    ClaimCommunicationResolver,
    CommunicationResolver,
    ObjectDeliveryResolver,
    RouteCommunicationResolver,
    ProposalArbiter,
    ResourceContestResolver,
    SemanticAuthorityFilter,
)
from src.story_engine.simulation.uncertain_outcomes import UncertainOutcomeResolver


class SimulationSystem(System):
    """
    Resolves collected intents into authoritative state changes.
    """
    def __init__(self) -> None:
        super().__init__()
        self.storylets = StoryletEngine()
        self.storylet_execution = StoryletExecution()
        self.legality = LegalityEngine()
        self.conflicts = ConflictPressure()
        self.social = SocialDynamics()
        self.characters = CharacterLifecycle()
        self.character_entries = CharacterEntryAuthority()
        self.storylet_definitions = StoryletDefinitionAuthority()
        self.storylet_definition_lifecycle = StoryletDefinitionLifecycle()
        self.topology_candidates = TopologyCandidateAuthority()
        self.topology_candidate_lifecycle = TopologyCandidateLifecycle()
        self.transaction = WorldStateTransaction()
        self.proposals = ProposalArbiter()
        self.resource_contests = ResourceContestResolver()
        self.uncertain_outcomes = UncertainOutcomeResolver()
        self.affordance_actions = AffordanceActionResolver()
        self.evidence_observations = EvidenceObservationResolver()
        self.communications = CommunicationResolver()
        self.claim_communications = ClaimCommunicationResolver()
        self.object_deliveries = ObjectDeliveryResolver()
        self.route_communications = RouteCommunicationResolver()
        self.authority = SemanticAuthorityFilter()

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        baseline = clone_delivery_context(context)
        scenes = [(scene, deepcopy(scene.model_dump())) for entity in entities.values()
                  if (scene := entity.get_component("SceneState")) is not None]
        feedback = None
        for attempt in range(2):
            if feedback is not None:
                context["settlement_feedback"] = feedback
            try:
                self._settle_once(entities, context)
                context.pop("settlement_feedback", None)
                context["settlement_attempts"] = attempt + 1
                return
            except SettlementRejected as exc:
                for scene, snapshot in scenes:
                    restored = scene.__class__(**deepcopy(snapshot))
                    for field in scene.__class__.model_fields:
                        setattr(scene, field, deepcopy(getattr(restored, field)))
                context.clear()
                context.update(clone_delivery_context(baseline))
                if attempt == 1:
                    raise
                feedback = {"issues": exc.issues, "rejected_candidate": exc.candidate}

    def _settle_once(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        for name, entity in list(entities.items()):
            simulation = entity.get_component("SimulationControl")
            if not simulation:
                continue

            scene_state = entity.get_component("SceneState")
            drama_state = None
            relation_registry = context.get("relation_registry")
            relation_before = relation_registry.snapshot() if relation_registry else None
            relationship_book = (
                relation_registry.to_relationship_book() if relation_registry else None
            )
            scenario = getattr(simulation, "scenario", None)
            semantic_settlement = isinstance(simulation, SimulationControl) and not isinstance(simulation, HostRuleSimulationControl)
            story_tracking = entity.get_component("StoryTracking")
            if story_tracking is not None:
                story_tracking.reconcile(scene_state)
            player_name = scenario.player_character_name if scenario else None
            current_step = context.get("clock").current_step if context.get("clock") else 0
            player_pov = scene_state.get_view_pov(player_name) if scene_state else {}
            pre_resolution_location = player_pov.get("location") if isinstance(player_pov, dict) else None
            pre_resolution_actor_locations = {
                actor: scene_state.get_actor_location(actor)
                for actor in scene_state.actor_states
            } if scene_state else {}
            pre_resolution_world_objects = (
                deepcopy(scene_state.world_objects) if scene_state else {}
            )
            pre_resolution_scene_flags = (
                deepcopy(scene_state.scene_flags) if scene_state else {}
            )
            situation_packet = self._refresh_situations(
                scene_state=scene_state,
                player_name=player_name,
                player_pov=player_pov,
                current_step=current_step,
            )
            player_intent = next(
                (item for item in context.get("intents", []) if item.get("actor") == player_name),
                None,
            )
            active_storylets = self._resolve_storylets(
                scene_state,
                scenario,
                situation_packet=situation_packet,
                tracking=story_tracking,
            )
            storylet_packet = self._build_storylet_packet(
                scene_state=scene_state,
                active_storylets=active_storylets,
                current_step=current_step,
                situation_packet=situation_packet,
            )
            storylet_triggers = self.storylet_execution.prepare(
                scene_state, active_storylets, current_step, tracking=story_tracking,
            )
            context["intents"] = list(context.get("intents", [])) + storylet_triggers
            context["storylet_triggers"] = storylet_triggers
            social_packet = self._build_social_packet(
                scene_state=scene_state,
                relationship_book=relationship_book,
                player_name=player_name,
                player_pov=player_pov,
            )
            motive_packet = self._build_motive_packet(
                scene_state=scene_state,
                scenario=scenario,
                player_name=player_name,
                player_pov=player_pov,
                social_packet=social_packet,
                entities=entities,
                relationship_book=relationship_book,
            )
            reaction_context = self._build_reaction_context(
                player_name,
                player_pov,
                player_intent,
                social_packet,
            )
            intent_focus = self._build_intent_focus_packet(
                intents=context.get("intents", []),
                player_name=player_name,
                player_intent=player_intent,
                reaction_context=reaction_context,
            )
            legality_context = self._build_legality_context(
                scene_state=scene_state,
                scenario=scenario,
                intents=context.get("intents", []),
                entities=entities,
            )
            legality_context["advisory_only"] = semantic_settlement
            conflict_packet = self._build_conflict_packet(
                scene_state=scene_state,
                scenario=scenario,
                current_step=current_step,
                reaction_context=reaction_context,
                storylet_packet=storylet_packet,
            )
            semantic_social = self._build_semantic_social_packet(social_packet)
            input_payload = {
                "current_step": current_step,
                "settlement_feedback": context.get("settlement_feedback"),
                "player_name": player_name,
                "player_pov": player_pov,
                "player_intent": player_intent or {},
                "intents": context.get("intents", []),
                "social": semantic_social,
                "legality": legality_context,
                "drive_context": self._build_drive_context(
                    entities,
                    context.get("intents", []),
                ),
                "modifier_catalog": list(context.get("modifier_catalog", [])),
                "claim_catalog": (
                    context["claim_registry"].gm_catalog()
                    if context.get("claim_registry") is not None
                    else []
                ),
                "character_entry_authorizations": list(
                    context.get("character_spawn_authorizations", [])
                ),
                "storylet_definition_authorizations": list(
                    context.get("storylet_definition_authorizations", [])
                ),
                "topology_candidate_authorizations": list(
                    context.get("topology_candidate_authorizations", [])
                ),
                "storylet_triggers": storylet_triggers,
                "conflict_pressure": self._build_conflict_pressure_hint(conflict_packet),

            }

            semantic_result = simulation.simulate(input_payload)
            simulation_error = semantic_result.get("simulation_error")
            if simulation_error:
                if isinstance(simulation_error, dict):
                    message = str(
                        simulation_error.get("message")
                        or simulation_error.get("kind")
                        or "semantic resolver failed"
                    )
                else:
                    message = str(simulation_error)
                raise RuntimeError(f"SimulationControl unresolved: {message}")
            self.storylet_execution.validate(storylet_triggers, semantic_result)
            authority_filter = self.authority.sanitize(semantic_result)
            result = authority_filter.result
            # This field is derived only after a successful transaction.  A
            # scripted resolver or LLM cannot forge movement event evidence.
            result["actor_movements"] = []
            result["object_state_changes"] = []
            result["scene_state_changes"] = []
            context["semantic_authority_rejections"] = list(
                authority_filter.rejected_writes
            )
            outcome_errors: List[str] = []
            outcome_traces: List[Dict[str, Any]] = []
            if result.get("uncertain_outcomes"):
                check_resolver = context.get("check_resolver")
                if check_resolver is None:
                    outcome_errors.append(
                        "uncertain_outcomes require the host CheckResolver"
                    )
                else:
                    try:
                        current_world_version = int(
                            scene_state.get_scene_flag("world_version", 0) or 0
                        ) if scene_state else 0
                    except (TypeError, ValueError):
                        current_world_version = 0
                    outcome_resolution = self.uncertain_outcomes.resolve(
                        result,
                        scene_state=scene_state,
                        intents=context.get("intents", []),
                        check_resolver=check_resolver,
                        current_step=current_step,
                        world_version=current_world_version,
                    )
                    result = outcome_resolution.result
                    outcome_errors.extend(outcome_resolution.errors)
                    outcome_traces.extend(outcome_resolution.traces)
                    context["semantic_authority_rejections"].extend(
                        outcome_resolution.rejected_writes
                    )
            # Each of these compiles Agent references the Input layer already
            # validated into exact Host writes, once the semantic layer has
            # judged that actor's action positive this round -- same
            # "reference in -> Host write out" shape, different domain. They
            # only differ in which extra context (scene_state, a registry,
            # the scenario) their domain needs to double-check before
            # materializing, so they run as one declarative pipeline instead
            # of a hand-copied call per resolver.
            intents = context.get("intents", [])
            communication_resolution = self.communications.resolve(
                intents=intents,
                legality_checks=legality_context.get("checks", []),
                scene_state=scene_state,
            ) if not semantic_settlement else None
            # Only the explicit offline baseline projects deterministic speech.
            # Production retains the model's delivery outcome and actual listeners.
            result["resolved_actions"] = [
                action
                for action in result.get("resolved_actions", []) or []
                if not (
                    isinstance(action, dict)
                    and str(action.get("actor", "")).strip()
                    in (communication_resolution.consumed_actors if communication_resolution else ())
                )
            ]
            result["resolved_actions"].extend(
                communication_resolution.resolved_actions if communication_resolution else ()
            )
            context["communication_traces"] = [
                dict(action) for action in (communication_resolution.resolved_actions if communication_resolution else ())
            ]
            for trace_key, resolver, kwargs in (
                (
                    "affordance_action_traces",
                    self.affordance_actions,
                    {"intents": intents, "scene_state": scene_state},
                ),
                (
                    "evidence_observation_traces",
                    self.evidence_observations,
                    {
                        "intents": intents,
                        "scene_state": scene_state,
                        "claim_registry": context.get("claim_registry"),
                    },
                ),
                (
                    "claim_communication_traces",
                    self.claim_communications,
                    {"intents": intents},
                ),
                (
                    "route_communication_traces",
                    self.route_communications,
                    {"intents": intents},
                ),
                (
                    "object_delivery_traces",
                    self.object_deliveries,
                    {"intents": intents, "scene_state": scene_state},
                ),
            ):
                if (semantic_settlement
                        and resolver in (self.evidence_observations, self.claim_communications, self.route_communications)):
                    continue
                resolution = resolver.resolve(result, **kwargs)
                result = resolution.result
                context[trace_key] = list(resolution.traces)
            proposal_actors = {
                str(item.get("actor", "")).strip()
                for item in context.get("intents", [])
                if isinstance(item, dict)
                and str(item.get("actor", "")).strip()
            }
            result = self.resource_contests.resolve(
                scene_state,
                result,
                intents=context.get("intents", []),
            )
            raw_storylet_definition = result.get("storylet_definition")
            storylet_definition_resolution = self.storylet_definitions.resolve(
                raw_storylet_definition,
                authorizations=context.get(
                    "storylet_definition_authorizations", []
                ),
                scene_state=scene_state,
                current_step=current_step,
            )
            context["storylet_definition_rejections"] = list(
                storylet_definition_resolution.rejected
            )
            result["storylet_definition"] = storylet_definition_resolution.request
            storylet_definition_preparation = self.storylet_definition_lifecycle.prepare(
                scenario,
                scene_state,
                storylet_definition_resolution.request,
            )
            storylet_definition_plan = storylet_definition_preparation.plan
            raw_topology_candidate = result.get("topology_candidate")
            topology_candidate_resolution = self.topology_candidates.resolve(
                raw_topology_candidate,
                authorizations=context.get(
                    "topology_candidate_authorizations", []
                ),
                scene_state=scene_state,
                current_step=current_step,
            )
            context["topology_candidate_rejections"] = list(
                topology_candidate_resolution.rejected
            )
            result["topology_candidate"] = topology_candidate_resolution.request
            topology_candidate_preparation = self.topology_candidate_lifecycle.prepare(
                scene_state,
                topology_candidate_resolution.request,
            )
            topology_candidate_plan = topology_candidate_preparation.plan
            # All new bodies and objects resolve their references against one
            # prospective graph. The authoritative Scene stays untouched.
            preview, completion_topology_plans, completion_errors = self._prepare_locations(
                scene_state, result, topology_candidate_plan,
            )
            raw_spawn_character = result.get("spawn_character")
            entry_resolution = self.character_entries.resolve(
                raw_spawn_character,
                authorizations=context.get("character_spawn_authorizations", []),
                scene_state=preview,
                current_step=current_step,
            )
            context["character_entry_rejections"] = list(entry_resolution.rejected)
            result["spawn_character"] = entry_resolution.request
            spawn_preparation = self.characters.prepare(
                entities,
                preview,
                entry_resolution.request,
                agent_runtime=getattr(scenario, "default_agent_runtime", ""),
                player_name=player_name,
                agent_registry=context.get("agent_registry"),
                memory_namespace=context.get("memory_namespace"),
            )
            spawn_plan = spawn_preparation.plan

            if spawn_plan is not None:
                completion_errors.extend(self.characters.stage(preview, spawn_plan))
            completion_spawn_plans = []
            for request in result.get("world_additions", {}).get("characters", []):
                preparation = self.characters.prepare(
                    entities, preview, request,
                    agent_runtime=getattr(scenario, "default_agent_runtime", ""),
                    agent_registry=context.get("agent_registry"),
                    memory_namespace=context.get("memory_namespace"),
                )
                completion_errors.extend(preparation.errors)
                if preparation.plan is not None:
                    completion_spawn_plans.append(preparation.plan)
                    completion_errors.extend(self.characters.stage(preview, preparation.plan))
            if completion_errors:
                raise SettlementRejected(completion_errors, result)
            self.storylet_execution.validate(storylet_triggers, result, preview)

            drive_states = {
                entity_name: drive
                for entity_name, character_entity in entities.items()
                if (drive := character_entity.get_component("DriveState")) is not None
            }
            all_spawn_plans = ([spawn_plan] if spawn_plan else []) + completion_spawn_plans
            for plan in all_spawn_plans:
                prepared_drive = plan.entity.get_component("DriveState")
                if prepared_drive is not None:
                    drive_states[plan.name] = prepared_drive
            spawned: List[str] = []
            preparation_errors = (
                list(outcome_errors)
                + list(spawn_preparation.errors)
                + list(storylet_definition_preparation.errors)
                + list(topology_candidate_preparation.errors)
            )
            if preparation_errors:
                transaction_result = TransactionResult(
                    False, preparation_errors
                )
                result = self.transaction.sanitize_rejected_result(
                    result,
                    transaction_result.errors,
                )
            else:
                transaction_result = self.transaction.commit(
                    scene_state=scene_state,
                    drama_state=drama_state,
                    result=result,
                    relationship_book=relationship_book,
                    character_spawn_plan=spawn_plan,
                    storylet_definition_plan=storylet_definition_plan,
                    topology_candidate_plan=topology_candidate_plan,
                    topology_candidate_plans=completion_topology_plans,
                    character_spawn_plans=completion_spawn_plans,
                    drive_states=drive_states,
                    current_step=current_step,
                    proposal_actors=proposal_actors,
                    emergent_meter_budget=int(
                        getattr(scenario, "emergent_meter_budget", 0) or 0
                    ),
                    semantic_validator=(
                        (lambda before, after, candidate: simulation.validate_commit(
                            before, after, candidate, input_payload))
                        if callable(getattr(simulation, "validate_commit", None)) else None
                    ),
                )
                if transaction_result.committed and all_spawn_plans:
                    try:
                        register_agent = context.get("register_agent")
                        if not callable(register_agent):
                            raise RuntimeError("agent registration callback is unavailable")
                        for plan in all_spawn_plans:
                            spawned.extend(self.characters.finalize(
                                entities, plan,
                                register_agent=register_agent,
                                unregister_agent=context.get("unregister_agent"),
                                agent_registry=context.get("agent_registry"),
                            ))
                    except Exception as exc:
                        for plan in all_spawn_plans:
                            entities.pop(plan.name, None)
                            unregister = context.get("unregister_agent")
                            if callable(unregister):
                                unregister(plan.entity)
                        spawned = []

                        if transaction_result.checkpoint:
                            transaction_result.checkpoint.restore()
                        transaction_result = TransactionResult(
                            False,
                            [f"spawn_character finalization rolled back: {exc}"],
                        )
                        if completion_spawn_plans:
                            raise RuntimeError("world completion subject registration failed") from exc
                if transaction_result.committed and relation_registry is not None:
                    try:
                        relation_registry.apply_relationship_book(
                            relationship_book, entities
                        )
                    except Exception as exc:
                        if transaction_result.checkpoint:
                            transaction_result.checkpoint.restore()
                        if relation_before is not None:
                            relation_registry.restore(relation_before, entities)
                        transaction_result = TransactionResult(
                            False,
                            [f"relationship publication rolled back: {exc}"],
                        )
                if transaction_result.committed and scene_state is not None:
                    self.storylet_execution.commit(
                        scene_state, active_storylets, storylet_triggers, result,
                        tracking=story_tracking, step=current_step,
                    )
                if not transaction_result.committed:
                    for plan in all_spawn_plans:
                        entities.pop(plan.name, None)
                        unregister = context.get("unregister_agent")
                        if callable(unregister):
                            unregister(plan.entity)
                    spawned = []
                    result = self.transaction.sanitize_rejected_result(
                        result,
                        transaction_result.errors,
                    )
                    result["action_feedback"] = [
                        {
                            "actor": str(item.get("actor", "")).strip(),
                            "event_id": str(item.get("event_id", "")).strip(),
                            "intent": str(item.get("intent", "")).strip(),
                            "action_kind": str(item.get("action_kind", "interact")),
                            "action_target": str(item.get("action_target", "")),
                            "outcome": "blocked",
                            "location": item.get("location"),
                            "visibility": "hidden",
                            "result": "这次行动没有形成有效的世界结果。",
                            "private_result": "",
                            "engine_feedback": True,
                        }
                        for item in context.get("intents", [])
                        if isinstance(item, dict)
                        and str(item.get("actor", "")).strip()
                        and str(item.get("actor", "")).strip() != "World"
                    ]

            if not transaction_result.committed and semantic_result.get("world_additions"):
                raise SettlementRejected(transaction_result.errors, semantic_result)

            if scene_state is not None and isinstance(raw_spawn_character, dict):
                spawned_this_step = bool(spawned) and transaction_result.committed
                record_candidate_audit(
                    scene_state,
                    kind="character",
                    source="gm",
                    accepted=spawned_this_step,
                    reason=(
                        ""
                        if spawned_this_step
                        else "; ".join(
                            entry_resolution.rejected
                            or spawn_preparation.errors
                            or transaction_result.errors
                        )
                    ),
                    candidate_id=str(raw_spawn_character.get("authorization_id", "")),
                    step=current_step,
                )

            if scene_state is not None and isinstance(raw_storylet_definition, dict):
                storylet_staged = (
                    storylet_definition_plan is not None
                    and transaction_result.committed
                )
                record_candidate_audit(
                    scene_state,
                    kind="storylet_definition",
                    source="gm",
                    accepted=storylet_staged,
                    reason=(
                        ""
                        if storylet_staged
                        else "; ".join(
                            storylet_definition_resolution.rejected
                            or storylet_definition_preparation.errors
                            or transaction_result.errors
                        )
                    ),
                    candidate_id=str(
                        raw_storylet_definition.get("authorization_id", "")
                    ),
                    step=current_step,
                )

            if scene_state is not None and isinstance(raw_topology_candidate, dict):
                topology_staged = (
                    topology_candidate_plan is not None
                    and transaction_result.committed
                )
                record_candidate_audit(
                    scene_state,
                    kind="topology",
                    source="gm",
                    accepted=topology_staged,
                    reason=(
                        ""
                        if topology_staged
                        else "; ".join(
                            topology_candidate_resolution.rejected
                            or topology_candidate_preparation.errors
                            or transaction_result.errors
                        )
                    ),
                    candidate_id=str(
                        raw_topology_candidate.get("authorization_id", "")
                    ),
                    step=current_step,
                )

            if transaction_result.committed and scene_state is not None:
                context["topology_changes"] = list(context.get("topology_changes", [])) + result.get("topology_changes", [])
                result["actor_movements"] = self._derive_actor_movements(
                    before_locations=pre_resolution_actor_locations,
                    scene_state=scene_state,
                    actions=result.get("resolved_actions", []),
                )
                result["object_state_changes"] = self._derive_object_state_changes(
                    before_objects=pre_resolution_world_objects,
                    scene_state=scene_state,
                    result=result,
                )
                result["scene_state_changes"] = self._derive_scene_state_changes(
                    before_flags=pre_resolution_scene_flags,
                    scene_state=scene_state,
                    result=result,
                )

            if scene_state:
                if transaction_result.committed:
                    self._record_conflict_result(scene_state, context, result)
                player_pov = scene_state.get_view_pov(player_name) if player_name else {}
                social_packet = self._build_social_packet(
                    scene_state=scene_state,
                    relationship_book=relationship_book,
                    player_name=player_name,
                    player_pov=player_pov,
                )
                situation_packet = self._refresh_situations(
                    scene_state=scene_state,
                    player_name=player_name,
                    player_pov=player_pov,
                    current_step=current_step,
                )
                motive_packet = self._build_motive_packet(
                    scene_state=scene_state,
                    scenario=scenario,
                    player_name=player_name,
                    player_pov=player_pov,
                    social_packet=social_packet,
                    entities=entities,
                    relationship_book=relationship_book,
                )
            visibility_window = self._build_visibility_window(
                pre_resolution_location,
                player_pov.get("location") if isinstance(player_pov, dict) else None,
            )
            actor_observation_windows = {
                actor: {
                    **self._build_visibility_window(
                        before_location,
                        scene_state.get_actor_location(actor) if scene_state else None,
                    ),
                    "present_during_step": True,
                }
                for actor, before_location in pre_resolution_actor_locations.items()
            }
            if scene_state:
                for actor in scene_state.actor_states:
                    actor_observation_windows.setdefault(
                        actor,
                        {
                            **self._build_visibility_window(
                                None,
                                scene_state.get_actor_location(actor),
                            ),
                            "present_during_step": False,
                        },
                    )

            context["simulation_result"] = result
            context["active_storylets"] = active_storylets
            context["situations"] = situation_packet
            context["reaction_context"] = reaction_context
            context["intent_focus"] = intent_focus
            context["social"] = social_packet
            context["motive_pressure"] = motive_packet
            context["legality"] = legality_context
            context["conflict"] = conflict_packet
            context["storylet_pressure"] = storylet_packet
            context["drive_context"] = self._build_drive_context(
                entities,
                context.get("intents", []),
            )
            context["state_snapshot"] = scene_state.get_snapshot() if scene_state else {}
            context["player_pov"] = player_pov
            context["visibility_window"] = visibility_window
            context["actor_observation_windows"] = actor_observation_windows
            context["spawned_characters"] = spawned
            context["state_transaction"] = {
                "committed": transaction_result.committed,
                "errors": list(transaction_result.errors),
            }
            context["outcome_check_errors"] = outcome_errors
            context["outcome_check_traces"] = outcome_traces

            world_updates = result.get("state_updates", {}).get("world_objects", {})
            actor_updates = result.get("state_updates", {}).get("actor_states", {})
            print(
                f"    -> Structured resolution ready: "
                f"{len(result.get('resolved_actions', []))} actions, "
                f"{len(world_updates)} world updates, {len(actor_updates)} actor updates, "
                f"{len(result.get('object_lifecycle', []))} object operations."
            )
            return

    def _build_drive_context(
        self,
        entities: Dict[str, Entity],
        intents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        relevant_names = {
            str(item.get("actor", "")).strip()
            for item in intents or []
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        packet = {}
        for name in relevant_names:
            entity = entities.get(name)
            drive = entity.get_component("DriveState") if entity else None
            if drive and hasattr(drive, "get_private_snapshot"):
                packet[name] = drive.get_private_snapshot()
        return packet

    def _build_visibility_window(
        self,
        before_location: Any,
        after_location: Any,
    ) -> Dict[str, Any]:
        locations: List[str] = []
        for raw in [before_location, after_location]:
            location = str(raw).strip() if raw else ""
            if location and location not in locations:
                locations.append(location)
        return {
            "locations": locations,
            "moved_this_turn": len(locations) > 1,
        }

    @staticmethod
    def _derive_actor_movements(
        *,
        before_locations: Dict[str, Any],
        scene_state: Any,
        actions: Any,
    ) -> List[Dict[str, Any]]:
        by_actor = {
            str(item.get("actor", "")).strip(): item
            for item in actions or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("outcome", "")).strip() != "blocked"
        }
        movements: List[Dict[str, Any]] = []
        for actor in sorted(before_locations):
            origin = str(before_locations.get(actor) or "").strip()
            destination = str(scene_state.get_actor_location(actor) or "").strip()
            if not origin or not destination or origin == destination:
                continue
            action = by_actor.get(actor, {})
            departure_witnesses = sorted(
                name
                for name, location in before_locations.items()
                if name != actor and str(location or "").strip() == origin
            )
            arrival_witnesses = sorted(
                name
                for name in scene_state.get_actors_in_location(destination)
                if name != actor
            )
            visibility = str(action.get("visibility", "local") or "local").strip()
            movements.append(
                {
                    "actor": actor,
                    "origin": origin,
                    "destination": destination,
                    "departure_witnesses": departure_witnesses,
                    "arrival_witnesses": arrival_witnesses,
                    "visibility": (
                        visibility
                        if visibility in {"public", "local", "hidden"}
                        else "local"
                    ),
                    "action_kind": str(action.get("action_kind", "")).strip(),
                    "action_target": str(action.get("action_target", "")).strip(),
                }
            )
        return movements

    def _prepare_locations(self, scene, result, authorized_plan):
        from src.story_engine.components.scene_state import SceneState
        additions = result.get("world_additions", {})
        if not isinstance(additions, dict) or set(additions) - {"locations", "characters", "facts"}:
            raise SettlementRejected(["world_additions requires locations, characters and facts lists"], result)
        for kind in ("locations", "characters"):
            values = additions.get(kind, [])
            if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                raise SettlementRejected([f"world_additions.{kind} must be a list of objects"], result)
        facts = additions.get("facts", [])
        if not isinstance(facts, list) or any(not isinstance(f, str) or not f.strip() for f in facts):
            raise SettlementRejected(["world_additions.facts must be a list of non-empty statements"], result)
        preview = SceneState(**deepcopy(scene.get_snapshot()))
        errors = self.topology_candidate_lifecycle.stage(preview, authorized_plan)
        plans, preparation_errors = self.topology_candidate_lifecycle.prepare_many(
            preview, additions.get("locations", []),
        )
        errors.extend(preparation_errors)
        if not errors:
            errors.extend(self.topology_candidate_lifecycle.stage_many(preview, plans))
        return preview, plans, errors

    @staticmethod
    def _derive_object_state_changes(
        *,
        before_objects: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        updates = result.get("state_updates", {}).get("world_objects", {})
        if not isinstance(updates, dict):
            return []
        protected = (
            WorldStateTransaction.OBJECT_LIFECYCLE_FIELDS
            | WorldStateTransaction.SPATIAL_TOPOLOGY_FIELDS
        )
        actions = [
            item
            for item in result.get("resolved_actions", []) or []
            if isinstance(item, dict)
            and str(item.get("outcome", "")).strip() != "blocked"
        ]
        changes: List[Dict[str, Any]] = []
        for object_id in sorted(updates):
            incoming = updates.get(object_id)
            before = before_objects.get(object_id)
            after = scene_state.get_object_state(object_id)
            if not isinstance(incoming, dict) or not isinstance(before, dict):
                continue
            paths = sorted(
                str(path)
                for path in incoming
                if str(path) not in protected
                and before.get(path) != after.get(path)
            )
            if not paths:
                continue
            source_actions = [
                action
                for action in actions
                if str(action.get("action_target", "")).strip() == str(object_id)
            ]
            source_actors = sorted(
                {
                    str(action.get("actor", "")).strip()
                    for action in source_actions
                    if str(action.get("actor", "")).strip()
                    and str(action.get("actor", "")).strip() != "World"
                }
            )
            if scene_state.is_location(object_id):
                location = str(object_id)
                hidden = False
            else:
                location = str(
                    scene_state.get_effective_object_location(object_id) or ""
                )
                hidden = bool(after.get("hidden", False))
            visibility = "hidden" if hidden else "local"
            if source_actions and all(
                str(action.get("visibility", "local")).strip() == "hidden"
                for action in source_actions
            ):
                visibility = "hidden"
            changes.append(
                {
                    "object_id": str(object_id),
                    "paths": paths,
                    "location": location,
                    "source_actors": source_actors,
                    "visibility": visibility,
                }
            )
        return changes

    @staticmethod
    def _derive_scene_state_changes(
        *,
        before_flags: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        updates = result.get("state_updates", {}).get("scene", {})
        if not isinstance(updates, dict):
            return []
        public_fields = scene_state.public_scene_field_names()
        paths = sorted(
            str(path)
            for path in updates
            if path != "description"
            and str(path) in public_fields
            and before_flags.get(path) != scene_state.get_scene_flag(path)
        )
        return [
            {
                "path": path,
                "value": deepcopy(scene_state.get_scene_flag(path)),
                "visibility": "public",
            }
            for path in paths
        ]

    def _build_reaction_context(
        self,
        player_name: Any,
        player_pov: Dict[str, Any],
        player_intent: Any,
        social_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.social.build_reaction_context(
            player_name, player_pov, player_intent, social_packet
        )

    def _build_legality_context(
        self,
        scene_state: Any,
        scenario: Any,
        intents: List[Dict[str, Any]],
        entities: Dict[str, Entity],
    ) -> Dict[str, Any]:
        known = {}
        for actor, entity in entities.items():
            state = entity.get_component("KnowledgeState")
            if state is not None:
                known[actor] = state.get_map_snapshot()
        return self.legality.build_context(
            scene_state,
            scenario,
            intents,
            actor_map_knowledge=known,
        )

    def _build_conflict_packet(
        self,
        scene_state: Any,
        scenario: Any,
        current_step: int,
        reaction_context: Dict[str, Any],
        storylet_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.conflicts.build_packet(
            scene_state,
            scenario,
            current_step,
            reaction_context,
            storylet_packet,
        )

    @staticmethod
    def _build_conflict_pressure_hint(
        conflict_packet: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        """Advisory-only pacing hint surfaced to the semantic resolver.

        The resolver may notice this advisory pressure and
        let a hostile watcher's action escalate accordingly, but nothing here
        forces a conflict beat or lets the GM act for a non-proposing actor.
        """
        if not conflict_packet:
            return {}
        active_templates = [
            {
                "template_id": str(item.get("template_id", "")),
                "instruction": str(item.get("instruction", "")),
                "preferred_actors": list(item.get("preferred_actors", [])),
                "tags": list(item.get("tags", [])),
            }
            for item in conflict_packet.get("active_templates", [])
            if isinstance(item, dict) and str(item.get("template_id", "")).strip()
        ]
        if not (
            conflict_packet.get("visible_conflict_opportunity")
            or active_templates
        ):
            return {}
        return {
            "visible_conflict_opportunity": bool(
                conflict_packet.get("visible_conflict_opportunity")
            ),
            "pressure_state": str(conflict_packet.get("pressure_state", "")),
            "opportunity_reasons": list(conflict_packet.get("opportunity_reasons", [])),
            "antagonist_names": list(conflict_packet.get("antagonist_names", [])),
            "preferred_modes": list(conflict_packet.get("preferred_modes", [])),
            "surface_style": str(conflict_packet.get("surface_style", "")),
            "active_templates": active_templates,
        }

    def _build_social_packet(
        self,
        scene_state: Any,
        relationship_book: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.social.build_social_packet(
            scene_state, relationship_book, player_name, player_pov
        )

    @staticmethod
    def _build_semantic_social_packet(
        social_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(social_packet, dict):
            return {}
        visible_relations = []
        for item in social_packet.get("visible_relations", []) or []:
            if not isinstance(item, dict):
                continue
            visible_relations.append(
                {
                    key: deepcopy(item.get(key))
                    for key in (
                        "actor",
                        "toward_viewer_states",
                        "viewer_toward_actor_states",
                        "relationship_bits",
                        "relationship_id",
                    )
                    if key in item
                }
            )
        return {
            "viewer": social_packet.get("viewer"),
            "visible_relations": visible_relations,
        }

    def _build_motive_packet(
        self,
        scene_state: Any,
        scenario: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        social_packet: Dict[str, Any],
        entities: Dict[str, Entity] | None = None,
        relationship_book: Any = None,
    ) -> Dict[str, Any]:
        return self.social.build_motive_packet(
            scene_state,
            scenario,
            player_name,
            player_pov,
            social_packet,
            entities or {},
            relationship_book,
        )

    def _build_intent_focus_packet(
        self,
        intents: List[Dict[str, Any]],
        player_name: Any,
        player_intent: Any,
        reaction_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.proposals.build_focus_packet(
            intents,
            player_name,
            player_intent,
            reaction_context,
        )

    def _score_actor_pressure(
        self,
        actor_state: Dict[str, Any],
        toward_viewer: Dict[str, Any],
    ) -> int:
        return self.social.score_pressure(actor_state, toward_viewer)

    def _refresh_situations(
        self,
        scene_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        current_step: int,
    ) -> Dict[str, Any]:
        return self.storylets.refresh_situations(
            scene_state=scene_state,
            player_name=player_name,
            player_pov=player_pov,
            current_step=current_step,
        )

    def _build_frontstage_situation(
        self,
        scene_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.storylets._frontstage(
            scene_state, player_name, player_pov
        )

    def _collect_situation_tags(
        self,
        kind: str,
        phase: str,
        location_kind: str,
        visibility: str,
        content_tags: Any = None,
    ) -> List[str]:
        return self.storylets._collect_situation_tags(
            kind, phase, location_kind, visibility, content_tags
        )

    def _situation_sort_key(self, item: Dict[str, Any]) -> Any:
        return self.storylets._situation_sort_key(item)

    def _dedupe_texts(self, items: List[Any]) -> List[str]:
        return self.storylets._dedupe_texts(items)

    def _build_storylet_packet(
        self,
        scene_state: Any,
        active_storylets: List[Dict[str, Any]],
        current_step: int,
        situation_packet: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return self.storylets.build_packet(
            scene_state=scene_state,
            active_storylets=active_storylets,
            current_step=current_step,
            situation_packet=situation_packet,
        )


    def _pick_salient_storylet(
        self,
        priority_storylets: List[Dict[str, Any]],
        recent_template_ids: set[str],
        focus_tags: set[str],
    ) -> Dict[str, Any]:
        return self.storylets.pick_salient(
            priority_storylets=priority_storylets,
            recent_template_ids=recent_template_ids,
            focus_tags=focus_tags,
        )

    def _assess_intent_legality(
        self,
        scene_state: Any,
        physics_profile: str,
        intent_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.legality.assess_intent(
            scene_state, physics_profile, intent_item
        )

    def _select_conflict_templates(
        self,
        templates: List[Any],
        day_phase: str,
        current_step: int,
    ) -> List[Dict[str, Any]]:
        return self.conflicts.select_templates(templates, day_phase, current_step)

    def _record_conflict_result(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        self.conflicts.record_result(scene_state, context, result)

    def _apply_relation_drift(
        self,
        scene_state: Any,
        relationship_book: Any,
        result: Dict[str, Any],
        player_name: Any,
    ) -> None:
        self.social.apply_relation_updates(scene_state, relationship_book, result)

    def _is_visible_conflict(self, result: Dict[str, Any], conflict_level: str) -> bool:
        return self.conflicts.is_visible(result, conflict_level)

    def _find_path(self, scene_state: Any, start: str, target: str) -> List[str]:
        return self.legality.find_path(scene_state, start, target)

    def _resolve_storylets(
        self,
        scene_state: Any,
        scenario: Any,
        situation_packet: Dict[str, Any] = None,
        tracking: Any = None,
    ) -> List[Dict[str, Any]]:
        return self.storylets.resolve(
            scene_state=scene_state,
            scenario=scenario,
            situation_packet=situation_packet,
            tracking=tracking,
        )

    def _storylet_requires_situation_route(self, storylet: Any) -> bool:
        return self.storylets.requires_situation_route(storylet)

    def _match_storylet_to_situations(
        self,
        storylet: Any,
        situation_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        return self.storylets.match_situations(storylet, situation_packet)
