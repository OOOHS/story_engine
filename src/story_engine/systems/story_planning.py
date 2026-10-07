"""Post-render planner polling over the player's cumulative narrative."""

from copy import deepcopy
from typing import Any, Dict
from uuid import uuid4

from src.story_engine.core.entity import Entity
from src.story_engine.environment.narrative_candidates import (
    STORY_REVIEW_STATUS,
    STORY_PENDING_STATUSES,
    PENDING_STORY_PROPOSALS_FLAG,
    record_story_proposal,
    record_candidate_audit,
)
from src.story_engine.systems.system import System
from src.story_engine.components.scene_state import SceneState
from src.story_engine.narrative.storylet_definitions import StoryletDefinitionLifecycle
from src.story_engine.scenarios.config import StoryletConfig


class StoryPlanningSystem(System):
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        # Only generated narration belongs in this agent's story. Renderer
        # failure leaves this phase pending along with the delivery receipt.
        if not context.get("step_committed") or "rendered_text" not in context:
            return
        for entity in entities.values():
            planner = entity.get_component("StoryPlanner")
            if planner is None:
                continue
            scene = entity.get_component("SceneState")
            turn_id = context.setdefault("narrative_turn_id", uuid4().hex)
            player = context.get("player_name") or (
                planner.scenario.player_character_name if planner.scenario else None
            )
            player_message = str(context.get("overrides", {}).get(player, "") or "")
            if not player_message:
                player_message = next((
                    str(item.get("intent", "")) for item in context.get("intents", [])
                    if item.get("actor") == player
                ), "")
            added = planner.record_turn(turn_id, player_message, str(context["rendered_text"]))
            if (not added or not planner.inquiry_due()
                    or (planner.scenario is not None and not planner.scenario.story_planner_enabled)):
                context["story_planner_status"] = {"status": "waiting"}
                self._finish_turn(planner, scene, context)
                continue
            storylets = [item.model_dump() for item in planner.scenario.storylets] if planner.scenario else []
            if scene is not None:
                storylets.extend(scene.get_scene_flag("dynamic_storylets", []) or [])
            pending = scene.get_scene_flag(PENDING_STORY_PROPOSALS_FLAG, []) if scene else []
            tracking = entity.get_component("StoryTracking")
            tracking_summary = {sid: {"status": agent.status, "progress": agent.progress}
                                for sid, agent in tracking.agents.items()} if tracking else {}
            try:
                result = planner.propose({"existing_storylets": storylets, "pending_proposals": pending,
                                         "storylet_tracking": tracking_summary})
                error = result.get("story_planner_error")
                if error:
                    context["story_planner_status"] = {"status": "failed", "error_type": str(error)[:80]}
                    self._finish_turn(planner, scene, context)
                    continue
                recorded = []
                for candidate in result.get("narrative_candidates", [])[:planner.MAX_CANDIDATES_PER_TICK]:
                    if scene is not None:
                        recorded.append(record_story_proposal(scene, candidate, turn_id=turn_id))
                context["story_planner_status"] = {
                    "status": "completed", "review_status": STORY_REVIEW_STATUS,
                    "proposal_count": len(recorded),
                }
                self._finish_turn(planner, scene, context)
            except Exception as exc:
                context["story_planner_status"] = {"status": "failed", "error_type": type(exc).__name__}
                self._finish_turn(planner, scene, context)


    def _finish_turn(self, planner, scene, context):
        self._register_pending(planner, scene, context)
        tracking = planner.entity.get_component("StoryTracking")
        if tracking is not None and scene is not None:
            tracking.reconcile(scene)
            context["story_tracker_status"] = tracking.track(
                planner.narrative_messages, context["narrative_turn_id"], scene,
            )

    def _register_pending(self, planner, scene, context):
        if scene is None:
            return
        lifecycle = StoryletDefinitionLifecycle()
        records = deepcopy(scene.get_scene_flag(PENDING_STORY_PROPOSALS_FLAG, []) or [])
        if not any(p.get("status") in STORY_PENDING_STATUSES for p in records):
            return
        reviewed = 0
        accepted = 0
        for proposal in records:
            if proposal.get("status") not in STORY_PENDING_STATUSES:
                continue
            if reviewed >= planner.MAX_CANDIDATES_PER_TICK:
                break
            reviewed += 1
            try:
                definition = StoryletConfig.model_validate(proposal["payload"]).model_dump()
                preparation = lifecycle.prepare(planner.scenario, scene, definition)
            except (ValueError, KeyError, TypeError) as exc:
                proposal.update({"status": "rejected", "review_status": STORY_REVIEW_STATUS,
                                 "issues": ["invalid storylet definition: " + type(exc).__name__]})
                scene.update_scene_flags({PENDING_STORY_PROPOSALS_FLAG: deepcopy(records)})
                continue
            try:
                verdict = {"valid": not preparation.errors, "issues": preparation.errors}
                valid = verdict["valid"] is True and not verdict["issues"]
                preview = SceneState(**deepcopy(scene.model_dump()))
                if valid:
                    errors = lifecycle.stage(preview, preparation.plan)
                    if errors:
                        verdict = {"valid": False, "issues": errors}
                        valid = False
                decision = {**proposal, "status": "accepted" if valid else "rejected",
                            "review_status": STORY_REVIEW_STATUS, "issues": verdict["issues"]}
                record_candidate_audit(preview, kind="storylet_definition", source="story_planner",
                                       accepted=valid, reason="; ".join(verdict["issues"]),
                                       candidate_id=definition["storylet_id"],
                                       step=int(context.get("clock").current_step) if context.get("clock") else 0)
                if valid:
                    preview.update_scene_flags({"world_version": int(scene.get_scene_flag("world_version", 0) or 0) + 1})
                next_records = [decision if p["proposal_id"] == proposal["proposal_id"] else p for p in records]
                preview.update_scene_flags({PENDING_STORY_PROPOSALS_FLAG: next_records})
                # Publish the future definition, registration decision and audit together.
                scene.scene_flags = deepcopy(preview.scene_flags)
                proposal.update(decision)
                if valid:
                    accepted += 1
            except Exception as exc:
                context["story_planner_status"].update({"review_status": "retry_pending",
                                                       "review_error_type": type(exc).__name__})
                break
        context["story_planner_status"].update({"reviewed_count": reviewed, "accepted_count": accepted})
