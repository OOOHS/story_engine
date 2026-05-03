"""
Console driver: player-centric interaction.
Each step defaults to player manual action, with optional per-step autonomous action.
"""
from typing import Dict, Optional
from .session import Session


class ConsoleDriver:
    """Runs a Session with a simple player-action prompt."""

    def __init__(self, session: Session, title: str = ""):
        self.session = session
        self.title = title or session.scenario.name

    def run(self) -> None:
        """Main loop: player input overrides the turn; blank input lets the character act autonomously."""
        print(f"\n=== {self.title} ===\n")
        print("按 Ctrl+C 或输入 /q 退出；直接回车表示该轮由角色顺势行动。\n")

        player_name = self._resolve_main_player()
        if player_name:
            print(f"主视角玩家：{player_name}")
            print("每回合你都可以直接输入覆盖该角色；留空则让她按局势自行提议行动。\n")
        else:
            print("未找到可用玩家角色，将全员按 AI 自主行动。\n")

        try:
            while True:
                print(f"\n--- 第 {self.session.step_count + 1} 步 ---")
                overrides: Dict[str, str] = {}

                if player_name:
                    command = input(
                        f"{player_name} 行动（输入行动文本；留空=该轮自主；/q=退出）> "
                    ).strip()
                    if command.lower() == "/q":
                        break
                    if command:
                        overrides[player_name] = command

                print("\n正在运行模拟步骤...")
                self.session.run_step(overrides=overrides)
        except KeyboardInterrupt:
            print("\n模拟被用户停止。")

    def _resolve_main_player(self) -> Optional[str]:
        player_name = self.session.player_character_name
        if player_name and player_name in self.session.entities:
            return player_name

        for name, entity in self.session.entities.items():
            if entity.get_component("SimulationControl") or entity.get_component("NarrativeControl"):
                continue
            return name
        return None
