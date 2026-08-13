from typing import Any, Dict, List


class PhysicalAffordanceEngine:
    """Derive universal object interactions from current physical state."""

    TAKE = "engine:take"
    DROP = "engine:drop"
    OPEN = "engine:open"
    CLOSE = "engine:close"
    IDS = {TAKE, DROP, OPEN, CLOSE}

    LABELS = {
        TAKE: "拿起",
        DROP: "放下",
        OPEN: "打开",
        CLOSE: "关闭",
    }

    POLICY_TAGS = {
        TAKE: ("acquire",),
        DROP: ("release",),
        OPEN: ("access",),
        CLOSE: (),
    }

    def build_opportunities(
        self,
        scene_state: Any,
        actor_name: str,
    ) -> List[Dict[str, Any]]:
        if not scene_state or not actor_name:
            return []
        actor_location = str(scene_state.get_actor_location(actor_name) or "").strip()
        if not actor_location:
            return []
        opportunities: List[Dict[str, Any]] = []
        for object_id, state in scene_state.get_visible_objects(actor_name).items():
            if not isinstance(state, dict):
                continue
            effective_location = str(
                scene_state.get_effective_object_location(object_id) or ""
            ).strip()
            if effective_location != actor_location:
                continue
            accessible = bool(scene_state.is_object_accessible(object_id, actor_name))
            owner = str(state.get("owner") or "").strip()
            portable = bool(state.get("portable", True))
            if portable and accessible and not owner:
                opportunities.append(self._opportunity(object_id, self.TAKE))
            elif portable and owner == actor_name:
                opportunities.append(self._opportunity(object_id, self.DROP))
            if bool(state.get("is_container", False)) and accessible:
                affordance_id = (
                    self.CLOSE
                    if bool(state.get("container_open", True))
                    else self.OPEN
                )
                opportunities.append(self._opportunity(object_id, affordance_id))
        opportunities.sort(
            key=lambda item: (item["object_id"], item["affordance_id"])
        )
        return opportunities

    def is_available(
        self,
        scene_state: Any,
        actor_name: str,
        object_id: str,
        affordance_id: str,
    ) -> bool:
        return any(
            item["object_id"] == object_id
            and item["affordance_id"] == affordance_id
            for item in self.build_opportunities(scene_state, actor_name)
        )

    def build_operation(
        self,
        scene_state: Any,
        actor_name: str,
        object_id: str,
        affordance_id: str,
    ) -> Dict[str, Any] | None:
        if not self.is_available(
            scene_state, actor_name, object_id, affordance_id
        ):
            return None
        base = {
            "object_id": object_id,
            "actor": actor_name,
            "affordance_id": affordance_id,
            "reason": "Agent 选择了当前可用的内建物理能力，Host 据此结算对象操作",
        }
        if affordance_id == self.TAKE:
            return {"operation": "relocate", **base, "owner": actor_name}
        if affordance_id == self.DROP:
            location = str(scene_state.get_actor_location(actor_name) or "").strip()
            return {"operation": "relocate", **base, "location": location}
        if affordance_id in {self.OPEN, self.CLOSE}:
            return {
                "operation": "set_container_state",
                **base,
                "open": affordance_id == self.OPEN,
            }
        return None

    @classmethod
    def is_builtin_id(cls, affordance_id: Any) -> bool:
        return str(affordance_id or "").strip() in cls.IDS

    @classmethod
    def is_reserved_id(cls, affordance_id: Any) -> bool:
        return str(affordance_id or "").strip().startswith("engine:")

    @classmethod
    def _opportunity(
        cls,
        object_id: str,
        affordance_id: str,
    ) -> Dict[str, Any]:
        return {
            "object_id": object_id,
            "affordance_id": affordance_id,
            "label": cls.LABELS[affordance_id],
            "need_effects": {},
            "consumes": False,
            "exclusive": affordance_id == cls.TAKE,
            "requires_owner": affordance_id == cls.DROP,
            "required_capabilities": [],
            "missing_capabilities": [],
            "available": True,
            "relief_score": 0.0,
            "policy_tags": list(cls.POLICY_TAGS.get(affordance_id, ())),
            "source": "engine_physics",
        }
