"""Explicit bundled-content console entry point."""
import argparse

from dotenv import load_dotenv

from src.story_engine.agents import (
    HermesContainerConfig,
    default_hermes_runtime_factories,
    default_local_hermes_config,
    default_local_hermes_runtime_factories,
    default_offline_runtime_factories,
)
from src.story_engine.session import (
    ConsoleDriver,
    PLAY_PROFILES,
    bind_play_profile,
    compile_scenario_seed,
    compile_scenario_seed_file,
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
    source.add_argument(
        "--seed",
        help=(
            "Author-facing seed text or JSON/YAML document; it is compiled "
            "into a validated ScenarioConfig before startup."
        ),
    )
    source.add_argument(
        "--seed-file",
        help="UTF-8 file containing author-facing seed text or JSON/YAML.",
    )
    parser.add_argument("--title", default="Story Engine · Console")
    parser.add_argument(
        "--profile",
        choices=PLAY_PROFILES,
        default="production",
        help="Explicit startup profile; offline is deterministic and model-free.",
    )
    parser.add_argument(
        "--hermes-transport",
        choices=("docker", "local"),
        default="local",
        help="Hermes process transport. Docker is deprecated and cannot restore sessions.",
    )
    parser.add_argument("--hermes-python", default="python")
    parser.add_argument("--hermes-entrypoint", default="")
    parser.add_argument("--hermes-vendor-root", default="")
    parser.add_argument("--hermes-working-directory", default="")
    parser.add_argument(
        "--hermes-home",
        default="",
        help="Host-owned subject home root. Defaults to ./.story-hermes.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    if args.scenario_ref:
        scenario = load_scenario_reference(args.scenario_ref)
    elif args.seed is not None:
        scenario = compile_scenario_seed(args.seed)
    elif args.seed_file:
        scenario = compile_scenario_seed_file(args.seed_file)
    else:
        scenario = load_bundled_scenario(args.scenario)
    scenario = bind_play_profile(scenario, args.profile)
    if args.profile == "offline":
        factories = default_offline_runtime_factories()
    elif args.hermes_transport == "docker":
        factories = default_hermes_runtime_factories(HermesContainerConfig())
    else:
        factories = default_local_hermes_runtime_factories(
            default_local_hermes_config(
                python_executable=args.hermes_python,
                entrypoint_path=args.hermes_entrypoint,
                vendor_root=args.hermes_vendor_root,
                working_directory=args.hermes_working_directory,
                home_root=args.hermes_home,
            )
        )
    session = create_session(scenario, agent_runtime_factories=factories)
    driver = ConsoleDriver(session, title=args.title)
    driver.run()


if __name__ == "__main__":
    main()
