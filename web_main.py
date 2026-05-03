"""
Web entry point for the Story Engine browser UI.
This layer stays outside the engine core and talks to Session through a thin adapter.

Usage:
    python web_main.py --host 0.0.0.0 --port 8000
"""
import argparse

from dotenv import load_dotenv

load_dotenv()

from src.story_engine.scenarios.false_heiress import false_heiress_scenario
from src.story_engine.web import WebGameAdapter, run_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Story Engine Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    adapter = WebGameAdapter(
        false_heiress_scenario,
        title="真假千金 · Web 试玩",
    )
    run_server(adapter, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
