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
            print("角色空闲时你可以直接输入行动；留空则让她按局势自主决定。\n")
        else:
            print("未找到可用玩家角色，将全员按 AI 自主行动。\n")

        try:
            while True:
                print(f"\n--- 第 {self.session.step_count + 1} 步 ---")
                overrides: Dict[str, str] = {}

                if player_name:
                    if not self.session.is_actor_ready(player_name):
                        pending = self.session.pending_action(player_name)
                        action = pending.get("action", {}).get("detail", "当前行动")
                        completes_at = pending.get("completes_at")
                        print(
                            f"{player_name}仍在执行：{action}（预计时间 {completes_at} 完成）。"
                        )
                        print("世界推进到下一个完成事件……")
                        result = self.session.run_step()
                        self._report_step_result(result)
                        continue
                    self._print_decision_context(
                        self.session.player_decision_context()
                    )
                    command = input(
                        f"{player_name} 行动（输入行动文本；留空=该轮自主；/q=退出）> "
                    ).strip()
                    if command.lower() == "/q":
                        break
                    if command:
                        overrides[player_name] = command

                print("\n正在推进到下一个动作完成事件...")
                result = self.session.run_step(overrides=overrides)
                self._report_step_result(result)
        except KeyboardInterrupt:
            print("\n模拟被用户停止。")
        finally:
            self.session.close()

    def _resolve_main_player(self) -> Optional[str]:
        player_name = self.session.player_character_name
        if player_name and player_name in self.session.entities:
            return player_name

        for name, entity in self.session.entities.items():
            if entity.get_component("SimulationControl"):
                continue
            return name
        return None

    @staticmethod
    def _print_decision_context(context: Dict[str, object]) -> None:
        pending_events = list(context.get("pending_world_events", []) or [])
        pending_responses = list(
            context.get("pending_event_responses", []) or []
        )
        observations = list(context.get("passive_observations", []) or [])
        if not pending_events and not pending_responses:
            return
        pending_ids = set(pending_events).union(pending_responses)
        relevant_observations = [
            item
            for item in observations
            if isinstance(item, dict)
            and (
                item.get("event_id") in pending_ids
                or item.get("response_id") in pending_ids
            )
        ]
        print("\n你当前能够据此作出决定的变化：")
        for item in relevant_observations[-4:]:
            if isinstance(item, dict) and str(item.get("result", "")).strip():
                print(f"- {str(item['result']).strip()}")
        if pending_events:
            print(f"- 尚待处理的世界事件：{len(pending_events)} 项")
        if pending_responses:
            print(f"- 尚待处理的他人回应：{len(pending_responses)} 项")

    def _report_step_result(self, context: Dict[str, object]) -> None:
        status = self.session.public_step_status(context)
        if status["status"] == "aborted":
            print("本次宿主输入未通过验证，世界没有推进；请修正后重试。")
        elif status["status"] == "rolled_back":
            phase = status.get("failure_phase") or "权威系统"
            print(f"{phase}发生内部故障，本步骤已完整回滚，世界没有推进。")
        elif status["status"] == "delivery_failed":
            phase = status.get("failure_phase") or "表现层"
            print(f"世界已经推进，但{phase}交付失败；本轮没有可靠的新叙事文本。")
            retry = self.session.retry_delivery()
            retry_status = self.session.public_step_status(retry)
            if retry_status["status"] == "committed":
                print("交付重试已经完成；世界没有重复推进。")
            else:
                print("交付重试仍未成功；在修复表现/归档层前不会接受下一步行动。")
