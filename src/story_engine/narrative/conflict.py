from typing import Any, Dict, List


class ConflictDirector:
    """Builds and records pacing pressure without resolving character actions."""

    def build_packet(
        self,
        scene_state: Any,
        scenario: Any,
        current_step: int,
        reaction_context: Dict[str, Any],
        storylet_packet: Dict[str, Any],
        timeline_packet: Dict[str, Any],
        director_packet: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not scene_state or not scenario or not getattr(scenario, "conflict", None):
            return {}
        config = scenario.conflict
        flags = scene_state.scene_flags or {}
        quiet_turns = int(flags.get("quiet_turns_since_conflict", 0))
        visible_count = int(flags.get("visible_conflict_count", 0))
        directive = str((director_packet or {}).get("directive", "")).strip()
        escalation = directive in {"inject_crisis", "raise_pressure"}
        transition_backlash = bool(
            isinstance(timeline_packet, dict)
            and isinstance(timeline_packet.get("transition_pressure"), dict)
            and timeline_packet["transition_pressure"].get("requires_human_backlash")
        )
        immediate = bool(
            reaction_context.get("requires_reaction")
            and reaction_context.get("action_pressure") != "low"
            and float(config.intensity) >= 0.8
        )
        visible_opportunity = bool(
            reaction_context.get("hostile_watchers")
            and (
                transition_backlash
                or immediate
                or escalation
                or (
                    current_step <= int(config.early_pressure_window_end)
                    and visible_count == 0
                )
                or quiet_turns >= int(config.max_quiet_turns)
            )
        )
        opportunity_reasons = []
        if transition_backlash:
            opportunity_reasons.append("timeline_transition")
        if immediate:
            opportunity_reasons.append("visible_reaction")
        if escalation:
            opportunity_reasons.append("low_dramatic_pressure")
        if quiet_turns >= int(config.max_quiet_turns):
            opportunity_reasons.append("extended_quiet")
        pressure_state = (
            "acute" if visible_opportunity and escalation
            else "rising" if visible_opportunity
            else "watch" if quiet_turns > 0
            else "quiet"
        )
        return {
            "mode": "advisory_pressure",
            "current_step": int(current_step),
            "intensity": float(config.intensity),
            "immediate_pressure": immediate,
            "transition_requires_backlash": transition_backlash,
            "visible_conflict_opportunity": visible_opportunity,
            "pressure_state": pressure_state,
            "opportunity_reasons": opportunity_reasons,
            "antagonist_names": list(config.antagonist_names),
            "preferred_modes": list(config.preferred_modes),
            "surface_style": str(config.surface_style),
            "verbal_directness": float(config.verbal_directness),
            "repetition_window": int(config.repetition_window),
            "director_directive": directive,
            "public_pressure_salient": bool(
                escalation
                or (
                    current_step <= int(config.early_pressure_window_end)
                    and visible_count <= 1
                )
            ),
            "quiet_turns_since_conflict": quiet_turns,
            "visible_conflict_count": visible_count,
            "recent_template_ids": [
                str(item) for item in flags.get("recent_conflict_template_ids", [])
                if str(item).strip()
            ],
            "storylet_ids": list(storylet_packet.get("priority_storylet_ids", [])),
            "storylet_tags": list(storylet_packet.get("priority_tags", [])),
            "storylet_template_ids": list(storylet_packet.get("preferred_template_ids", [])),
            "active_templates": self.select_templates(
                getattr(scenario, "conflict_templates", []),
                str(flags.get("day_phase", "")),
                current_step,
            ),
        }

    def select_templates(self, templates: List[Any], phase: str, current_step: int):
        selected = []
        for template in templates or []:
            phases = list(getattr(template, "phases", []) or [])
            if phases and phase and phase not in phases:
                continue
            if current_step < int(getattr(template, "min_step", 0)):
                continue
            selected.append(
                {
                    "template_id": getattr(template, "template_id", ""),
                    "instruction": getattr(template, "instruction", ""),
                    "fallback_result": getattr(template, "fallback_result", ""),
                    "fallback_results": list(getattr(template, "fallback_results", []) or []),
                    "preferred_actors": list(getattr(template, "preferred_actors", []) or []),
                    "tags": list(getattr(template, "tags", []) or []),
                }
            )
        return selected

    def record_result(self, scene_state, context, result):
        if not scene_state:
            return
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        flags = scene_state.scene_flags or {}
        quiet = int(flags.get("quiet_turns_since_conflict", 0))
        count = int(flags.get("visible_conflict_count", 0))
        if self.is_visible(result, str(result.get("conflict_level", "none"))):
            packet = context.get("conflict", {}) if isinstance(context.get("conflict"), dict) else {}
            window = max(1, int(packet.get("repetition_window", 2)))
            recent = [
                str(item) for item in flags.get("recent_conflict_template_ids", [])
                if str(item).strip()
            ]
            recent.extend(
                str(item) for item in result.get("applied_conflict_templates", [])
                if str(item).strip()
            )
            scene_state.update_scene_flags(
                {
                    "last_visible_conflict_step": step,
                    "quiet_turns_since_conflict": 0,
                    "visible_conflict_count": count + 1,
                    "recent_conflict_template_ids": recent[-window:],
                }
            )
        else:
            scene_state.update_scene_flags({"quiet_turns_since_conflict": quiet + 1})

    def is_visible(self, result: Dict[str, Any], level: str) -> bool:
        if level in {"medium", "high"} or bool(result.get("conflict_flags", [])):
            return True
        return any(
            isinstance(item, dict)
            and item.get("visibility") == "public"
            and item.get("outcome") in {"blocked", "complication"}
            for item in result.get("resolved_actions", [])
        )
