from typing import Any, ClassVar, Dict

from src.story_engine.components.narrative_renderer import NarrativeRenderer


class HostRuleNarrativeRenderer(NarrativeRenderer):
    """Fact-only renderer for runtime audits that must not call another LLM."""

    component_slot: ClassVar[str] = "NarrativeRenderer"

    def render(self, render_payload: Dict[str, Any]) -> str:
        return self._fallback_render(render_payload)
