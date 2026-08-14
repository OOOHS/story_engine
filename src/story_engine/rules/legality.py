from typing import Any, Callable, Dict, List, Optional

from src.story_engine.common.movement_intent import extract_move_target_from_intent


ProfileRule = Callable[[str, Dict[str, Any]], str]


class LegalityEngine:
    """Applies hard world-law and spatial constraints to action proposals."""

    def __init__(self, profile_rules: Optional[Dict[str, ProfileRule]] = None) -> None:
        self.profile_rules: Dict[str, ProfileRule] = {
            "mundane": self.detect_mundane_violation,
        }
        self.profile_rules.update(profile_rules or {})

    def register_profile(self, profile: str, rule: ProfileRule) -> None:
        self.profile_rules[str(profile)] = rule

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
        if source in {"timeline", "injected"} or actor == "World" or not intent:
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

        if physics_rules:
            # Content-declared rules take priority over any hardcoded
            # profile function: a scenario that declares physics_rules is
            # explicitly opting into a fully data-driven gate for this
            # profile, magic worlds included.
            violation = self.detect_configured_violation(intent, actor_state, physics_rules)
            if violation:
                result.update(
                    verdict="block",
                    reason=violation,
                    rule=f"{physics_profile}_physics",
                )
                return result
        else:
            profile_rule = self.profile_rules.get(str(physics_profile))
            if profile_rule:
                violation = profile_rule(intent, actor_state)
                if violation:
                    result.update(
                        verdict="block",
                        reason=violation,
                        rule=f"{physics_profile}_physics",
                    )
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
        if movement:
            result.update(movement)
        return result

    def detect_mundane_violation(self, intent: str, actor_state: Dict[str, Any]) -> str:
        capabilities = actor_state.get("capabilities", []) if isinstance(actor_state, dict) else []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        capabilities = {str(item) for item in capabilities}
        patterns = [
            (("飞起来", "悬浮", "漂浮在空中", "腾空而起"), "flight", "普通人在这个世界里不能突然飞起来。"),
            (("瞬移", "传送到", "闪现到"), "teleportation", "这个世界里没有瞬间移动这种能力。"),
            (("穿墙", "穿过墙"), "phasing", "普通人不能直接穿墙。"),
            (("隐身", "突然消失"), "invisibility", "普通人不能无痕隐身或凭空消失。"),
            (("凭空变出", "召唤出", "变出一把", "变出一只"), "conjuration", "普通人不能凭空变出物件。"),
            (("放出火球", "打出雷电", "施法", "念咒"), "magic", "当前世界不是可随意施法的物理规则。"),
        ]
        for keywords, capability, reason in patterns:
            if any(keyword in str(intent or "") for keyword in keywords):
                if capability in capabilities or "supernatural" in capabilities:
                    return ""
                return reason
        return ""

    def detect_configured_violation(
        self,
        intent: str,
        actor_state: Dict[str, Any],
        rules: List[Any],
    ) -> str:
        """Same keyword-gate behavior as detect_mundane_violation, but the
        keyword/capability/reason table comes from content (PhysicsRuleConfig)
        instead of a hardcoded Python list. A scenario can therefore declare
        its own physics (e.g. a magic world where "飞起来" is fine for
        capability "mage") without editing this file.
        """
        capabilities = actor_state.get("capabilities", []) if isinstance(actor_state, dict) else []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        capabilities = {str(item) for item in capabilities}
        text = str(intent or "")
        for rule in rules or []:
            if isinstance(rule, dict):
                keywords = rule.get("keywords", [])
                capability = str(rule.get("capability", ""))
                reason = str(rule.get("reason", ""))
            else:
                keywords = getattr(rule, "keywords", [])
                capability = str(getattr(rule, "capability", ""))
                reason = str(getattr(rule, "reason", ""))
            if any(str(keyword) in text for keyword in keywords or []):
                if capability in capabilities or "supernatural" in capabilities:
                    continue
                return reason
        return ""

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
            else self.extract_target_location(scene_state, intent, current_location)
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

    def extract_target_location(self, scene_state, intent, current_location):
        aliases = {
            str(location): list((state or {}).get("aliases", []) or [])
            for location, state in scene_state.world_objects.items()
            if isinstance(state, dict) and scene_state.is_location(location)
        }
        return extract_move_target_from_intent(
            intent=intent,
            current_location=str(current_location) if current_location else None,
            connected_locations=scene_state.get_object_state(current_location).get("connected_to", []),
            known_locations=scene_state.get_known_locations(),
            location_aliases=aliases,
        )

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
