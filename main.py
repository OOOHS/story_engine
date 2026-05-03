"""
Entry point: load env, create a session from a scenario, run with console driver.
换剧本：改下面 import 和 create_session 的参数即可。
"""
from dotenv import load_dotenv

load_dotenv()

from src.story_engine.session import create_session, ConsoleDriver
from src.story_engine.scenarios.false_heiress import false_heiress_scenario


def main():
    session = create_session(false_heiress_scenario)
    driver = ConsoleDriver(session, title="真假千金 — 玩家受限视角文字冒险")
    driver.run()


if __name__ == "__main__":
    main()
