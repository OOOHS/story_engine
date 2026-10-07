"""
Web entry point for the Story Engine browser UI.
This layer stays outside the engine core and talks to Session through a thin adapter.

Usage:
    python web_main.py --host 0.0.0.0 --port 8000
"""
import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.story_engine.agents import (
    HermesContainerConfig,
    default_hermes_runtime_factories,
    default_local_hermes_config,
    default_local_hermes_runtime_factories,
    default_offline_runtime_factories,
)
from src.story_engine_content.catalog import (
    available_bundled_scenarios,
    load_bundled_scenario,
)
from src.story_engine.session import (
    PLAY_PROFILES,
    bind_play_profile,
    compile_play_seed,
    compile_play_seed_file,
    load_scenario_reference,
    load_session,
)
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
    source.add_argument(
        "--seed",
        help="Author-facing seed text or JSON/YAML document.",
    )
    source.add_argument(
        "--seed-file",
        help="UTF-8 file containing author-facing seed text or JSON/YAML.",
    )
    source.add_argument("--load-save", help="Restore a whole-session .storysave archive.")
    parser.add_argument("--save-path", help="Save after each step and before exit.")
    parser.add_argument("--title", default="Story Engine · Web")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
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
        help="Hermes transport. Docker is deprecated and cannot restore sessions.",
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


def main(argv=None) -> None:
    args = parse_args(argv)
    # Load the repository's .env by absolute path so launching the Web UI
    # from another directory still passes the configured Actor/GM/Narrator
    # provider settings to their respective subprocesses.  Secrets remain in
    # process environment only and are not serialized or logged.
    load_dotenv(Path(__file__).resolve().parent / ".env")
    if args.load_save:
        scenario = None
    elif args.scenario_ref:
        scenario = load_scenario_reference(args.scenario_ref)
    elif args.seed is not None:
        scenario = compile_play_seed(args.seed, profile=args.profile)
    elif args.seed_file:
        scenario = compile_play_seed_file(args.seed_file, profile=args.profile)
    else:
        scenario = load_bundled_scenario(args.scenario)
    if scenario is not None:
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
    restored = load_session(args.load_save, agent_runtime_factories=factories) if args.load_save else None
    adapter = WebGameAdapter(
        restored.scenario if restored else scenario,
        session=restored,
        save_path=args.save_path,
        title=args.title,
        agent_runtime_factories=factories,
    )
    run_server(adapter, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
