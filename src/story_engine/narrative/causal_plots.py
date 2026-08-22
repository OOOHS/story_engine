from typing import Any, Dict, List


class CausalPlotEngine:
    """Settles narrative bookkeeping against already-committed world facts.

    This runs strictly after ``WorldStateTransaction.commit`` has succeeded,
    never before and never against a rehearsed guess of what a commit would
    produce. Plot clocks, runtime plot-beat proposals, and storylet
    consumption are all *consumers* of committed state, not participants in
    the atomic settlement of a batch of concurrent actions: if this pass
    fails or is skipped, the world facts that already committed stay
    committed, and only the narrative bookkeeping is left for next tick.
    """

    CONSUMED_RULES_FLAG = "consumed_plot_rules"

    def settle(
        self,
        *,
        scene_state: Any,
        plot_state: Any,
        scenario: Any,
        result: Dict[str, Any],
        consumed_storylet_ids: List[str] | None = None,
        current_step: int = 0,
    ) -> Dict[str, Any]:
        if scene_state is None or plot_state is None:
            return result
        self._consume_storylets(scene_state, plot_state, consumed_storylet_ids or [], current_step)
        if plot_state is not None:
            plot_state.apply_beat_proposals(
                list(result.get("plot_beat_proposals") or []),
                current_step=int(current_step),
                known_actors=set(scene_state.actor_states),
            )
        self._apply_causal_rules(scene_state, plot_state, scenario, result, current_step)
        return result

    def _consume_storylets(
        self,
        scene_state: Any,
        plot_state: Any,
        storylet_ids: List[str],
        current_step: int,
    ) -> None:
        if not storylet_ids:
            return
        existing = scene_state.get_scene_flag("consumed_storylets", [])
        if not isinstance(existing, list):
            existing = []
        normalized = [str(item).strip() for item in existing if str(item).strip()]
        for raw_id in storylet_ids:
            storylet_id = str(raw_id).strip()
            if storylet_id and storylet_id not in normalized:
                normalized.append(storylet_id)
        scene_state.update_scene_flags({"consumed_storylets": normalized})

        for raw_id in storylet_ids:
            parsed = plot_state.parse_runtime_storylet_id(raw_id)
            if not parsed:
                continue
            plot_id, beat_id = parsed
            plot_state.consume_beat(plot_id, beat_id, current_step=int(current_step))

    def _apply_causal_rules(
        self,
        scene_state: Any,
        plot_state: Any,
        scenario: Any,
        result: Dict[str, Any],
        current_step: int,
    ) -> None:
        rules = list(getattr(scenario, "plot_rules", []) or []) if scenario else []
        if not rules:
            return

        consumed = set(scene_state.get_scene_flag(self.CONSUMED_RULES_FLAG, []) or [])
        triggered: List[str] = []
        consumed_this_turn: List[str] = []
        derived: List[Dict[str, Any]] = []
        suppressed: List[str] = []
        max_triggers = int(getattr(scenario, "causal_plot_max_triggers_per_turn", 3))
        max_total_advance = int(getattr(scenario, "causal_plot_max_total_advance", 3))
        total_advance = 0
        ordered_rules = sorted(
            enumerate(rules),
            key=lambda item: (-int(getattr(item[1], "priority", 0)), item[0]),
        )
        for _, rule in ordered_rules:
            if rule.one_shot and rule.rule_id in consumed:
                continue
            if rule.plot_id not in plot_state.plots:
                continue
            if not all(
                scene_state.matches_condition(condition, plot_state=plot_state)
                for condition in rule.conditions
            ):
                continue
            movement = abs(int(rule.advance))
            if len(triggered) >= max_triggers or total_advance + movement > max_total_advance:
                suppressed.append(rule.rule_id)
                continue
            derived.append(
                {
                    "plot_id": rule.plot_id,
                    "advance": int(rule.advance),
                    "stage_shift": int(rule.stage_shift),
                    "note": rule.reason or f"causal plot rule: {rule.rule_id}",
                    "rule_id": rule.rule_id,
                }
            )
            triggered.append(rule.rule_id)
            if rule.one_shot:
                consumed_this_turn.append(rule.rule_id)
            total_advance += movement

        # Always write back the real ledger, even with nothing new this turn,
        # so a stale/forged value can never linger unresolved.
        scene_state.update_scene_flags(
            {self.CONSUMED_RULES_FLAG: sorted(consumed.union(consumed_this_turn))}
        )
        if suppressed:
            result["causal_plot_suppressed_rules"] = suppressed
        if not derived:
            return
        plot_state.apply_updates(derived, current_step=current_step)
        result["plot_updates"] = list(result.get("plot_updates") or []) + derived
        result["causal_plot_rules"] = triggered
