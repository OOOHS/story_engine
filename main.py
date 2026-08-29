"""Explicit bundled-content console entry point."""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from src.story_engine.agents import (
    HermesContainerConfig,
    HermesLocalProcessConfig,
    default_hermes_runtime_factories,
    default_local_hermes_runtime_factories,
)
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
    parser.add_argument(
        "--hermes-transport",
        choices=("docker", "local"),
        default="docker",
        help="Hermes process transport; local still starts one child process per character.",
    )
    parser.add_argument("--hermes-python", default="python")
    parser.add_argument("--hermes-entrypoint", default="")
    parser.add_argument("--hermes-vendor-root", default="")
    parser.add_argument("--hermes-working-directory", default="")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    scenario = (
        load_scenario_reference(args.scenario_ref)
        if args.scenario_ref
        else load_bundled_scenario(args.scenario)
    )
    if args.hermes_transport == "local":
        project_entrypoint = Path(__file__).resolve().parent / "docker" / "hermes-story" / "entrypoint.py"
        project_vendor_root = project_entrypoint.parent / "hermes-agent"
        local_config = HermesLocalProcessConfig(
            python_executable=args.hermes_python,
            entrypoint_path=args.hermes_entrypoint or str(project_entrypoint),
            vendor_root=(
                args.hermes_vendor_root
                or os.getenv("HERMES_VENDOR_ROOT", "")
                or (str(project_vendor_root) if project_vendor_root.is_dir() else "")
            ),
            working_directory=args.hermes_working_directory,
        )
        factories = default_local_hermes_runtime_factories(local_config)
    else:
        factories = default_hermes_runtime_factories(HermesContainerConfig())
    session = create_session(scenario, agent_runtime_factories=factories)
    driver = ConsoleDriver(session, title=args.title)
    driver.run()


if __name__ == "__main__":
    main()
