from copy import deepcopy
from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class ActionSchedulingSystem(System):
    """Schedule submitted actions and expose the next completion batch.

    This is a discrete-event barrier: decisions made at the same logical time
    are all scheduled before the queue advances, and only actions completing at
    the earliest time enter authoritative Simulation together.
    """

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        queue = context.get("action_queue")
        if queue is None:
            return
        submissions = [
            item for item in context.get("intents", []) if isinstance(item, dict)
        ]
        scheduled = []
        errors = []
        for proposal in submissions:
            proposal = dict(proposal)
            actor = str(proposal.get("actor", "")).strip()
            # A slow action outlives the turn it was chosen on, so her stated
            # reason travels with the event; otherwise the audit would attribute
            # the completion to whatever she happens to be thinking later.
            motive_refs = (
                context.get("agent_motive_refs", {}).get(actor) if actor else None
            )
            target = str(proposal.get("action_target", "")).strip()
            if scene_state := self._scene_state(entities):
                if target in scene_state.world_objects:
                    proposal["target_reference_kind"] = "world_object"
                elif target in scene_state.actor_states:
                    proposal["target_reference_kind"] = "actor"
                elif target in scene_state.get_known_locations():
                    proposal["target_reference_kind"] = "location"
            try:
                event = queue.schedule(
                    proposal,
                    host_metadata=(
                        {"motive_refs": deepcopy(motive_refs)}
                        if isinstance(motive_refs, list) and motive_refs
                        else {}
                    ),
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue
            scheduled.append(
                {
                    "event_id": event.event_id,
                    "actor": event.actor,
                    "starts_at": event.starts_at,
                    "completes_at": event.completes_at,
                    "duration": event.duration,
                    "action": dict(event.proposal.get("action", {})),
                }
            )

        completed = queue.pop_next_batch()
        scene_state = self._scene_state(entities)
        try:
            world_version = int(
                scene_state.get_scene_flag("world_version", 0) if scene_state else 0
            )
        except (TypeError, ValueError):
            world_version = 0
        completed_motive_refs = {}
        for proposal in completed:
            host_metadata = proposal.pop("_host_metadata", {})
            actor = str(proposal.get("actor", "")).strip()
            event_id = str(proposal.get("event_id", "")).strip()
            motive_refs = (
                host_metadata.get("motive_refs")
                if isinstance(host_metadata, dict)
                else None
            )
            if actor and event_id and isinstance(motive_refs, list) and motive_refs:
                completed_motive_refs[actor] = {
                    "event_id": event_id,
                    "motive_refs": deepcopy(motive_refs),
                }
            try:
                based_on = int(proposal.get("based_on_world_version", world_version))
            except (TypeError, ValueError):
                based_on = world_version
            proposal["completion_world_version"] = world_version
            proposal["stale_by_versions"] = max(0, world_version - based_on)
        clock = context.get("clock")
        if clock and hasattr(clock, "advance_to"):
            clock.advance_to(queue.current_time)
        context["action_submissions"] = submissions
        context["scheduled_actions"] = scheduled
        context["action_scheduling_errors"] = errors
        context["completed_action_events"] = completed
        context["completed_action_motive_refs"] = completed_motive_refs
        context["action_queue_snapshot"] = queue.snapshot()
        context["intents"] = completed

    @staticmethod
    def _scene_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            if entity.get_component("SimulationControl"):
                return entity.get_component("SceneState")
        return None
