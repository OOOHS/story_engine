from typing import Any, Dict, List


class ProposalArbiter:
    """Ranks simultaneous proposals without turning them into outcomes."""

    def build_focus_packet(
        self,
        intents: List[Dict[str, Any]],
        player_name: Any,
        player_intent: Any,
        timeline_packet: Dict[str, Any],
        reaction_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        proposals = []
        for index, item in enumerate(intents or []):
            if not isinstance(item, dict) or not item.get("actor"):
                continue
            proposals.append(
                {
                    "actor": item.get("actor"),
                    "intent": item.get("intent", ""),
                    "action_kind": item.get("action_kind", "interact"),
                    "action_target": item.get("action_target", ""),
                    "action_started_at": item.get("action_started_at"),
                    "action_completed_at": item.get("action_completed_at"),
                    "action_duration": item.get("action_duration"),
                    "stale_by_versions": item.get("stale_by_versions", 0),
                    "location": item.get("location"),
                    "role": item.get("proposal_role") or "character_proposal",
                    "priority": float(item.get("proposal_priority", 0.5) or 0.0),
                    "source": item.get("source", ""),
                    "activation_scope": item.get("activation_scope", "foreground"),
                    "batch_step": item.get("proposal_batch_step"),
                    "must_reference": bool(item.get("source") in {"manual", "timeline", "injected"}),
                    "_order": index,
                }
            )
        proposals.sort(key=lambda item: (-item["priority"], item["_order"]))
        for item in proposals:
            item.pop("_order", None)

        player_proposal = {}
        if isinstance(player_intent, dict) and player_intent.get("intent"):
            player_proposal = {
                "actor": player_name,
                "intent": player_intent.get("intent", ""),
                "action_kind": player_intent.get("action_kind", "interact"),
                "action_target": player_intent.get("action_target", ""),
                "priority": float(player_intent.get("proposal_priority", 0.0) or 0.0),
                "role": player_intent.get("proposal_role", "character_proposal"),
                "source": player_intent.get("source", ""),
            }
        anchor = {}
        if player_proposal.get("source") == "manual":
            anchor = {
                "actor": player_name,
                "intent": player_proposal["intent"],
                "action_kind": player_proposal.get("action_kind", "interact"),
                "action_target": player_proposal.get("action_target", ""),
                "priority": float(player_proposal.get("priority", 1.0) or 1.0),
                "role": player_proposal.get("role", "player_override"),
            }
        return {
            "anchor_actor": player_name if anchor else None,
            "anchor_intent": anchor,
            "player_proposal": player_proposal,
            "player_override_active": bool(anchor),
            "player_proposal_is_primary": bool(anchor),
            "proposal_semantics": "simultaneous",
            "proposals": proposals[:8],
            "due_commitment_ids": [
                str(item.get("commitment_id"))
                for item in timeline_packet.get("due_commitments", [])
                if isinstance(item, dict) and str(item.get("commitment_id", "")).strip()
            ],
            "requires_same_scene_reaction": bool(reaction_context.get("requires_reaction")),
        }
