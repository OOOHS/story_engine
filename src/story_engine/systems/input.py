import re
from copy import deepcopy
from typing import Dict, Any
from src.story_engine.agents import (
    AgentDecision,
    AgentPerception,
    AgentScheduler,
    runtime_owns_subjective_state,
)
from src.story_engine.agents.actions import AgentAction, parse_natural_language_action
from src.story_engine.agents.commitment import (
    commit_runtime_action,
    repetition_signature,
    repetition_target,
)
from src.story_engine.agents.memory_context import AgentMemoryContextBuilder
from src.story_engine.motivation import NeedDynamics
from src.story_engine.narrative import TimelineEngine
from src.story_engine.environment.physical_affordances import (
    PhysicalAffordanceEngine,
)
from src.story_engine.common.action_target import bind_action_target
from src.story_engine.environment.narrative_candidates import (
    drain_due_director_authorizations,
)
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity


class InputSystem(System):
    """
    Collect free-form intents from the player and autonomous actors.
    This is the Input phase of the engine loop.
    """
    def __init__(self) -> None:
        super().__init__()
        self.scheduler = AgentScheduler()
        self.needs = NeedDynamics()
        self.physical_affordances = PhysicalAffordanceEngine()
        self.memory_context = AgentMemoryContextBuilder()
        self.timeline = TimelineEngine()

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        dispatcher = context.get("dispatcher")
        intents_buffer = context.setdefault("intents", [])
        scene_state = self._get_scene_state(entities)
        player_name = context.get("player_name")
        agent_registry = context.get("agent_registry")
        action_queue = context.get("action_queue")
        relation_registry = context.get("relation_registry")
        relationship_book = (
            relation_registry.to_relationship_book() if relation_registry else None
        )
        context["_relationship_book_view"] = relationship_book
        player_location = scene_state.get_actor_location(player_name) if scene_state and player_name else None
        clock = context.get("clock")
        current_step = (
            int(action_queue.current_time)
            if action_queue is not None
            else clock.current_step if clock else 0
        )
        activation_trace = context.setdefault("agent_activations", {})
        context["proposal_semantics"] = "simultaneous"
        context["character_spawn_authorizations"] = []
        context["storylet_definition_authorizations"] = []
        context["topology_candidate_authorizations"] = []
        context["interrupted_actions"] = []
        self._drain_director_authorizations(scene_state, context, current_step)

        for event in context.get("inject_events", []):
            event_data = event if isinstance(event, dict) else {"intent": str(event)}
            content = str(
                event_data.get("intent") or event_data.get("content") or ""
            ).strip()
            if not content:
                continue
            self._register_character_entry_authorization(
                context,
                event_data.get("character_entry"),
                fallback_id=event_data.get("event_id"),
                source="injected",
                current_step=current_step,
            )
            self._register_storylet_definition_authorization(
                context,
                event_data.get("storylet_definition"),
                fallback_id=event_data.get("event_id"),
                source="injected",
                current_step=current_step,
            )
            self._register_topology_candidate_authorization(
                context,
                event_data.get("topology_candidate"),
                fallback_id=event_data.get("event_id"),
                source="injected",
                current_step=current_step,
            )
            intents_buffer.append(
                {
                    "actor": "World",
                    "intent": content,
                    "thought": "",
                    "source": "injected",
                    "location": event_data.get("location") or player_location,
                    "visibility": event_data.get("visibility", "local"),
                    "tags": list(event_data.get("tags", []) or []),
                    "event_id": event_data.get("event_id"),
                    "proposal_role": "world_event",
                    "proposal_priority": float(event_data.get("priority", 0.9) or 0.9),
                    "action": AgentAction(
                        kind="interact",
                        detail=content,
                        target=str(event_data.get("location") or ""),
                    ).to_dict(),
                    "based_on_world_version": (
                        int(scene_state.get_scene_flag("world_version", 0) or 0)
                        if scene_state
                        else 0
                    ),
                }
            )

        self._inject_commitment_events(scene_state, context, intents_buffer, player_location)

        ordered_entities = self._order_entities(entities, player_name)
        for name, entity in ordered_entities:
            if entity.get_component("SimulationControl"):
                continue
            if not entity.get_component("AgentController"):
                continue

            intent = None
            action_spec = None
            thought = ""
            source = "ai"
            actor_location = scene_state.get_actor_location(name) if scene_state else None
            is_player = bool(player_name and name == player_name)
            if action_queue is not None and action_queue.is_busy(name):
                interrupted = self._preempt_for_critical_attention(
                    entity, action_queue, current_step
                )
                if interrupted is None:
                    activation_trace[name] = {
                        "active": False,
                        "scope": "busy",
                        "reason": "action_in_progress",
                        "busy_until": action_queue.busy_until(name),
                        "pending_action": action_queue.pending_for(name),
                    }
                    continue
                context["interrupted_actions"].append(interrupted)
            activation = self.scheduler.activation_for(
                entity,
                step=current_step,
                actor_location=actor_location,
                player_location=player_location,
                proposals=intents_buffer,
                is_player=is_player,
                has_manual_override=name in overrides,
                scene_state=scene_state,
            )
            activation_trace[name] = {
                "active": activation.active,
                "scope": activation.scope,
                "reason": activation.reason,
            }
            if not activation.active:
                continue

            if name in overrides:
                perception = self.build_agent_perception(
                    entity=entity,
                    scene_state=scene_state,
                    intents_buffer=intents_buffer,
                    context=context,
                    activation_scope="manual",
                )
                context.setdefault("manual_perceptions", {})[
                    name
                ] = perception.manual_decision_context()
                self._acknowledge_perception_attention(entity, perception)
                manual_action = parse_natural_language_action(
                    overrides[name], field=f"manual action for {name}"
                )
                intent = manual_action.detail
                action_spec = manual_action
                source = "manual"
                controller = entity.get_component("AgentController")
                if intent and controller is not None:
                    controller.record_decision(current_step)
                print(f"> {name} (INTENT/MANUAL): {intent}")
                self.logger.info(f"{name} (INTENT/MANUAL): {intent}")
            elif is_player and context.get("allow_auto_player") is False:
                # Players default to the same autonomous proposal loop as other actors.
                # The only special case is an explicit manual override for the turn.
                continue
            else:
                print(f"... {name} is thinking ...", end="\r", flush=True)
                if not agent_registry or not agent_registry.is_registered(entity):
                    print(" " * 50 + "\r", end="", flush=True)
                    activation_trace[name] = {
                        "active": False,
                        "scope": activation.scope,
                        "reason": "missing_agent_runtime",
                    }
                    context.setdefault("agent_registration_errors", []).append(name)
                    continue
                perception = self.build_agent_perception(
                    entity=entity,
                    scene_state=scene_state,
                    intents_buffer=intents_buffer,
                    context=context,
                    activation_scope=activation.scope,
                )
                result = agent_registry.decide(entity, perception)
                controller = entity.get_component("AgentController")
                if controller is not None:
                    controller.record_decision(current_step)
                self._acknowledge_perception_attention(entity, perception)
                thought = result.thought
                commitment = commit_runtime_action(result)
                action_spec = commitment.action
                intent = action_spec.detail
                if controller is not None:
                    controller.record_policy_action(
                        repetition_signature(action_spec),
                        repetition_target(action_spec),
                    )
                if activation.reason.startswith("agent_goal:"):
                    controller = entity.get_component("AgentController")
                    if controller is not None:
                        goal_id = activation.reason.removeprefix("agent_goal:")
                        signature = (
                            f"{action_spec.kind}|"
                            f"{str(action_spec.target or '').strip().casefold()}"
                        )
                        if controller.last_goal_wakeup_id != goal_id:
                            controller.goal_continuation_attempts = 0
                            controller.repeated_goal_action_count = 0
                            controller.last_goal_action_signature = ""
                        controller.goal_continuation_attempts += 1
                        controller.repeated_goal_action_count = (
                            controller.repeated_goal_action_count + 1
                            if controller.last_goal_action_signature == signature
                            else 1
                        )
                        controller.last_goal_action_signature = signature
                        controller.last_goal_wakeup_step = int(current_step)
                        controller.last_goal_wakeup_id = goal_id
                context.setdefault("policy_traces", {})[name] = commitment.trace
                self._apply_agent_private_updates(
                    entity,
                    dict(result.metadata or {}),
                    current_step,
                )
                self._collect_agent_goal_request(
                    name,
                    result.metadata,
                    context,
                    perception,
                )
                self._collect_agent_sentiment_updates(
                    name,
                    result.metadata,
                    context,
                )
                self._collect_agent_motive_refs(
                    name,
                    entity,
                    result.metadata,
                    context,
                )
                print(" " * 50 + "\r", end="", flush=True)
                # Registered agents own their policy. The engine may normalize
                # presentation, but cannot rewrite a decision to manufacture a
                # desired scene or compensate for a missing runtime.
                intent = self._sanitize_auto_intent(intent, is_player=is_player)

                if thought:
                    print(f"{name} (Thought): {thought}")
                    self.logger.info(f"{name} (Thought): {thought}")

                if intent:
                    print(f"> {name} (INTENT): {intent}")
                    self.logger.info(f"{name} (INTENT): {intent}")

            if not intent:
                continue

            if action_spec is None:
                action_spec = parse_natural_language_action(intent, field=f"action for {name}")

            target_binding = bind_action_target(
                action_spec,
                actor_name=name,
                perception=perception,
            )
            action_spec = target_binding.action
            if target_binding.status in {"ambiguous", "absent"} and action_spec.kind in {
                "move",
                "interact",
                "communicate",
            }:
                context.setdefault("action_target_bindings", []).append(
                    {
                        "actor": name,
                        "status": target_binding.status,
                        "candidates": list(target_binding.candidates),
                        "detail": action_spec.detail,
                    }
                )

            action_payload = action_spec.to_dict()
            affordance_id = self._validated_affordance_reference(
                action_spec, perception
            )
            if action_spec.affordance_id and not affordance_id:
                action_payload.pop("affordance_id", None)
                context.setdefault("agent_action_reference_rejections", []).append(
                    {
                        "actor": name,
                        "reference_kind": "affordance",
                        "object_id": action_spec.target,
                        "affordance_id": action_spec.affordance_id,
                    }
                )
            claim_reference = self._validated_claim_communication_reference(
                action_spec, perception
            )
            if action_spec.claim_id and not claim_reference:
                for key in ("claim_id", "claim_stance", "evidence_refs"):
                    action_payload.pop(key, None)
                context.setdefault("agent_action_reference_rejections", []).append(
                    {
                        "actor": name,
                        "reference_kind": "claim_communication",
                        "claim_id": action_spec.claim_id,
                        "target": action_spec.target,
                    }
                )
            delivery_recipient = self._validated_delivery_reference(
                action_spec, perception
            )
            if action_spec.delivery_recipient and not delivery_recipient:
                action_payload.pop("delivery_recipient", None)
                context.setdefault("agent_action_reference_rejections", []).append(
                    {
                        "actor": name,
                        "reference_kind": "object_delivery",
                        "object_id": action_spec.target,
                        "recipient": action_spec.delivery_recipient,
                    }
                )
            route_reference = self._validated_route_reference(
                action_spec, perception
            )
            if (action_spec.route_source or action_spec.route_target) and not route_reference:
                action_payload.pop("route_source", None)
                action_payload.pop("route_target", None)
                context.setdefault("agent_action_reference_rejections", []).append(
                    {
                        "actor": name,
                        "reference_kind": "route_communication",
                        "route_source": action_spec.route_source,
                        "route_target": action_spec.route_target,
                    }
                )
            if action_spec.route_path and not route_reference:
                action_payload.pop("route_path", None)

            intent_record = {
                "actor": name,
                "intent": intent,
                "action": action_payload,
                "action_kind": action_spec.kind,
                "action_target": action_spec.target,
                "action_affordance_id": affordance_id,
                "action_claim_id": claim_reference.get("claim_id", ""),
                "action_claim_stance": claim_reference.get("claim_stance", ""),
                "action_evidence_refs": list(
                    claim_reference.get("evidence_refs", [])
                ),
                "action_delivery_recipient": delivery_recipient,
                "action_route_source": route_reference.get("source", ""),
                "action_route_target": route_reference.get("target", ""),
                "action_route_path": list(route_reference.get("path", [])),
                "thought": thought,
                "source": source,
                "location": actor_location,
                "is_player": is_player,
                "proposal_role": self._proposal_role(
                    is_player=is_player,
                    source=source,
                    activation_scope=activation.scope,
                ),
                "proposal_priority": self._proposal_priority(
                    is_player=is_player,
                    source=source,
                    activation_scope=activation.scope,
                ),
                "activation_scope": activation.scope,
                "activation_reason": activation.reason,
                "proposal_batch_step": current_step,
                "based_on_world_version": (
                    int(scene_state.get_scene_flag("world_version", 0) or 0)
                    if scene_state
                    else 0
                ),
            }
            intents_buffer.append(intent_record)
            if dispatcher:
                dispatcher.publish({"type": "intent", "agent": name, "content": intent})

    @staticmethod
    def _validated_affordance_reference(
        action: AgentAction,
        perception: AgentPerception,
    ) -> str:
        affordance_id = str(action.affordance_id or "").strip()
        if action.kind != "interact" or not affordance_id or not action.target:
            return ""
        for opportunity in perception.affordance_opportunities:
            if not isinstance(opportunity, dict):
                continue
            if not bool(opportunity.get("available", True)):
                continue
            if str(opportunity.get("object_id", "")).strip() != action.target:
                continue
            if str(opportunity.get("affordance_id", "")).strip() == affordance_id:
                return affordance_id
        return ""

    @staticmethod
    def _validated_claim_communication_reference(
        action: AgentAction,
        perception: AgentPerception,
    ) -> Dict[str, Any]:
        claim_id = str(action.claim_id or "").strip()
        if action.kind != "communicate" or not claim_id or not action.target:
            return {}
        visible_actors = {
            str(item).strip()
            for item in perception.world_view.get("visible_actors", []) or []
            if str(item).strip()
        }
        if action.target not in visible_actors or action.target == perception.actor_name:
            return {}
        claim = next(
            (
                item
                for item in perception.private_knowledge.get("claims", []) or []
                if isinstance(item, dict)
                and str(item.get("claim_id", "")).strip() == claim_id
            ),
            None,
        )
        if claim is None:
            return {}
        known_evidence = {
            str(item).strip()
            for item in claim.get("evidence_refs", []) or []
            if str(item).strip()
        }
        requested_evidence = tuple(
            str(item).strip()
            for item in action.evidence_refs
            if str(item).strip()
        )
        if not set(requested_evidence).issubset(known_evidence):
            return {}
        visible_objects = {
            str(item).strip()
            for item in perception.world_view.get("visible_objects", []) or []
            if str(item).strip()
        }
        visible_world = perception.world_view.get("visible_world", {})
        if isinstance(visible_world, dict):
            visible_objects.update(str(item).strip() for item in visible_world)
        if not set(requested_evidence).issubset(visible_objects):
            return {}
        stance = str(action.claim_stance or "").strip()
        if stance not in {"supports", "rejects", "uncertain"}:
            stance = str(claim.get("stance", "uncertain")).strip()
        return {
            "claim_id": claim_id,
            "claim_stance": (
                stance
                if stance in {"supports", "rejects", "uncertain"}
                else "uncertain"
            ),
            "evidence_refs": requested_evidence,
        }

    @staticmethod
    def _validated_delivery_reference(
        action: AgentAction,
        perception: AgentPerception,
    ) -> str:
        recipient = str(action.delivery_recipient or "").strip()
        object_id = str(action.target or "").strip()
        if action.kind != "interact" or not recipient or not object_id:
            return ""
        visible_actors = {
            str(item).strip()
            for item in perception.world_view.get("visible_actors", []) or []
            if str(item).strip()
        }
        if recipient not in visible_actors or recipient == perception.actor_name:
            return ""
        visible_world = perception.world_view.get("visible_world", {})
        state = (
            visible_world.get(object_id, {})
            if isinstance(visible_world, dict)
            else {}
        )
        if not isinstance(state, dict):
            return ""
        if str(state.get("owner") or "").strip() != perception.actor_name:
            return ""
        if bool(state.get("hidden", False)) or not bool(state.get("portable", True)):
            return ""
        quantity = state.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return ""
        return recipient

    @staticmethod
    def _validated_route_reference(
        action: AgentAction,
        perception: AgentPerception,
    ) -> Dict[str, str]:
        path = tuple(action.route_path)
        if not path and action.route_source and action.route_target:
            path = (str(action.route_source).strip(), str(action.route_target).strip())
        if (
            action.kind != "communicate"
            or len(path) < 2
            or len(path) > 8
            or len(set(path)) != len(path)
            or not action.target
            or action.target == perception.actor_name
        ):
            return {}
        visible_actors = set(perception.world_view.get("visible_actors", []) or [])
        routes = perception.private_knowledge.get("map", {}).get(
            "known_routes", {}
        )
        if action.target not in visible_actors or any(
            target not in routes.get(source, [])
            for source, target in zip(path, path[1:])
        ):
            return {}
        return {"source": path[0], "target": path[-1], "path": path}

    def build_agent_perception(
        self,
        entity: Entity,
        scene_state: Any,
        intents_buffer: Any,
        context: Dict[str, Any],
        activation_scope: str = "foreground",
    ) -> AgentPerception:
        """Build one character's bounded, POV-safe input for its agent loop."""
        actor_name = entity.name
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        world_view = scene_state.get_view_pov(actor_name) if scene_state else {}
        self_state = scene_state.get_self_actor_state(actor_name) if scene_state else {}
        actor_location = self_state.get("location") if isinstance(self_state, dict) else None

        visible_proposals = []
        world_signals = []
        for proposal in intents_buffer or []:
            if not isinstance(proposal, dict) or proposal.get("actor") == actor_name:
                continue
            # Symmetry invariant: the player is just another proposer sharing
            # this batch. Her still-uncommitted intent is exactly as invisible
            # to peers deciding in the same batch as anyone else's, so no
            # actor -- human or autonomous -- gets to read a decision before
            # it settles. Only World-originated signals (already-authoritative
            # environment events, not proposals) bypass this barrier.
            if (
                proposal.get("actor") != "World"
                and proposal.get("proposal_batch_step") == step
            ):
                continue
            location = proposal.get("location")
            if actor_location and location and location != actor_location:
                continue
            public_item = {
                "actor": proposal.get("actor"),
                "intent": proposal.get("intent", ""),
                "source": proposal.get("source", ""),
                "event_id": proposal.get("event_id"),
                "tags": list(proposal.get("tags", []) or []),
            }
            if proposal.get("actor") == "World":
                world_signals.append(public_item)
            else:
                visible_proposals.append(public_item)

        director_signals = (
            scene_state.pop_director_signals(actor_name, step)
            if scene_state and hasattr(scene_state, "pop_director_signals")
            else []
        )

        observation = entity.get_component("Observation")
        recent_observations = (
            list(observation.current_observations[-8:])
            if observation and hasattr(observation, "current_observations")
            else []
        )
        planning = entity.get_component("Planning")
        current_plan = planning.get_plan() if planning and hasattr(planning, "get_plan") else ""
        cognition = entity.get_component("Cognition")
        private_cognition = (
            cognition.get_private_snapshot(step)
            if cognition and hasattr(cognition, "get_private_snapshot")
            else {}
        )
        experience_events = []
        for experience in private_cognition.get("recent_experiences", []):
            if not isinstance(experience, dict):
                continue
            try:
                observed_step = int(experience.get("step", step))
            except (TypeError, ValueError):
                observed_step = int(step)
            for event in experience.get("events", []):
                if not isinstance(event, dict):
                    continue
                projected_event = dict(event)
                projected_event["observed_step"] = observed_step
                projected_event["age_steps"] = max(0, int(step) - observed_step)
                experience_events.append(projected_event)
        passive_observations = [
            dict(event)
            for event in experience_events
            if event.get("observation_mode", "passive") == "passive"
        ][-12:]
        active_observation_results = [
            dict(event)
            for event in experience_events
            if event.get("observation_mode") == "active"
        ][-12:]
        private_cognition = dict(private_cognition)
        private_cognition.pop("recent_experiences", None)
        drive = entity.get_component("DriveState")
        private_drives = (
            drive.get_private_snapshot()
            if drive and hasattr(drive, "get_private_snapshot")
            else {}
        )
        raw_affordance_opportunities = (
            self.needs.build_opportunities(
                scene_state,
                actor_name,
                drive,
            )
            + self.physical_affordances.build_opportunities(
                scene_state, actor_name
            )
        )
        host_action_features = {}
        affordance_opportunities = []
        for opportunity in raw_affordance_opportunities:
            if not isinstance(opportunity, dict):
                continue
            public_opportunity = dict(opportunity)
            tags = tuple(public_opportunity.pop("policy_tags", []) or [])
            object_id = str(public_opportunity.get("object_id", "")).strip()
            affordance_id = str(
                public_opportunity.get("affordance_id", "")
            ).strip()
            if object_id and affordance_id and tags:
                host_action_features[(object_id, affordance_id)] = tags
            affordance_opportunities.append(public_opportunity)
        context.setdefault("_host_action_features", {})[
            actor_name
        ] = host_action_features
        affordance_opportunities.sort(
            key=lambda item: (
                not bool(item.get("available", True)),
                -float(item.get("relief_score", 0.0) or 0.0),
                item.get("source") == "engine_physics",
                str(item.get("object_id", "")),
                str(item.get("affordance_id", "")),
            )
        )
        traits = entity.get_component("TraitState")
        private_traits = (
            traits.get_private_snapshot()
            if traits and hasattr(traits, "get_private_snapshot")
            else {}
        )
        sentiments = entity.get_component("SentimentState")
        private_sentiments = (
            sentiments.get_private_snapshot()
            if sentiments and hasattr(sentiments, "get_private_snapshot")
            else {}
        )
        relationship_book = context.get("_relationship_book_view")
        if relationship_book is None and context.get("relation_registry"):
            relationship_book = context["relation_registry"].to_relationship_book()
        relationship_context = {
            "visible_relations": (
                relationship_book.get_visible_relations(
                    actor_name,
                    world_view.get("visible_actors", []),
                    world_view.get("visible_actor_states", {}),
                )
                if relationship_book
                else []
            )
        }
        goals = entity.get_component("GoalState")
        private_goals = (
            goals.get_private_snapshot()
            if goals and hasattr(goals, "get_private_snapshot")
            else {}
        )
        controller = entity.get_component("AgentController")
        active_goal_ids = {
            str(item.get("goal_id", ""))
            for item in private_goals.get("active", [])
            if isinstance(item, dict)
        }
        if (
            controller
            and controller.last_goal_wakeup_id
            and controller.last_goal_wakeup_id in active_goal_ids
        ):
            private_goals = dict(private_goals)
            private_goals["continuation"] = {
                "goal_id": controller.last_goal_wakeup_id,
                "attempt_count": controller.goal_continuation_attempts,
                "reactivation_count": controller.goal_reactivation_count,
                "repeated_action_count": controller.repeated_goal_action_count,
                "last_action_signature": controller.last_goal_action_signature,
                "last_wakeup_step": controller.last_goal_wakeup_step,
            }
        modifiers = entity.get_component("ModifierState")
        private_modifiers = (
            modifiers.get_private_snapshot()
            if modifiers and hasattr(modifiers, "get_private_snapshot")
            else {}
        )
        knowledge_state = entity.get_component("KnowledgeState")
        if knowledge_state is not None and scene_state is not None:
            knowledge_state.observe_location(
                scene_state, scene_state.get_actor_location(actor_name)
            )
        claim_registry = context.get("claim_registry")
        private_knowledge = (
            claim_registry.private_snapshot(
                actor=actor_name,
                knowledge_state=knowledge_state,
                scene_state=scene_state,
            )
            if claim_registry is not None and knowledge_state is not None
            else {}
        )
        navigation = entity.get_component("NavigationState")
        if navigation is not None and actor_location:
            navigation.resolve_departed(str(actor_location))
        private_navigation = (
            navigation.private_snapshot()
            if navigation and hasattr(navigation, "private_snapshot")
            else {}
        )
        private_schedule = self.timeline.private_schedule(
            scene_state,
            actor_name,
            step,
            include_player_relevant=bool(
                context.get("player_name") == actor_name
            ),
        )
        ongoing_actions = self._visible_ongoing_actions(
            actor_name,
            actor_location,
            scene_state,
            context.get("action_queue"),
        )
        memory = entity.get_component("Memory")
        relevant_memories = []
        memory_routes = []
        if not runtime_owns_subjective_state(entity):
            memory_routes = self.memory_context.build_queries(
                actor_name=actor_name,
                world_view=world_view or {},
                recent_observations=recent_observations,
                visible_proposals=visible_proposals,
                world_signals=world_signals,
                private_goals=private_goals,
                private_schedule=private_schedule,
                private_knowledge=private_knowledge,
                private_navigation=private_navigation,
                private_sentiments=private_sentiments,
                relationship_context=relationship_context,
                private_cognition=private_cognition,
                current_plan=current_plan,
                ongoing_actions=ongoing_actions,
            )
        if memory and memory_routes and hasattr(memory, "retrieve"):
            relevant_memories, memory_trace = self.memory_context.retrieve(
                memory,
                memory_routes,
                current_step=step,
            )
            context.setdefault("memory_retrieval_traces", {})[
                actor_name
            ] = memory_trace

        return AgentPerception(
            actor_name=actor_name,
            step=step,
            activation_scope=activation_scope,
            world_view=world_view or {},
            self_state=dict(self_state or {}),
            private_cognition=private_cognition,
            private_drives=private_drives,
            private_traits=private_traits,
            private_sentiments=private_sentiments,
            relationship_context=relationship_context,
            affordance_opportunities=affordance_opportunities,
            private_schedule=private_schedule,
            private_goals=private_goals,
            private_modifiers=private_modifiers,
            private_knowledge=private_knowledge,
            private_navigation=private_navigation,
            recent_observations=recent_observations,
            passive_observations=passive_observations,
            active_observation_results=active_observation_results,
            ongoing_actions=ongoing_actions,
            relevant_memories=relevant_memories,
            current_plan=current_plan,
            visible_proposals=visible_proposals,
            world_signals=world_signals,
            director_signals=director_signals,
        )

    @staticmethod
    def _acknowledge_perception_attention(
        entity: Entity,
        perception: AgentPerception,
    ) -> None:
        cognition = entity.get_component("Cognition")
        if cognition is None:
            return
        pending_world_events = list(
            perception.private_cognition.get("pending_world_events", []) or []
        )
        if pending_world_events and hasattr(cognition, "acknowledge_world_events"):
            cognition.acknowledge_world_events(pending_world_events)
        pending_event_responses = list(
            perception.private_cognition.get("pending_event_responses", []) or []
        )
        if pending_event_responses and hasattr(
            cognition, "acknowledge_event_responses"
        ):
            cognition.acknowledge_event_responses(pending_event_responses)

    @staticmethod
    def _preempt_for_critical_attention(
        entity: Entity,
        action_queue: Any,
        current_step: int,
    ) -> Dict[str, Any] | None:
        """Abort an in-flight action when a critical signal is waiting for her.

        This is where the three urgency tiers stop being labels. A character
        performing a multi-step action is otherwise skipped entirely, so she
        would be deaf even to someone drawing a blade on her. Only ``critical``
        buys an interruption: ``direct`` is guaranteed delivery at her next
        decision point and ``ambient`` waits, neither of which should make her
        drop what she is holding.

        Reading the queue is enough to know she is busy, but the decision comes
        from Cognition, which the Host has already committed, so it survives
        replay. Returning a receipt here also means a critical signal always
        reaches an activation: the same pending record makes AgentScheduler
        wake her, so nothing is aborted without a decision replacing it.
        """
        cognition = entity.get_component("Cognition")
        if cognition is None or not hasattr(cognition, "next_pending_attention"):
            return None
        kind, attention_id, urgency = cognition.next_pending_attention(current_step)
        if not attention_id or urgency != "critical":
            return None
        return action_queue.preempt(
            entity.name, reason=f"{kind}:{attention_id}"
        )

    def _visible_ongoing_actions(
        self,
        actor_name: str,
        actor_location: Any,
        scene_state: Any,
        action_queue: Any,
    ) -> list[Dict[str, Any]]:
        if not actor_location or action_queue is None:
            return []
        visible_objects = set(scene_state.get_visible_objects(actor_name)) if scene_state else set()
        visible_actors = set(
            scene_state.get_actors_in_location(actor_location)
        ) if scene_state else set()
        ongoing = []
        for event in action_queue.snapshot().get("pending", []):
            if not isinstance(event, dict):
                continue
            other_actor = str(event.get("actor", "")).strip()
            if not other_actor or other_actor in {actor_name, "World"}:
                continue
            if str(event.get("location", "")) != str(actor_location):
                continue
            action = event.get("action", {}) if isinstance(event.get("action"), dict) else {}
            target = str(action.get("target", "")).strip()
            public_target = target if target in visible_objects or target in visible_actors else ""
            ongoing.append(
                {
                    "actor": other_actor,
                    "action_kind": str(action.get("kind", "interact")),
                    "visible_target": public_target,
                    "started_at": event.get("starts_at"),
                    "completes_at": event.get("completes_at"),
                }
            )
        return ongoing

    def _apply_agent_private_updates(
        self,
        entity: Entity,
        metadata: Dict[str, Any],
        current_step: int,
    ) -> None:
        if not isinstance(metadata, dict):
            return
        # A persistent subject owns plan, focus, beliefs, commitments and memory.
        # Its only Host-facing subjective metadata is a separately validated
        # registration request (handled by _collect_agent_goal_request).
        if metadata.get("subject_runtime") is True:
            return
        cognition = entity.get_component("Cognition")
        if cognition and hasattr(cognition, "apply_agent_updates"):
            cognition.apply_agent_updates(metadata, step=current_step)
        plan = " ".join(str(metadata.get("plan", "") or "").split()).strip()
        planning = entity.get_component("Planning")
        if planning and hasattr(planning, "set_plan"):
            if metadata.get("clear_plan") is True:
                planning.set_plan("")
            elif plan:
                planning.set_plan(plan[:500])

    @staticmethod
    def _collect_agent_sentiment_updates(
        actor: str,
        metadata: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Forward a subject's own account of how she feels toward someone.

        This is deliberately thin: unlike ``goal_requests`` it carries no
        world-state claim to cross-check, only her private interior state.
        ``SentimentSystem``/``SentimentDynamics.apply_self_reported`` still
        validate the actors, vocabulary and magnitude bounds before anything
        touches authoritative SentimentState/RelationshipBook.
        """
        if not isinstance(metadata, dict):
            return
        updates = metadata.get("sentiment_updates", [])
        if not isinstance(updates, list):
            return
        for raw in updates[:4]:
            if not isinstance(raw, dict):
                continue
            context.setdefault("agent_sentiment_updates", []).append(
                {**dict(raw), "actor": str(actor)}
            )

    # The audit vocabulary for "why did she do that". Each entry names the
    # component that owns the referenced record and how to enumerate it, so a
    # motive can only cite something the character demonstrably has.
    _MOTIVE_SOURCES = {
        "goal": ("GoalState", "goals"),
        "sentiment": ("SentimentState", "sentiments"),
        "drive_need": ("DriveState", "needs"),
    }

    @classmethod
    def _collect_agent_motive_refs(
        cls,
        actor: str,
        entity: Entity,
        metadata: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Record the character's own account of why she acted.

        The Host cannot derive this: it no longer chooses her action, so it has
        no view of what she was weighing. She is the only one who can say, and
        the claim is still falsifiable -- she may only cite a goal, sentiment
        or need she actually holds. An unresolvable reference is
        dropped and logged rather than trusted, so the audit trail stays a
        record of checkable facts instead of self-flattery.
        """
        if not isinstance(metadata, dict):
            return
        refs = metadata.get("motive_refs", [])
        if not isinstance(refs, list):
            return
        for raw in refs[:4]:
            if not isinstance(raw, dict):
                continue
            kind = " ".join(str(raw.get("kind", "") or "").split()).strip()[:40]
            ref = " ".join(str(raw.get("ref", "") or "").split()).strip()[:120]
            source = cls._MOTIVE_SOURCES.get(kind)
            if not kind or not ref or source is None:
                context.setdefault("agent_motive_ref_rejections", []).append(
                    {"actor": str(actor), "kind": kind, "ref": ref}
                )
                continue
            component = entity.get_component(source[0])
            held = getattr(component, source[1], None) if component else None
            if not isinstance(held, dict) or ref not in held:
                context.setdefault("agent_motive_ref_rejections", []).append(
                    {"actor": str(actor), "kind": kind, "ref": ref}
                )
                continue
            context.setdefault("agent_motive_refs", {}).setdefault(
                str(actor), []
            ).append({"kind": kind, "ref": ref})

    @staticmethod
    def _collect_agent_goal_request(
        actor: str,
        metadata: Dict[str, Any],
        context: Dict[str, Any],
        perception: AgentPerception,
    ) -> None:
        if not isinstance(metadata, dict):
            return
        requests = metadata.get("goal_requests", [])
        if not isinstance(requests, list):
            return
        for raw in requests[:1]:
            if not isinstance(raw, dict):
                continue
            visible_world = perception.world_view.get("visible_world", {})
            context.setdefault("agent_goal_requests", []).append(
                {
                    **dict(raw),
                    "actor": str(actor),
                    "_host_perception": {
                        "location": perception.self_state.get("location"),
                        "visible_actors": list(
                            perception.world_view.get("visible_actors", []) or []
                        )[:32],
                        "visible_world": {
                            str(name): dict(state)
                            for name, state in (
                                visible_world.items()
                                if isinstance(visible_world, dict)
                                else []
                            )
                            if isinstance(state, dict)
                        },
                        "affordances": [
                            {
                                "object_id": str(item.get("object_id", "")),
                                "affordance_id": str(
                                    item.get("affordance_id", "")
                                ),
                                "need_effects": {
                                    str(need): float(delta)
                                    for need, delta in (
                                        item.get("need_effects", {}) or {}
                                    ).items()
                                    if str(need).strip()
                                    and isinstance(delta, (int, float))
                                    and not isinstance(delta, bool)
                                },
                            }
                            for item in perception.affordance_opportunities[:64]
                            if isinstance(item, dict)
                            and item.get("available", True)
                            and str(item.get("object_id", "")).strip()
                            and str(item.get("affordance_id", "")).strip()
                        ],
                        "drive_needs": {
                            str(need): {
                                "pressure": float(
                                    (meter or {}).get("pressure", 0.0) or 0.0
                                ),
                                "critical_threshold": float(
                                    (meter or {}).get(
                                        "critical_threshold", 0.8
                                    ) or 0.8
                                ),
                            }
                            for need, meter in (
                                perception.private_drives.get("needs", {}) or {}
                            ).items()
                            if str(need).strip() and isinstance(meter, dict)
                        },
                    },
                }
            )

    def _get_scene_state(self, entities: Dict[str, Entity]):
        for entity in entities.values():
            if entity.get_component("SimulationControl"):
                return entity.get_component("SceneState")
        return None

    def _order_entities(self, entities: Dict[str, Entity], player_name: Any):
        ordered = list(entities.items())
        if not player_name:
            return ordered

        def sort_key(item: Any):
            name, entity = item
            if entity.get_component("SimulationControl"):
                return (2, 0)
            if name == player_name:
                return (0, 0)
            return (1, 0)

        return sorted(ordered, key=sort_key)

    def _inject_commitment_events(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        intents_buffer: Any,
        player_location: Any,
    ) -> None:
        if not scene_state:
            return

        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        for item in scene_state.get_scene_flag("upcoming_commitments", []):
            if not isinstance(item, dict):
                continue
            if int(item.get("due_step", -1)) != current_step:
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue

            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            content = f"{title}：{summary}" if title and summary else (summary or title)
            if not content:
                continue

            intents_buffer.append(
                {
                    "actor": "World",
                    "intent": content,
                    "thought": "",
                    "source": "timeline",
                    "location": item.get("location") or player_location,
                    "proposal_role": "world_pressure",
                    "proposal_priority": 0.78,
                }
            )
            self._register_character_entry_authorization(
                context,
                item.get("character_entry"),
                fallback_id=item.get("commitment_id"),
                source="timeline",
                current_step=current_step,
            )
            self._register_storylet_definition_authorization(
                context,
                item.get("storylet_definition"),
                fallback_id=item.get("commitment_id"),
                source="timeline",
                current_step=current_step,
            )
            self._register_topology_candidate_authorization(
                context,
                item.get("topology_candidate"),
                fallback_id=item.get("commitment_id"),
                source="timeline",
                current_step=current_step,
            )

    def _register_character_entry_authorization(
        self,
        context: Dict[str, Any],
        raw: Any,
        *,
        fallback_id: Any,
        source: str,
        current_step: int,
    ) -> None:
        self._register_candidate_authorization(
            context,
            raw,
            fallback_id=fallback_id,
            source=source,
            current_step=current_step,
            authorizations_key="character_spawn_authorizations",
            error_key="character_entry_authorization_errors",
        )

    def _register_storylet_definition_authorization(
        self,
        context: Dict[str, Any],
        raw: Any,
        *,
        fallback_id: Any,
        source: str,
        current_step: int,
    ) -> None:
        self._register_candidate_authorization(
            context,
            raw,
            fallback_id=fallback_id,
            source=source,
            current_step=current_step,
            authorizations_key="storylet_definition_authorizations",
            error_key="storylet_definition_authorization_errors",
        )

    def _register_topology_candidate_authorization(
        self,
        context: Dict[str, Any],
        raw: Any,
        *,
        fallback_id: Any,
        source: str,
        current_step: int,
    ) -> None:
        self._register_candidate_authorization(
            context,
            raw,
            fallback_id=fallback_id,
            source=source,
            current_step=current_step,
            authorizations_key="topology_candidate_authorizations",
            error_key="topology_candidate_authorization_errors",
        )

    def _drain_director_authorizations(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        current_step: int,
    ) -> None:
        """Surface NarrativeDirector-queued authorizations that are due this
        step into the same per-kind pools ``inject_events``/timeline
        authorizations land in, so a downstream ``Authority.resolve()`` call
        cannot tell the difference between the three sources.
        """
        if scene_state is None:
            return
        for kind, authorizations_key, consumed_flag in (
            (
                "character",
                "character_spawn_authorizations",
                "consumed_character_entry_authorizations",
            ),
            (
                "storylet_definition",
                "storylet_definition_authorizations",
                "consumed_storylet_definition_authorizations",
            ),
            (
                "topology",
                "topology_candidate_authorizations",
                "consumed_topology_authorizations",
            ),
        ):
            due = drain_due_director_authorizations(
                scene_state,
                kind=kind,
                consumed_flag=consumed_flag,
                current_step=current_step,
            )
            if due:
                context.setdefault(authorizations_key, []).extend(deepcopy(due))

    def _register_candidate_authorization(
        self,
        context: Dict[str, Any],
        raw: Any,
        *,
        fallback_id: Any,
        source: str,
        current_step: int,
        authorizations_key: str,
        error_key: str,
    ) -> None:
        """Shared plumbing for every narrative-candidate authorization kind.

        Character entries and storylet definitions both get issued the same
        way -- via ``inject_events``/timeline commitment payloads carrying a
        kind-specific dict -- and only differ in which pool the resulting
        authorization lands in.
        """
        if not isinstance(raw, dict):
            return
        authorization = deepcopy(raw)
        authorization_id = str(
            authorization.get("authorization_id") or fallback_id or ""
        ).strip()
        if not authorization_id:
            context.setdefault(error_key, []).append(
                f"{source}:missing_authorization_id"
            )
            return
        authorization["authorization_id"] = authorization_id
        authorization.setdefault("not_before_step", int(current_step))
        # The capability lives only in this Runner context.  A small numeric
        # window merely accommodates discrete action duration before the same
        # batch reaches Simulation; it does not persist into the next step.
        authorization.setdefault("expires_step", int(current_step) + 20)
        authorization["source"] = source
        context.setdefault(authorizations_key, []).append(authorization)

    def _sanitize_auto_intent(self, intent: str, is_player: bool) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return normalized

        normalized = normalized.replace("……", "，").replace("...", "，").replace("..", "，")
        normalized = re.sub(r"[“”\"]", "", normalized)

        normalized = re.sub(r"，{2,}", "，", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip("，； ")
        if normalized and normalized[-1] not in "。！？!?":
            normalized += "。"
        return normalized

    def _proposal_role(
        self,
        is_player: bool,
        source: str,
        activation_scope: str = "foreground",
    ) -> str:
        if source == "manual" and is_player:
            return "player_override"
        if source == "manual":
            return "manual_override"
        if source == "timeline":
            return "world_pressure"
        if source == "injected":
            return "world_event"
        if activation_scope == "background":
            return "background_character_proposal"
        return "character_proposal"

    def _proposal_priority(
        self,
        is_player: bool,
        source: str,
        activation_scope: str = "foreground",
    ) -> float:
        if is_player and source == "manual":
            return 1.0
        if source == "manual":
            return 0.7
        if source == "timeline":
            return 0.78
        if source == "injected":
            return 0.9
        if activation_scope == "background":
            return 0.28
        return 0.48
