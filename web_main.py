"""
Web entry point for the Story Engine browser UI.
This layer stays outside the engine core and talks to Session through a thin adapter.

Usage:
    python web_main.py --host 0.0.0.0 --port 8000
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from src.story_engine.agents import (
    HermesContainerConfig,
    HermesLocalProcessConfig,
    default_hermes_runtime_factories,
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
    compile_scenario_seed,
    compile_scenario_seed_file,
    load_scenario_reference,
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
        default="docker",
        help="Hermes transport for the production profile.",
    )
    parser.add_argument("--hermes-python", default="python")
    parser.add_argument("--hermes-entrypoint", default="")
    parser.add_argument("--hermes-vendor-root", default="")
    parser.add_argument("--hermes-working-directory", default="")
    return parser.parse_args(argv)


def main(argv=None) -> None:
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
    elif args.hermes_transport == "local":
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
    adapter = WebGameAdapter(
        scenario,
        title=args.title,
        agent_runtime_factories=factories,
    )
    run_server(adapter, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
