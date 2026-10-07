from typing import Any, Dict, List



class LegalityEngine:
    """Checks structured identities, reachability and known routes after semantic interpretation."""

    def build_context(
        self,
        scene_state: Any,
        scenario: Any,
        intents: List[Dict[str, Any]],
        actor_map_knowledge: Dict[str, Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        profile = str(getattr(scenario, "physics_profile", "mundane") or "mundane")
        physics_rules = list(getattr(scenario, "physics_rules", []) or [])
        return {
            "physics_profile": profile,
            "checks": [
                self.assess_intent(
                    scene_state,
                    profile,
                    item,
                    map_knowledge=(actor_map_knowledge or {}).get(
                        str(item.get("actor", ""))
                    ),
                    physics_rules=physics_rules,
                )
                for item in intents or []
                if isinstance(item, dict) and item.get("actor")
            ],
        }

    def assess_intent(
        self,
        scene_state: Any,
        physics_profile: str,
        intent_item: Dict[str, Any],
        map_knowledge: Dict[str, Any] | None = None,
        physics_rules: List[Any] | None = None,
    ) -> Dict[str, Any]:
        actor = str(intent_item.get("actor", "Unknown"))
        intent = str(intent_item.get("intent", "")).strip()
        source = str(intent_item.get("source", ""))
        action_kind = str(intent_item.get("action_kind", "")).strip()
        action_target = str(intent_item.get("action_target", "")).strip()
        target_reference_kind = str(
            intent_item.get("target_reference_kind", "")
        ).strip()
        current_location = scene_state.get_actor_location(actor) if scene_state else None
        actor_state = scene_state.get_actor_state(actor) if scene_state else {}
        result = {
            "actor": actor,
            "intent": intent,
            "verdict": "allow",
            "reason": "",
            "suggested_intent": "",
            "rewrite_location": None,
            "rule": "none",
            "action_kind": action_kind,
            "action_target": action_target,
            "target_reference_kind": target_reference_kind,
        }
        if source in {"injected"} or actor == "World" or not intent:
            return result

        if target_reference_kind == "world_object" and (
            not scene_state or action_target not in scene_state.world_objects
        ):
            result.update(
                verdict="block",
                reason=f"动作目标{action_target}在完成前已不再存在。",
                rule="stale_target",
            )
            return result
        if target_reference_kind == "actor" and (
            not scene_state or action_target not in scene_state.actor_states
        ):
            result.update(
                verdict="block",
                reason=f"动作目标{action_target}在完成前已不再存在。",
                rule="stale_target",
            )
            return result

        if action_kind == "communicate":
            # Speech is checked for delivery reachability. Its quoted content
            # remains an attributed claim, including impossible or deceitful
            # claims; it does not perform the physical action it describes.
            target_check = self.assess_structured_target(
                scene_state, actor, action_kind, action_target, current_location,
            )
            if target_check:
                result.update(target_check)
            return result

        target_check = self.assess_structured_target(
            scene_state,
            actor,
            action_kind,
            action_target,
            current_location,
        )
        if target_check:
            result.update(target_check)
            if result.get("verdict") == "block":
                return result

        movement = self.assess_movement(
            scene_state,
            actor,
            intent,
            current_location,
            explicit_target=action_target if action_kind == "move" else "",
            map_knowledge=map_knowledge,
        )
        if action_kind != "move":
            movement = None
        if movement:
            result.update(movement)
        return result

    def assess_structured_target(
        self,
        scene_state: Any,
        actor: str,
        action_kind: str,
        target: str,
        current_location: Any,
    ) -> Dict[str, Any] | None:
        if not scene_state or not target:
            return None
        if action_kind == "communicate" and target in scene_state.actor_states:
            if scene_state.get_actor_location(target) != current_location:
                return {
                    "verdict": "block",
                    "reason": f"{target}不在{actor}当前可直接交流的地点。",
                    "rule": "communication_range",
                }
        if action_kind in {"observe", "interact"} and target in scene_state.world_objects:
            if scene_state.is_location(target):
                if target != current_location:
                    return {
                        "verdict": "block",
                        "reason": f"{actor}不能从当前位置直接{action_kind}异地目标{target}。",
                        "rule": "action_range",
                    }
                return None
            visible = set(scene_state.get_visible_objects(actor))
            if target not in visible:
                return {
                    "verdict": "block",
                    "reason": f"{target}当前不在{actor}可感知的对象范围内。",
                    "rule": "target_visibility",
                }
            if action_kind == "interact" and not scene_state.is_object_accessible(
                target, actor
            ):
                return {
                    "verdict": "block",
                    "reason": f"{target}当前隔着不可访问的容器。",
                    "rule": "target_access",
                }
        return None

    def assess_movement(
        self,
        scene_state,
        actor,
        intent,
        current_location,
        *,
        explicit_target: str = "",
        map_knowledge: Dict[str, Any] | None = None,
    ):
        if not scene_state or not intent or not current_location:
            return None
        target = (
            explicit_target
            if explicit_target in scene_state.get_known_locations()
            else None
        )
        if not target or target == current_location:
            return None
        known_locations = (
            set(map_knowledge.get("known_locations", []) or [])
            if map_knowledge is not None
            else None
        )
        if known_locations is not None and target not in known_locations:
            return {
                "verdict": "block",
                "reason": f"{actor}尚不知道如何前往{target}。",
                "suggested_intent": "",
                "rewrite_location": None,
                "rule": "unknown_destination",
            }
        connected = {
            str(name)
            for name in scene_state.get_object_state(current_location).get("connected_to", [])
        }
        if target in connected:
            return {
                "verdict": "allow",
                "reason": "",
                "suggested_intent": "",
                "rewrite_location": target,
                "rule": "movement",
            }
        path = (
            self.find_known_path(map_knowledge, current_location, target)
            if map_knowledge is not None
            else self.find_path(scene_state, current_location, target)
        )
        if path and len(path) >= 2:
            if path[1] not in connected:
                return {
                    "verdict": "block",
                    "reason": f"{actor}记得的下一段道路目前无法通行。",
                    "suggested_intent": "",
                    "rewrite_location": None,
                    "rule": "stale_route",
                }
            return {
                "verdict": "rewrite",
                "reason": f"{actor}不能一步直接到达{target}，需要按空间连通性移动。",
                "suggested_intent": f"先前往{path[1]}",
                "rewrite_location": path[1],
                "rule": "movement_path",
            }
        return {
            "verdict": "block",
            "reason": f"{target} 目前不是可直接到达的位置。",
            "suggested_intent": "",
            "rewrite_location": None,
            "rule": "movement_blocked",
        }

    def find_path(self, scene_state, start: str, target: str) -> List[str]:
        if start == target:
            return [start]
        queue = [[start]]
        visited = {start}
        while queue:
            path = queue.pop(0)
            for raw_neighbor in scene_state.get_object_state(path[-1]).get("connected_to", []):
                neighbor = str(raw_neighbor)
                if neighbor in visited:
                    continue
                next_path = path + [neighbor]
                if neighbor == target:
                    return next_path
                visited.add(neighbor)
                queue.append(next_path)
        return []

    @staticmethod
    def find_known_path(
        map_knowledge: Dict[str, Any], start: str, target: str
    ) -> List[str]:
        routes = map_knowledge.get("known_routes", {}) or {}
        queue = [[start]]
        visited = {start}
        while queue:
            path = queue.pop(0)
            for raw_neighbor in routes.get(path[-1], []) or []:
                neighbor = str(raw_neighbor)
                if neighbor in visited:
                    continue
                next_path = path + [neighbor]
                if neighbor == target:
                    return next_path
                visited.add(neighbor)
                queue.append(next_path)
        return [start] if start == target else []
