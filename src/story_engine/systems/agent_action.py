from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity

class AgentActionSystem(System):
    """
    System responsible for processing agent actions.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        dispatcher = context.get("dispatcher")
        actions_buffer = context.setdefault("actions", [])
        
        for name, entity in entities.items():
            # Skip GM (NarrativeControl) - they act in NarrativeSystem
            if entity.get_component("NarrativeControl"):
                continue
            
            action = None
            
            # 1. Check for manual override
            if name in overrides:
                action = overrides[name]
                print(f"> {name} (MANUAL): {action}")
                self.logger.info(f"{name} (MANUAL): {action}")
            else:
                # 2. AI Act via Persona Component
                persona = entity.get_component("Persona")
                if persona and hasattr(persona, "act"):
                    print(f"... {name} is observing ...", end="\r", flush=True)
                    # Simulate thinking time or just state update
                    print(f"... {name} is thinking ...", end="\r", flush=True)
                    
                    result = persona.act()
                    
                    # Clear status line
                    print(" " * 50 + "\r", end="", flush=True)
                    
                    if isinstance(result, dict):
                        thought = result.get("thought", "")
                        action = result.get("action", "")
                        
                        if thought:
                            # Print thought in a different style (e.g., grey or indented)
                            print(f"{name} (Thought): {thought}")
                            self.logger.info(f"{name} (Thought): {thought}")
                    else:
                        action = str(result)
                        
                else:
                    # Entity has no brain, skip
                    continue
                
                if action:
                    print(f"> {name}: {action}")
                    self.logger.info(f"{name}: {action}")

            # 3. Publish and Record
            if action:
                formatted_action = f"{name}: {action}"
                actions_buffer.append(formatted_action)
                if dispatcher:
                    dispatcher.publish({"type": "action", "agent": name, "content": action})
