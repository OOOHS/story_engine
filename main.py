"""Explicit bundled-content console entry point."""
import argparse

from dotenv import load_dotenv

from src.story_engine.agents import default_hermes_runtime_factories
from src.story_engine.session import (
    ConsoleDriver,
    create_session,
    load_scenario_reference,
)
from src.story_engine_content.catalog import (
    available_bundled_scenarios,
    load_bundled_scenario,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a bundled Story Engine scenario")
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
    parser.add_argument("--title", default="Story Engine · Console")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    scenario = (
        load_scenario_reference(args.scenario_ref)
        if args.scenario_ref
        else load_bundled_scenario(args.scenario)
    )
    session = create_session(
        scenario, agent_runtime_factories=default_hermes_runtime_factories()
    )
    driver = ConsoleDriver(session, title=args.title)
    driver.run()


if __name__ == "__main__":
    main()
