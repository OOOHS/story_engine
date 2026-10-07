from typing import Any, ClassVar, Dict

from typing import List, Optional
from src.story_engine.rules.offline_semantics import interpret_offline_action, extract_move_target_from_intent
from src.story_engine.components.simulation_control import SimulationControl


class HostRuleSimulationControl(SimulationControl):
    """Deterministic semantic baseline using only project-owned Host rules."""

    component_slot: ClassVar[str] = "SimulationControl"

    def validate_commit(self, before: Any, after: Any,
                        result: Dict[str, Any], input_payload: Dict[str, Any]) -> None:
        """The explicit offline baseline has no model settlement to check."""
        return None

    def interpret_action(self, intent: str, actor: str, perception: Any):
        return interpret_offline_action(intent, actor, perception)

    def simulate(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._fallback_result(
            input_payload,
            note="通用 Host 规则结算已启用。",
        )

    def _fallback_result(self, input_payload: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        result = self._empty_result()
        scene_state = self.entity.get_component("SceneState") if self.entity else None
        actor_updates: Dict[str, Dict[str, Any]] = {}
        resolved_actions = []
        for item in input_payload.get("intents", []):
            actor = item.get("actor", "Unknown")
            intent = item.get("intent", "")
            source = str(item.get("source", ""))
            is_player = bool(item.get("is_player"))
            visibility = "public" if is_player or item.get("location") == input_payload.get("player_pov", {}).get("location") else "hidden"
            if source == "storylet":
                resolved_actions.append({
                    "actor": "World", "intent": intent,
                    "source_storylet_id": item.get("source_storylet_id", ""),
                    "action_kind": "interact", "action_target": "",
                    "outcome": "blocked", "visibility": "hidden",
                    "location": item.get("location"),
                    "result": "故事块需语义结算，当前规则模式保持待结算。",
                })
                continue
            if actor == "World" or source in {"injected"}:
                world_result = self._summarize_world_intent(intent)
                if world_result:
                    resolved_actions.append(
                        {
                            "actor": actor,
                            "intent": intent,
                            "action_kind": item.get("action_kind", "interact"),
                            "action_target": item.get("action_target", ""),
                            "outcome": "partial",
                            "location": item.get("location") or input_payload.get("player_pov", {}).get("location"),
                            "result": world_result,
                            "private_result": "",
                            "visibility": "public",
                        }
                    )
                continue

            move_target = self._extract_move_target(actor, intent, scene_state)
            if move_target:
                actor_updates.setdefault(actor, {})["location"] = move_target
                resolved_actions.append(
                    {
                        "actor": actor,
                        "intent": intent,
                        "action_kind": item.get("action_kind", "move"),
                        "action_target": item.get("action_target", move_target),
                        "outcome": "success",
                        "location": move_target,
                        "result": f"动身前往{move_target}。",
                        "private_result": "",
                        "visibility": visibility,
                    }
                )
                continue

            summary = self._summarize_fallback_intent(actor, intent, item, input_payload)
            if not summary and not is_player:
                continue

            outcome = "success" if is_player else "partial"
            resolved_actions.append(
                {
                    "actor": actor,
                    "intent": intent,
                    "action_kind": item.get("action_kind", "interact"),
                    "action_target": item.get("action_target", ""),
                    "outcome": outcome,
                    "location": item.get("location"),
                    "result": summary or "系统未完成结构化判定，暂按意图记录。",
                    "private_result": "",
                    "visibility": visibility,
                }
            )
        result["resolved_actions"] = resolved_actions
        if actor_updates:
            result["state_updates"]["actor_states"] = actor_updates
        result["storylet_hits"] = []
        result["simulation_notes"] = [note] if note else []
        return self._enforce_legality(result, input_payload)

    def _summarize_world_intent(self, intent: str) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return ""
        return normalized.rstrip("。") + "。"

    def _summarize_fallback_intent(
        self,
        actor: str,
        intent: str,
        item: Dict[str, Any],
        input_payload: Dict[str, Any],
    ) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return ""

        return f"{actor}尝试执行其意图：{normalized.rstrip('。')}。"

    def _extract_move_target(self, actor_name: str, intent: str, scene_state: Any) -> Optional[str]:
        if not scene_state or not intent or not actor_name:
            return None

        current_location = scene_state.get_actor_location(actor_name)
        connected_locations = []
        if current_location:
            connected_locations = scene_state.get_object_state(current_location).get("connected_to", [])
        return extract_move_target_from_intent(
            intent=intent,
            current_location=current_location,
            connected_locations=connected_locations,
            known_locations=scene_state.get_known_locations(),
            location_aliases={
                str(location): list((state or {}).get("aliases", []) or [])
                for location, state in scene_state.world_objects.items()
                if isinstance(state, dict) and scene_state.is_location(location)
            },
        )

    def _enforce_legality(self, result: Dict[str, Any], input_payload: Dict[str, Any]) -> Dict[str, Any]:
        legality_checks = input_payload.get("legality", {}).get("checks", [])
        if not isinstance(legality_checks, list):
            return result

        actor_updates = result.setdefault("state_updates", {}).setdefault("actor_states", {})
        notes = result.setdefault("simulation_notes", [])

        for check in legality_checks:
            if not isinstance(check, dict):
                continue
            actor = check.get("actor")
            intent = check.get("intent", "")
            verdict = check.get("verdict", "allow")
            if not actor:
                continue

            if verdict == "allow":
                # A legal graph move is a deterministic host transition.  The
                # semantic resolver describes it, but cannot accidentally omit
                # (or decline) the actual body movement.
                rewrite_location = check.get("rewrite_location")
                action = self._find_matching_action(
                    result.get("resolved_actions", []), actor, intent
                )
                if (
                    check.get("rule") == "movement"
                    and rewrite_location
                    and action is not None
                    and str(action.get("action_kind", "")).strip() == "move"
                    and str(action.get("outcome", "")).strip() != "blocked"
                ):
                    action["location"] = rewrite_location
                    actor_updates.setdefault(actor, {})[
                        "location"
                    ] = rewrite_location
                continue

            action = self._find_matching_action(result.get("resolved_actions", []), actor, intent)
            if verdict == "block":
                result["uncertain_outcomes"] = [
                    check
                    for check in result.get("uncertain_outcomes", [])
                    if not isinstance(check, dict)
                    or str(check.get("actor", "")).strip() != str(actor)
                ]
                if action is None:
                    action = {
                        "actor": actor,
                        "intent": intent,
                        "outcome": "blocked",
                        "location": self._infer_location(actor, input_payload),
                        "result": "",
                        "visibility": "public" if actor == input_payload.get("player_name") else "local",
                    }
                    result.setdefault("resolved_actions", []).append(action)
                action["outcome"] = "blocked"
                action["location"] = self._infer_location(actor, input_payload)
                action["result"] = check.get("reason", "这个动作不符合当前世界法则。")
                actor_updates.pop(actor, None)
                result["object_lifecycle"] = [
                    operation
                    for operation in result.get("object_lifecycle", [])
                    if not isinstance(operation, dict)
                    or str(operation.get("actor", "")).strip() != str(actor)
                ]
                result["exchanges"] = [
                    exchange
                    for exchange in result.get("exchanges", [])
                    if not isinstance(exchange, dict)
                    or str(actor) not in {
                        str(party).strip()
                        for party in exchange.get("parties", [])
                    }
                ]
                result["social_impacts"] = [
                    impact
                    for impact in result.get("social_impacts", [])
                    if not isinstance(impact, dict)
                    or str(impact.get("source", "")).strip() != str(actor)
                ]
                result["knowledge_updates"] = [
                    update
                    for update in result.get("knowledge_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("source", "")).strip() != str(actor)
                ]
                result["drive_updates"] = [
                    update
                    for update in result.get("drive_updates", [])
                    if not isinstance(update, dict)
                    or str(update.get("source", update.get("actor", ""))).strip()
                    != str(actor)
                ]
                result["drive_creations"] = [
                    creation
                    for creation in result.get("drive_creations", [])
                    if not isinstance(creation, dict)
                    or str(creation.get("actor", "")).strip() != str(actor)
                ]
                note = f"{actor}的动作被裁定为不合法：{check.get('reason', '')}".strip()
                if note and note not in notes:
                    notes.append(note)
                continue

            if verdict == "rewrite":
                result["uncertain_outcomes"] = [
                    check
                    for check in result.get("uncertain_outcomes", [])
                    if not isinstance(check, dict)
                    or str(check.get("actor", "")).strip() != str(actor)
                ]
                result["social_impacts"] = [
                    impact
                    for impact in result.get("social_impacts", [])
                    if not isinstance(impact, dict)
                    or str(impact.get("source", "")).strip() != str(actor)
                ]
                rewrite_location = check.get("rewrite_location")
                suggested = check.get("suggested_intent", "")
                if action is None:
                    action = {
                        "actor": actor,
                        "intent": intent,
                        "outcome": "partial",
                        "location": rewrite_location or self._infer_location(actor, input_payload),
                        "result": "",
                        "visibility": "public" if actor == input_payload.get("player_name") else "local",
                    }
                    result.setdefault("resolved_actions", []).append(action)
                action["outcome"] = "partial" if action.get("outcome") == "success" else action.get("outcome", "partial")
                if rewrite_location:
                    action["location"] = rewrite_location
                    actor_updates.setdefault(actor, {})["location"] = rewrite_location
                base_result = action.get("result", "").strip()
                rewrite_line = check.get("reason", "")
                if suggested:
                    rewrite_line = f"{rewrite_line} 实际只能{suggested}。".strip()
                action["result"] = rewrite_line if not base_result else f"{rewrite_line} {base_result}".strip()
                note = f"{actor}的动作被改写为合法版本。"
                if note not in notes:
                    notes.append(note)

        allowed_locations = {
            str(check.get("actor", "")).strip(): str(
                check.get("rewrite_location", "") or ""
            ).strip()
            for check in legality_checks
            if isinstance(check, dict)
            and str(check.get("actor", "")).strip()
            and str(check.get("rule", "")).strip()
            in {"movement", "movement_path"}
            and str(check.get("verdict", "allow")).strip() in {"allow", "rewrite"}
            and str(check.get("rewrite_location", "") or "").strip()
        }
        additions = result.get("world_additions", {})
        locations = additions.get("locations", []) if isinstance(additions, dict) else []
        new_locations = {
            str(item.get("location_id", "")).strip()
            for item in locations if isinstance(item, dict)
        } if isinstance(locations, list) else set()
        for actor, update in list(actor_updates.items()):
            if not isinstance(update, dict) or "location" not in update:
                continue
            authorized = allowed_locations.get(str(actor).strip(), "")
            if authorized:
                update["location"] = authorized
                continue
            if update["location"] in new_locations:
                # The old graph has no route ruling for a newly resolved
                # place. Preserve the candidate for structural staging and
                # the model's causal/autonomy check against original intents.
                continue
            update.pop("location", None)
            note = f"{actor}没有宿主移动裁定，语义位置写入已忽略。"
            if note not in notes:
                notes.append(note)
            if not update:
                actor_updates.pop(actor, None)

        return result
