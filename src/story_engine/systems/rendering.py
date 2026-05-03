from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity


class RenderingSystem(System):
    """
    Converts resolved simulation output into player-facing narration.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        simulation_result = context.get("simulation_result", {})
        if not simulation_result:
            return
        player_pov = context.get("player_pov", {})
        visibility_window = context.get("visibility_window", {})
        visible_locations = visibility_window.get("locations", []) if isinstance(visibility_window, dict) else []
        visible_simulation = self._build_visible_simulation(
            simulation_result,
            player_pov,
            visible_locations=visible_locations,
        )
        visible_actor_names = set(player_pov.get("visible_actors", []))
        timeline = context.get("timeline", {})
        conflict = context.get("conflict", {})
        social = context.get("social", {})
        storylet_pressure = context.get("storylet_pressure", {})

        for name, entity in entities.items():
            renderer = entity.get_component("NarrativeRenderer")
            if not renderer:
                continue
            scene_state = entity.get_component("SceneState")
            continuity = {}
            if scene_state:
                continuity = scene_state.get_scene_flag("last_player_visible_snapshot", {}) or {}
            visible_fact_lines = self._collect_visible_fact_lines(visible_simulation)

            render_payload = {
                "player_pov": player_pov,
                "simulation_result": visible_simulation,
                "timeline": timeline,
                "conflict": conflict,
                "social": social,
                "storylet_pressure": storylet_pressure,
                "continuity": continuity,
                "current_visible_facts": visible_fact_lines,
                "visible_spatial_facts": list(player_pov.get("visible_spatial_facts", [])),
                "spatial_layout": player_pov.get("spatial_layout", {}),
            }

            if name in overrides:
                narration = overrides[name]
                print(f"\n> World Engine (MANUAL RENDER): {narration}\n")
            else:
                narration = renderer.render(render_payload)
                print(f"\n> World Engine: {narration}\n")

            if scene_state:
                scene_state.update_scene_flags(
                    {
                        "last_player_visible_snapshot": {
                            "narration": narration,
                            "visible_facts": visible_fact_lines,
                        }
                    }
                )

            observation_text = self._build_observation_text(
                narration,
                visible_fact_lines,
                list(player_pov.get("visible_spatial_facts", [])),
            )
            for target_name, target in entities.items():
                obs_comp = target.get_component("Observation")
                if obs_comp and hasattr(obs_comp, "add_observation"):
                    if target_name == name or target_name in visible_actor_names:
                        obs_comp.add_observation(observation_text)

            context["rendered_text"] = narration
            context["observation_text"] = observation_text
            context["visible_actor_names"] = list(visible_actor_names)
            context["visible_simulation_result"] = visible_simulation
            return

    def _build_visible_simulation(
        self,
        simulation_result: Dict[str, Any],
        player_pov: Dict[str, Any],
        visible_locations: List[str] = None,
    ) -> Dict[str, Any]:
        allowed_locations = [
            str(item).strip()
            for item in (visible_locations or [])
            if str(item).strip()
        ]
        if not allowed_locations:
            visible_location = str(player_pov.get("location", "")).strip()
            if visible_location:
                allowed_locations.append(visible_location)
        allowed_location_set = set(allowed_locations)
        visible_actions: List[Dict[str, Any]] = []
        for item in simulation_result.get("resolved_actions", []):
            visibility = item.get("visibility", "public")
            item_location = item.get("location")
            if visibility != "public":
                continue
            if allowed_location_set and item_location and str(item_location).strip() not in allowed_location_set:
                continue
            visible_actions.append(item)

        return {
            **simulation_result,
            "resolved_actions": visible_actions,
        }

    def _collect_visible_fact_lines(self, simulation_result: Dict[str, Any]) -> List[str]:
        visible_facts: List[str] = []
        for item in simulation_result.get("resolved_actions", []):
            if item.get("visibility", "public") != "public":
                continue
            actor = item.get("actor", "某人")
            result = item.get("result", "")
            if result:
                visible_facts.append(f"{actor}: {result}")
        return visible_facts

    def _build_observation_text(
        self,
        narration: str,
        visible_facts: List[str],
        spatial_facts: List[str],
    ) -> str:
        lines: List[str] = []
        if spatial_facts:
            lines.append("当前站位：")
            lines.extend([f"- {fact}" for fact in spatial_facts[:4]])
        if visible_facts:
            lines.append("本回合可见事实：")
            lines.extend([f"- {fact}" for fact in visible_facts])
        if narration:
            lines.append(f"叙事摘要：{narration}")
        return "\n".join(lines) if lines else narration
