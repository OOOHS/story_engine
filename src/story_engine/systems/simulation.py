from copy import deepcopy
from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.environment.character_lifecycle import CharacterLifecycle
from src.story_engine.environment.character_entries import CharacterEntryAuthority
from src.story_engine.environment.contracts import ContractDynamics
from src.story_engine.environment.world_transaction import (
    TransactionResult,
    WorldStateTransaction,
)
from src.story_engine.narrative import (
    ConflictDirector,
    StoryletEngine,
    TimelineEngine,
)
from src.story_engine.rules import LegalityEngine
from src.story_engine.social import SocialDynamics
from src.story_engine.simulation import (
    AffordanceActionResolver,
    EvidenceObservationResolver,
    ClaimCommunicationResolver,
    CommunicationResolver,
    ObjectDeliveryResolver,
    AgreementActionResolver,
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
        self.timeline = TimelineEngine()
        self.legality = LegalityEngine()
        self.conflicts = ConflictDirector()
        self.social = SocialDynamics()
        self.characters = CharacterLifecycle()
        self.character_entries = CharacterEntryAuthority()
        self.contracts = ContractDynamics()
        self.transaction = WorldStateTransaction()
        self.proposals = ProposalArbiter()
        self.resource_contests = ResourceContestResolver()
        self.uncertain_outcomes = UncertainOutcomeResolver()
        self.affordance_actions = AffordanceActionResolver()
        self.evidence_observations = EvidenceObservationResolver()
        self.communications = CommunicationResolver()
        self.claim_communications = ClaimCommunicationResolver()
        self.object_deliveries = ObjectDeliveryResolver()
        self.agreement_actions = AgreementActionResolver()
        self.route_communications = RouteCommunicationResolver()
        self.authority = SemanticAuthorityFilter()

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        for name, entity in list(entities.items()):
            simulation = entity.get_component("SimulationControl")
            if not simulation:
                continue

            scene_state = entity.get_component("SceneState")
            drama_state = entity.get_component("DramaState")
            relation_registry = context.get("relation_registry") or getattr(
                context.get("agreement_registry"), "relation_registry", None
            )
            if relation_registry is not None:
                context["relation_registry"] = relation_registry
            relation_before = relation_registry.snapshot() if relation_registry else None
            relationship_book = (
                relation_registry.to_relationship_book() if relation_registry else None
            )
            scenario = getattr(simulation, "scenario", None)
            player_name = scenario.player_character_name if scenario else None
            current_step = context.get("clock").current_step if context.get("clock") else 0
            timeline_packet = self._refresh_timeline(scene_state, context, player_name=player_name)
            phase_transition = dict(timeline_packet.get("phase_transition", {}) or {})
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
                timeline_packet=timeline_packet,
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
            )
            storylet_packet = self._build_storylet_packet(
                scene_state=scene_state,
                active_storylets=active_storylets,
                current_step=current_step,
                situation_packet=situation_packet,
            )
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
                timeline_packet=timeline_packet,
                entities=entities,
                relationship_book=relationship_book,
            )
            reaction_context = self._build_reaction_context(
                player_name,
                player_pov,
                player_intent,
                social_packet,
                timeline_packet=timeline_packet,
            )
            intent_focus = self._build_intent_focus_packet(
                intents=context.get("intents", []),
                player_name=player_name,
                player_intent=player_intent,
                timeline_packet=timeline_packet,
                reaction_context=reaction_context,
            )
            legality_context = self._build_legality_context(
                scene_state=scene_state,
                scenario=scenario,
                intents=context.get("intents", []),
                entities=entities,
            )
            director_packet = drama_state.build_directive() if drama_state else {}
            conflict_packet = self._build_conflict_packet(
                scene_state=scene_state,
                scenario=scenario,
                current_step=current_step,
                reaction_context=reaction_context,
                storylet_packet=storylet_packet,
                timeline_packet=timeline_packet,
                director_packet=director_packet,
            )
            semantic_social = self._build_semantic_social_packet(social_packet)
            input_payload = {
                "current_step": current_step,
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
                "obligation_context": self._build_obligation_context(
                    entities,
                    context.get("intents", []),
                    current_step,
                ),
                "modifier_catalog": list(context.get("modifier_catalog", [])),
                "claim_catalog": (
                    context["claim_registry"].gm_catalog()
                    if context.get("claim_registry") is not None
                    else []
                ),
                "agreement_snapshot": (
                    context["agreement_registry"].to_book().model_dump(mode="json")
                    if context.get("agreement_registry") is not None
                    else {}
                ),
                "character_entry_authorizations": list(
                    context.get("character_spawn_authorizations", [])
                ),
                "storylet_opportunities": self._build_storylet_opportunities(
                    active_storylets
                ),
                "narrative_pressure": {
                    "directive": str(director_packet.get("directive", "") or ""),
                    "instruction": str(director_packet.get("instruction", "") or ""),
                    "tension": director_packet.get("tension"),
                }
                if director_packet
                else {},
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
                        movement_authorizations=(
                            self._movement_authorizations(legality_context)
                        ),
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
            )
            # The GM may still have opinions about whether these actors'
            # utterances "succeeded" -- discard them unconditionally. Only
            # its knowledge_updates/social_impacts for these actors (content,
            # not delivery) survive untouched.
            result["resolved_actions"] = [
                action
                for action in result.get("resolved_actions", []) or []
                if not (
                    isinstance(action, dict)
                    and str(action.get("actor", "")).strip()
                    in communication_resolution.consumed_actors
                )
            ]
            result["resolved_actions"].extend(
                communication_resolution.resolved_actions
            )
            context["communication_traces"] = [
                dict(action) for action in communication_resolution.resolved_actions
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
                (
                    "agreement_action_traces",
                    self.agreement_actions,
                    {
                        "intents": intents,
                        "scenario": scenario,
                        "current_step": current_step,
                    },
                ),
            ):
                resolution = resolver.resolve(result, **kwargs)
                result = resolution.result
                context[trace_key] = list(resolution.traces)
            agreement_registry = context.get("agreement_registry")
            agreement_book = (
                agreement_registry.to_book()
                if agreement_registry
                else None
            )
            proposal_actors = {
                str(item.get("actor", "")).strip()
                for item in context.get("intents", [])
                if isinstance(item, dict)
                and str(item.get("actor", "")).strip()
            }
            existing_obligation_states = {
                entity_name: obligations
                for entity_name, character_entity in entities.items()
                if (
                    obligations := character_entity.get_component("ObligationState")
                ) is not None
            }
            contract_resolution = self.contracts.resolve(
                agreement_book,
                scene_state,
                existing_obligation_states,
                result,
                current_step=current_step,
                proposal_actors=proposal_actors,
            )
            result = contract_resolution.result
            result = self.resource_contests.resolve(
                scene_state,
                result,
                intents=context.get("intents", []),
            )
            entry_resolution = self.character_entries.resolve(
                result.get("spawn_character"),
                authorizations=context.get("character_spawn_authorizations", []),
                scene_state=scene_state,
                current_step=current_step,
            )
            context["character_entry_rejections"] = list(entry_resolution.rejected)
            result["spawn_character"] = entry_resolution.request
            spawn_preparation = self.characters.prepare(
                entities,
                scene_state,
                entry_resolution.request,
                agent_runtime=getattr(scenario, "default_agent_runtime", ""),
                player_name=player_name,
                agent_registry=context.get("agent_registry"),
                memory_namespace=context.get("memory_namespace"),
            )
            spawn_plan = spawn_preparation.plan
            drive_states = {
                entity_name: drive
                for entity_name, character_entity in entities.items()
                if (drive := character_entity.get_component("DriveState")) is not None
            }
            if spawn_plan is not None:
                prepared_drive = spawn_plan.entity.get_component("DriveState")
                if prepared_drive is not None:
                    drive_states[spawn_plan.name] = prepared_drive
            obligation_states = {
                entity_name: obligations
                for entity_name, character_entity in entities.items()
                if (
                    obligations := character_entity.get_component("ObligationState")
                ) is not None
            }
            if spawn_plan is not None:
                prepared_obligations = spawn_plan.entity.get_component("ObligationState")
                if prepared_obligations is not None:
                    obligation_states[spawn_plan.name] = prepared_obligations
            spawned: List[str] = []
            preparation_errors = list(outcome_errors) + list(
                spawn_preparation.errors
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
                    drive_states=drive_states,
                    obligation_states=obligation_states,
                    current_step=current_step,
                    proposal_actors=proposal_actors,
                    agreement_book=agreement_book,
                    emergent_meter_budget=int(
                        getattr(scenario, "emergent_meter_budget", 0) or 0
                    ),
                )
                if transaction_result.committed and spawn_plan is not None:
                    try:
                        register_agent = context.get("register_agent")
                        if not callable(register_agent):
                            raise RuntimeError("agent registration callback is unavailable")
                        spawned = self.characters.finalize(
                            entities,
                            spawn_plan,
                            register_agent=register_agent,
                            unregister_agent=context.get("unregister_agent"),
                            agent_registry=context.get("agent_registry"),
                        )
                    except Exception as exc:
                        if transaction_result.checkpoint:
                            transaction_result.checkpoint.restore()
                        transaction_result = TransactionResult(
                            False,
                            [f"spawn_character finalization rolled back: {exc}"],
                        )
                if transaction_result.committed and relation_registry is not None:
                    try:
                        relation_registry.apply_relationship_book(
                            relationship_book, entities
                        )
                        if agreement_registry is not None:
                            agreement_registry.apply_book(agreement_book, entities)
                    except Exception as exc:
                        if transaction_result.checkpoint:
                            transaction_result.checkpoint.restore()
                        if relation_before is not None:
                            relation_registry.restore(relation_before, entities)
                        transaction_result = TransactionResult(
                            False,
                            [f"agreement entity publication rolled back: {exc}"],
                        )
                if transaction_result.committed and scene_state is not None:
                    result["storylet_hits"] = self.storylets.detect_hits(
                        active_storylets, result
                    )
                    consumed_storylet_ids = self.storylets.consumable_hits(
                        scenario,
                        result["storylet_hits"],
                    )
                    narrative_director = entity.get_component("NarrativeDirector")
                    if narrative_director is not None:
                        self._run_narrative_director(
                            narrative_director,
                            scene_state=scene_state,
                            result=result,
                            director_packet=director_packet,
                            active_storylets=active_storylets,
                            current_step=current_step,
                            context=context,
                        )
                    if consumed_storylet_ids:
                        consumed = list(scene_state.get_scene_flag("consumed_storylets", []) or [])
                        for storylet_id in consumed_storylet_ids:
                            if storylet_id not in consumed:
                                consumed.append(storylet_id)
                        scene_state.update_scene_flags({"consumed_storylets": consumed})
                if not transaction_result.committed:
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

            if transaction_result.committed and scene_state is not None:
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
                timeline_packet = self._finalize_timeline(scene_state, context, player_name)
                if phase_transition:
                    timeline_packet["phase_transition"] = phase_transition
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
                    timeline_packet=timeline_packet,
                    current_step=current_step,
                )
                motive_packet = self._build_motive_packet(
                    scene_state=scene_state,
                    scenario=scenario,
                    player_name=player_name,
                    player_pov=player_pov,
                    social_packet=social_packet,
                    timeline_packet=timeline_packet,
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
            context["director_packet"] = director_packet
            context["active_storylets"] = active_storylets
            context["timeline"] = timeline_packet
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
            context["obligation_context"] = self._build_obligation_context(
                entities,
                context.get("intents", []),
                current_step,
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
            context["contract_resolution_errors"] = list(
                contract_resolution.errors
            )
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

    def _build_obligation_context(
        self,
        entities: Dict[str, Entity],
        intents: List[Dict[str, Any]],
        current_step: int,
    ) -> Dict[str, Any]:
        relevant_names = {
            str(item.get("actor", "")).strip()
            for item in intents or []
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        packet = {}
        for name in relevant_names:
            entity = entities.get(name)
            obligations = entity.get_component("ObligationState") if entity else None
            if obligations and hasattr(obligations, "get_private_snapshot"):
                packet[name] = obligations.get_private_snapshot(current_step)
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

    @staticmethod
    def _movement_authorizations(
        legality_context: Any,
    ) -> Dict[str, str]:
        checks = (
            legality_context.get("checks", [])
            if isinstance(legality_context, dict)
            else []
        )
        return {
            str(check.get("actor", "")).strip(): str(
                check.get("rewrite_location", "") or ""
            ).strip()
            for check in checks
            if isinstance(check, dict)
            and str(check.get("actor", "")).strip()
            and str(check.get("rule", "")).strip() == "movement"
            and str(check.get("verdict", "allow")).strip() == "allow"
            and str(check.get("rewrite_location", "") or "").strip()
        }

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
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.social.build_reaction_context(
            player_name, player_pov, player_intent, social_packet, timeline_packet
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
        timeline_packet: Dict[str, Any],
        director_packet: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return self.conflicts.build_packet(
            scene_state,
            scenario,
            current_step,
            reaction_context,
            storylet_packet,
            timeline_packet,
            director_packet,
        )

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
            "allow_unsignaled_touch": bool(
                social_packet.get("allow_unsignaled_touch", False)
            ),
        }

    def _build_motive_packet(
        self,
        scene_state: Any,
        scenario: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        social_packet: Dict[str, Any],
        timeline_packet: Dict[str, Any],
        entities: Dict[str, Entity] | None = None,
        relationship_book: Any = None,
    ) -> Dict[str, Any]:
        return self.social.build_motive_packet(
            scene_state,
            scenario,
            player_name,
            player_pov,
            social_packet,
            timeline_packet,
            entities or {},
            relationship_book,
        )

    def _build_intent_focus_packet(
        self,
        intents: List[Dict[str, Any]],
        player_name: Any,
        player_intent: Any,
        timeline_packet: Dict[str, Any],
        reaction_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.proposals.build_focus_packet(
            intents,
            player_name,
            player_intent,
            timeline_packet,
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
        timeline_packet: Dict[str, Any],
        current_step: int,
    ) -> Dict[str, Any]:
        return self.storylets.refresh_situations(
            scene_state=scene_state,
            player_name=player_name,
            player_pov=player_pov,
            timeline_packet=timeline_packet,
            current_step=current_step,
        )

    def _build_frontstage_situation(
        self,
        scene_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.storylets._frontstage(
            scene_state, player_name, player_pov, timeline_packet
        )

    def _build_commitment_situations(
        self,
        scene_state: Any,
        player_name: Any,
        timeline_packet: Dict[str, Any],
        current_step: int,
    ) -> List[Dict[str, Any]]:
        return self.storylets._commitments(
            scene_state, player_name, timeline_packet, current_step
        )

    def _build_transition_situation(
        self,
        player_name: Any,
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.storylets._transition(player_name, timeline_packet)

    def _build_aftermath_situation(
        self,
        player_name: Any,
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.storylets._aftermath(player_name, timeline_packet)

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

    def _build_storylet_opportunities(
        self,
        active_storylets: List[Dict[str, Any]],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Trim active_storylets down to what a resolver needs to decide
        whether a director_signal is warranted: what the opportunity is,
        who it concerns, what's at stake. Not authoritative content and not
        an instruction -- the resolver can act on it, ignore it, or note
        that no in-scene actor is a good fit.
        """
        opportunities: List[Dict[str, Any]] = []
        for item in (active_storylets or [])[:limit]:
            if not isinstance(item, dict):
                continue
            beat = item.get("beat", {}) if isinstance(item.get("beat", {}), dict) else {}
            opportunities.append(
                {
                    "storylet_id": item.get("storylet_id", ""),
                    "intent": item.get("intent", ""),
                    "tags": list(item.get("tags", [])),
                    "kind": item.get("kind") or "",
                    "preferred_actors": list(beat.get("preferred_actors", [])),
                    "target_actor": beat.get("target_actor"),
                    "stake": beat.get("stake", ""),
                    "visibility": beat.get("visibility", "public"),
                }
            )
        return opportunities

    def _run_narrative_director(
        self,
        narrative_director: Any,
        *,
        scene_state: Any,
        result: Dict[str, Any],
        director_packet: Dict[str, Any],
        active_storylets: List[Dict[str, Any]],
        current_step: int,
        context: Dict[str, Any],
    ) -> None:
        """Run narrative intuition strictly after this tick's commit.

        Never raises: an unavailable or malformed response just means no
        new beat/signal this tick, not a rolled-back one. The tick already
        committed; this pass is a bonus, not a dependency.
        """
        try:
            payload = {
                "committed_facts": {
                    "resolved_actions": result.get("resolved_actions", []),
                    "social_impacts": result.get("social_impacts", []),
                    "object_lifecycle": result.get("object_lifecycle", []),
                    "storylet_hits": result.get("storylet_hits", []),
                    "tension_delta": result.get("tension_delta", 0.0),
                    "conflict_level": result.get("conflict_level", "none"),
                },
                "storylet_opportunities": self._build_storylet_opportunities(
                    active_storylets
                ),
                "narrative_pressure": {
                    "directive": str(director_packet.get("directive", "") or ""),
                    "instruction": str(director_packet.get("instruction", "") or ""),
                    "tension": director_packet.get("tension"),
                }
                if director_packet
                else {},
            }
            narrative_result = narrative_director.direct(payload)
        except Exception:
            return
        if not isinstance(narrative_result, dict):
            return
        filtered = self.authority.sanitize(narrative_result)
        context.setdefault("semantic_authority_rejections", []).extend(
            filtered.rejected_writes
        )
        director_signals = filtered.result.get("director_signals", [])
        result["director_signals"] = director_signals
        for signal in director_signals:
            if not isinstance(signal, dict):
                continue
            actor = str(signal.get("actor", "")).strip()
            if scene_state is None or actor not in scene_state.actor_states:
                continue
            scene_state.queue_director_signal(
                actor,
                str(signal.get("suggestion", "")),
                current_step=int(current_step),
                source_ref=str(signal.get("source_ref", "")),
                tags=list(signal.get("tags", []) or []),
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

    def _detect_mundane_violation(self, intent: str, actor_state: Dict[str, Any]) -> str:
        return self.legality.detect_mundane_violation(intent, actor_state)

    def _assess_movement_legality(
        self,
        scene_state: Any,
        actor: str,
        intent: str,
        current_location: Any,
    ) -> Any:
        return self.legality.assess_movement(
            scene_state, actor, intent, current_location
        )

    def _extract_target_location(self, scene_state: Any, intent: str, current_location: Any) -> Any:
        return self.legality.extract_target_location(
            scene_state, intent, current_location
        )

    def _find_path(self, scene_state: Any, start: str, target: str) -> List[str]:
        return self.legality.find_path(scene_state, start, target)

    def _refresh_timeline(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        player_name: Any = None,
    ) -> Dict[str, Any]:
        return self.timeline.refresh(scene_state, context, player_name)

    def _finalize_timeline(self, scene_state: Any, context: Dict[str, Any], player_name: Any) -> Dict[str, Any]:
        return self.timeline.finalize(scene_state, context, player_name)

    def _build_transition_pressure(
        self,
        scene_state: Any,
        commitments: List[Dict[str, Any]],
        current_step: int,
        player_name: Any,
    ) -> Dict[str, Any]:
        return self.timeline.build_transition_pressure(
            scene_state, commitments, current_step, player_name
        )

    def _resolve_transition_carriers(
        self,
        commitment: Dict[str, Any],
        same_scene_states: Dict[str, Dict[str, Any]],
        player_name: Any,
    ) -> List[str]:
        return self.timeline.resolve_transition_carriers(
            commitment, same_scene_states, player_name
        )

    def _resolve_storylets(
        self,
        scene_state: Any,
        scenario: Any,
        situation_packet: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        return self.storylets.resolve(
            scene_state=scene_state,
            scenario=scenario,
            situation_packet=situation_packet,
        )

    def _storylet_requires_situation_route(self, storylet: Any) -> bool:
        return self.storylets.requires_situation_route(storylet)

    def _match_storylet_to_situations(
        self,
        storylet: Any,
        situation_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        return self.storylets.match_situations(storylet, situation_packet)

    def _normalize_phase_schedule(self, items: Any) -> List[Dict[str, Any]]:
        return self.timeline.normalize_phase_schedule(items)

    def _normalize_commitments(self, items: Any) -> List[Dict[str, Any]]:
        return self.timeline.normalize_commitments(items)

    def _resolve_day_phase(
        self,
        current_step: int,
        phase_schedule: List[Dict[str, Any]],
        current_phase: Any,
    ) -> str:
        return self.timeline.resolve_day_phase(current_step, phase_schedule, current_phase)

    def _resolve_phase_turn(
        self,
        current_step: int,
        phase_schedule: List[Dict[str, Any]],
        day_phase: str,
    ) -> int:
        return self.timeline.resolve_phase_turn(current_step, phase_schedule, day_phase)
