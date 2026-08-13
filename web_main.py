"""
Web entry point for the Story Engine browser UI.
This layer stays outside the engine core and talks to Session through a thin adapter.

Usage:
    python web_main.py --host 0.0.0.0 --port 8000
"""
import argparse

from dotenv import load_dotenv

from src.story_engine_content.catalog import (
    available_bundled_scenarios,
    load_bundled_scenario,
)
from src.story_engine.session import load_scenario_reference
from src.story_engine.web import WebGameAdapter, run_server


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Story Engine Web UI")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--scenario",
        choices=available_bundled_scenarios(),
        help="Bundled content alias; there is deliberately no default story.",
    )
    source.add_argument(
        "--scenario-ref",
        help="External ScenarioConfig object/factory as module.path:attribute.",
    )
    parser.add_argument("--title", default="Story Engine · Web")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    load_dotenv()
    scenario = (
        load_scenario_reference(args.scenario_ref)
        if args.scenario_ref
        else load_bundled_scenario(args.scenario)
    )
    adapter = WebGameAdapter(scenario, title=args.title)
    run_server(adapter, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
