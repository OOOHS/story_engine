from typing import Dict, Any, List, Optional, Union
from pydantic import PrivateAttr
from src.story_engine.core.component import Component
from src.story_engine.llm.provider import LLMProvider
from src.story_engine.scenarios.config import ScenarioConfig

class NarrativeControl(Component):
    """
    Component that handles narrative generation.
    """
    llm_config: Dict[str, Any] = {}
    scenario: Optional[ScenarioConfig] = None # Hold reference to scenario rules
    _llm: Optional[LLMProvider] = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        config = self.llm_config or data.get("model_config", {})
        self._llm = LLMProvider(**config)

    def act(self) -> Union[str, Dict[str, Any]]:
        if not self.entity:
            return "错误：未绑定实体。"
            
        scene_state = self.entity.get_component("SceneState")
        observation = self.entity.get_component("Observation")
        
        # 将默认值也改为中文
        context = scene_state.description if scene_state else "未知地点"
        state_dump = scene_state.get_state_string() if scene_state else ""
        time = "当前时间" 
        
        recent_actions = observation.get_text() if observation else "无动作。"
        
        # 检索记忆
        memory = self.entity.get_component("Memory")
        relevant_memories = []
        if memory and recent_actions:
            relevant_memories = memory.retrieve(recent_actions, n_results=3)
        memories_text = "\n".join(relevant_memories) if relevant_memories else "无"

        # 构建规则文本
        rules_text = ""
        if self.scenario:
            # 这里对应原代码的 "\nRules:\n"
            rules_text = "\n世界规则：\n" + "\n".join([f"- {r}" for r in self.scenario.rules])
            
        # --- 中文 Prompt 开始 ---
        prompt = f"""
        你现在是这个世界的【物理引擎】和【环境模拟器】（World Engine）。
        你的职责不是讲故事，而是客观地判定行为结果并更新世界状态。
        
        当前剧本：{self.scenario.name if self.scenario else '通用剧本'}
        当前环境：{context}
        {state_dump}
        当前时间：{time}
        {rules_text}
        
        相关历史记忆（供参考）：
        {memories_text}
        
        最近发生的动作：
        {recent_actions}
        
        任务目标：
        1. 【动作判定】：检查所有角色的动作是否符合逻辑、物理规则。
           - 严厉打击不符合物理定律或世界设定的行为（如瞬移、凭空造物）。
           - 对于失败的动作，直接描述其失败的物理后果（如“撞墙”、“抓空”）。
        2. 【状态更新】：计算这些动作对环境和其他角色造成了什么物理/状态上的改变。
           - 必须明确哪些物体/属性发生了变化（如：门开了，杯子碎了，灯灭了）。
        3. 【客观输出】：用客观、中立、简洁的语言描述发生的事情。
           - 就像一个文字冒险游戏的后台日志。
           - 不要使用修辞、心理描写或情感渲染。
           - 不要试图构建戏剧性冲突，只陈述事实。
        4. 【涌现与自由演化】：世界按因果自然演化，不必死扣预设剧情；上述规则是边界而非剧本。可以引入意外但合理的事件（如路人、环境变化、新到场者），只要符合世界规则即可。
        5. 【新角色（可选）】：若剧情自然需要新角色登场（如有人推门而入、新客人、被卷入的路人），在输出末尾增加 INTRODUCE_CHARACTER 块，该角色从下一轮起将作为可行动角色参与模拟。仅当剧情确实需要时使用，不要每步都加。
        
        输出格式（严格遵守）：
        
        NARRATION:
        [这里写客观的事件描述]
        
        STATE_UPDATE:
        ```json
        {{
            "object_name": {{"property": "new_value"}},
            "door": {{"state": "open", "locked": false}},
            "glass": {{"integrity": "broken"}}
        }}
        ```
        (如果没有状态改变，STATE_UPDATE 输出 {{}})
        
        (若本步有新角色自然登场，再增加以下块，否则省略)
        INTRODUCE_CHARACTER:
        ```json
        {{"name": "角色名", "role": "身份/职业", "personality": "简短性格", "goals": ["目标1", "目标2"]}}
        ```
        """
        # --- 中文 Prompt 结束 ---
        
        response = self._llm.generate(prompt)
        content = response["content"]
        
        # Parse output
        import re
        import json
        
        narration = content
        
        # Extract JSON
        json_match = re.search(r"STATE_UPDATE:.*?```json(.*?)```", content, re.DOTALL)
        if not json_match:
             # Try without code blocks
             json_match = re.search(r"STATE_UPDATE:(.*)", content, re.DOTALL)
        
        if json_match:
            json_str = json_match.group(1).strip()
            try:
                updates = json.loads(json_str)
                if scene_state and isinstance(updates, dict):
                    for obj_name, props in updates.items():
                        scene_state.update_object_state(obj_name, props)
                        print(f"[State Update] {obj_name} -> {props}")
            except Exception as e:
                print(f"[State Update Error] Failed to parse JSON: {e}")
                
        # Extract Narration
        narration_match = re.search(r"NARRATION:(.*?)(?=STATE_UPDATE:|INTRODUCE_CHARACTER:|$)", content, re.DOTALL)
        if narration_match:
            narration = narration_match.group(1).strip()
        else:
            narration = re.sub(r"STATE_UPDATE:.*", "", content, flags=re.DOTALL).strip()
            narration = re.sub(r"INTRODUCE_CHARACTER:.*", "", narration, flags=re.DOTALL).strip()

        # Extract INTRODUCE_CHARACTER (optional)
        intro_match = re.search(r"INTRODUCE_CHARACTER:.*?```json\s*(.*?)```", content, re.DOTALL)
        if not intro_match:
            intro_match = re.search(r"INTRODUCE_CHARACTER:\s*(.+?)(?=\n\n|\Z)", content, re.DOTALL)
        introduce_character = None
        if intro_match:
            try:
                intro_str = intro_match.group(1).strip()
                introduce_character = json.loads(intro_str)
                if isinstance(introduce_character, dict) and introduce_character.get("name"):
                    introduce_character.setdefault("role", "路人")
                    introduce_character.setdefault("personality", "未知")
                    introduce_character.setdefault("goals", [])
                    if isinstance(introduce_character.get("goals"), str):
                        introduce_character["goals"] = [introduce_character["goals"]]
            except (json.JSONDecodeError, TypeError):
                introduce_character = None

        if introduce_character:
            return {"narration": narration, "introduce_character": introduce_character}
        return narration
