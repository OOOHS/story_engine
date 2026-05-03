from typing import Dict, Any, Optional
import re
from pydantic import PrivateAttr
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.components.identity import Identity
from src.story_engine.components.memory import Memory
from src.story_engine.components.observation import Observation
from src.story_engine.components.planning import Planning

class Persona(Component):
    """
    The 'Brain' of the agent. Handles decision making using LLM.
    """
    llm_config: Dict[str, Any] = {}
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def act(self, immediate_context: str = "") -> Dict[str, str]:
        if not self.entity:
            return {"thought": "No entity attached.", "action": "Error: No entity attached."}

        identity = self.entity.get_component("Identity")
        memory = self.entity.get_component("Memory")
        observation = self.entity.get_component("Observation")
        planning = self.entity.get_component("Planning")

        # Gather context
        name = self.entity.name
        role = identity.role if identity else "Unknown"
        personality = identity.personality if identity else "None"
        goals = identity.goals if identity else []
        is_player = bool(identity.is_player) if identity else False
        
        recent_obs = observation.get_text() if observation else "None"
        current_plan = planning.get_plan() if planning else "None"
        
        # Retrieve relevant memories (simple query based on observations)
        relevant_memories = []
        if memory and recent_obs:
            relevant_memories = memory.retrieve(recent_obs, n_results=3)
        
        memories_text = "\n".join(relevant_memories)
        auto_policy = ""
        if is_player:
            auto_policy = """
        Player Auto Guidance:
        - You are acting as the player's character inside the same proposal loop as everyone else.
        - Do not keep choosing passive compliance turn after turn unless the scene truly forces it.
        - When the atmosphere is hostile, prefer claiming position, asking a pointed question, probing, or a restrained refusal over silently shrinking away.
        - Keep your outward action concrete and playable. Avoid ornamental gestures, cinematic gaze descriptions, or over-written emotional staging unless they materially change the situation.
        """

        prompt = f"""
        You are {name}, a {role}.
        Personality: {personality}
        Goals: {goals}
        
        Relevant Memories:
        {memories_text}
        
        Current Plan:
        {current_plan}
        
        Recent Observations:
        {recent_obs}

        Immediate Current-Turn Context:
        {immediate_context or "None"}
        {auto_policy or ""}
        
        Task: Decide on your next intended move for the engine's Input phase.
        1. OBSERVATION: Briefly review what you see and know.
        2. THOUGHT: Reason about your goals and the situation.
        3. ACTION: Describe your attempted action or statement for this turn.
           This is an intent proposal, not the final resolved outcome.
           Keep it short and immediately legible.
        4. If you are hiding motives, be subtle and socially plausible.
           Do not theatricalize yourself or spell out secrets in your outward action.
        5. Prefer ordinary, grounded behavior over melodramatic or over-explicit reactions.
        5.5. If the immediate context tells you your habitual pressure style or leverage, use it now instead of defaulting to passive observation.
        6. THOUGHT should be at most one short sentence.
        7. ACTION should be one clear sentence. Keep it tight, but do not self-truncate with ellipses.
        
        Output Format (STRICT):
        THOUGHT: [Your internal reasoning]
        ACTION: [Your attempted action]
        """

        response = self._llm.generate(prompt)
        content = response["content"]
        if content.startswith("[LLM disabled]") or content.startswith("[LLM error"):
            return {
                "thought": "模型当前不可用，先采取保守行动。",
                "action": "先观察局势，避免贸然暴露自己。",
            }
        
        # Parse the response
        thought = ""
        action = content
        
        thought_match = re.search(r"THOUGHT:\s*(.*?)(?=ACTION:|$)", content, re.DOTALL)
        action_match = re.search(r"ACTION:\s*(.*)", content, re.DOTALL)
        
        if thought_match:
            thought = self._compact_line(thought_match.group(1).strip(), max_len=40)
        if action_match:
            action = self._normalize_action_line(action_match.group(1).strip())

        if not action:
            action = "先观察局势。"
            
        return {"thought": thought, "action": action}

    def _compact_line(self, text: str, max_len: int) -> str:
        if not text:
            return ""
        line = " ".join(str(text).split())
        parts = re.split(r"(?<=[。！？；.!?;])\s*", line)
        candidate = next((part.strip() for part in parts if part.strip()), line)
        if len(candidate) <= max_len:
            return candidate
        trimmed = candidate[: max_len - 1].rstrip("，,、 ")
        return trimmed + "…"

    def _normalize_action_line(self, text: str) -> str:
        if not text:
            return ""
        line = " ".join(str(text).split())
        parts = re.split(r"(?<=[。！？；.!?;])\s*", line)
        candidate = next((part.strip() for part in parts if part.strip()), line)
        return candidate.strip()
