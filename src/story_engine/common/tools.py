from pydantic import BaseModel, Field
from typing import List, Dict, Any
from src.story_engine.agents.base import BaseAgent
from src.story_engine.components.memory import VectorMemory

class QueryMemoryInput(BaseModel):
    query: str = Field(..., description="Query text")

class PerformActionInput(BaseModel):
    description: str = Field(..., description="Action description")

class AgentTools:
    def __init__(self, agent: BaseAgent):
        self.agent = agent
    
    def query_memory(self, input_data: QueryMemoryInput):
        memory: VectorMemory = self.agent.get_component("VectorMemory")
        if not memory: return []
        return memory.query_memory(input_data.query)
    
    def perform_action(self, input_data: PerformActionInput):
        return f"Action proposed: {input_data.description}"

def get_tool_functions(agent_tools: AgentTools) -> List[Dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": "query_memory", "parameters": QueryMemoryInput.model_json_schema()}},
        {"type": "function", "function": {"name": "perform_action", "parameters": PerformActionInput.model_json_schema()}}
    ]
