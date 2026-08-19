import importlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.session import create_session, load_scenario_reference


class _StubHermesRuntime:
    """Test double standing in for the real Hermes container runtime.

    Bundled content declares agent_runtime="hermes"; these boundary tests
    care about wiring (does every declared character resolve to a live
    runtime and get registered), not about actually invoking Hermes or
    Docker, so a minimal stand-in is enough.
    """

    def decide(self, entity, perception):
        raise NotImplementedError("stub hermes runtime never decides in these tests")


def _bundled_runtime_factories():
    return {"hermes": lambda entity, runtime_config: _StubHermesRuntime()}


BUNDLED_SCENARIOS = (
    (
        "src.story_engine_content.bundled.false_heiress",
        "false_heiress_scenario",
    ),
    (
        "src.story_engine_content.bundled.cthulhu_arkham",
        "cthulhu_arkham_scenario",
    ),
    (
        "src.story_engine_content.bundled.thirteenth_floor",
        "thirteenth_floor_scenario",
    ),
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_bundled_content_modules_export_valid_explicit_scenarios():
    for module_name, attribute in BUNDLED_SCENARIOS:
        module = importlib.import_module(module_name)
        scenario = getattr(module, attribute)

        assert isinstance(scenario, ScenarioConfig)
        assert scenario.name
        assert scenario.characters
        assert {character.name for character in scenario.characters} == set(
            scenario.initial_actor_states
        )


def test_every_bundled_behavioral_character_gets_a_live_agent_runtime():
    for module_name, attribute in BUNDLED_SCENARIOS:
        scenario = getattr(importlib.import_module(module_name), attribute)
        session = create_session(
            scenario,
            random_seed="content-package-boundary",
            agent_runtime_factories=_bundled_runtime_factories(),
        )

        for character in scenario.characters:
            entity = session.entities[character.name]
            assert entity.get_component("AgentController") is not None
            assert session.runner.agent_registry.is_registered(entity)


def test_bundled_world_rules_do_not_contain_director_or_render_instructions():
    forbidden_rule_tokens = (
        "须描写",
        "GM 可",
        "快节奏",
        "优先让玩家",
        "Simulation 层",
        "Rendering 层",
        "INTRODUCE_CHARACTER",
    )
    forbidden_public_environment_tokens = ("[GM 参考]", "仅 GM 参考")

    for module_name, attribute in BUNDLED_SCENARIOS:
        scenario = getattr(importlib.import_module(module_name), attribute)
        assert all(
            token not in scenario.environment
            for token in forbidden_public_environment_tokens
        )
        assert all(
            token not in rule
            for rule in scenario.rules
            for token in forbidden_rule_tokens
        )


def test_bundled_sessions_do_not_share_runtime_registries_or_world_entities():
    first_module, first_attribute = BUNDLED_SCENARIOS[0]
    second_module, second_attribute = BUNDLED_SCENARIOS[1]
    first = create_session(
        getattr(importlib.import_module(first_module), first_attribute),
        random_seed="first-content",
        agent_runtime_factories=_bundled_runtime_factories(),
    )
    second = create_session(
        getattr(importlib.import_module(second_module), second_attribute),
        random_seed="second-content",
        agent_runtime_factories=_bundled_runtime_factories(),
    )

    assert first.runner.agent_registry is not second.runner.agent_registry
    assert first.entities is not second.entities
    assert set(first.entities).isdisjoint(
        set(second.entities).difference({"GameMaster"})
    )


def test_core_and_content_have_separate_distribution_manifests():
    core = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    content = tomllib.loads(
        (
            PROJECT_ROOT / "src" / "story_engine_content" / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )

    assert core["project"]["name"] == "story-engine"
    include = set(core["tool"]["setuptools"]["packages"]["find"]["include"])
    exclude = set(core["tool"]["setuptools"]["packages"]["find"]["exclude"])
    assert {"src.story_engine", "src.story_engine.*", "src.config"} <= include
    assert {"src.story_engine_content", "src.story_engine_content.*"} <= exclude

    assert content["project"]["name"] == "story-engine-content"
    assert content["project"]["dependencies"] == ["story-engine>=0.1.0"]
    packages = set(content["tool"]["setuptools"]["packages"])
    assert packages == {
        "src.story_engine_content",
        "src.story_engine_content.bundled",
        "src.story_engine_content.evaluation",
    }
    assert all(not name.startswith("src.story_engine.") for name in packages)


def test_staged_wheels_physically_preserve_core_content_boundary(tmp_path):
    script = PROJECT_ROOT / "scripts" / "check_distribution_boundary.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(PROJECT_ROOT),
            "--work-root",
            str(tmp_path / "wheel-boundary"),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["core_content_entry_count"] == 0
    assert payload["content_core_entry_count"] == 0
    assert payload["content_config_entry_count"] == 0


def test_bundled_catalog_is_lazy_and_loads_only_the_explicit_story():
    code = """
import sys
from src.story_engine_content.catalog import available_bundled_scenarios, load_bundled_scenario
prefix = 'src.story_engine_content.bundled.'
assert not any(name.startswith(prefix) for name in sys.modules)
assert available_bundled_scenarios() == ('cthulhu-arkham', 'false-heiress', 'thirteenth-floor')
scenario = load_bundled_scenario('false-heiress')
loaded = sorted(name for name in sys.modules if name.startswith(prefix))
assert loaded == ['src.story_engine_content.bundled.false_heiress']
assert scenario.name
print(scenario.name)
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "真假千金" in completed.stdout


def test_console_and_web_entrypoints_require_explicit_story_selection():
    console = importlib.import_module("main")
    web = importlib.import_module("web_main")

    console_args = console.parse_args(["--scenario", "thirteenth-floor"])
    web_args = web.parse_args([
        "--scenario", "cthulhu-arkham", "--port", "8123"
    ])

    assert console_args.scenario == "thirteenth-floor"
    assert web_args.scenario == "cthulhu-arkham"
    assert web_args.port == 8123
    external_console = console.parse_args([
        "--scenario-ref",
        "src.story_engine_content.evaluation.minimal_goal_growth:"
        "build_minimal_goal_growth_scenario",
    ])
    external_web = web.parse_args([
        "--scenario-ref",
        "src.story_engine_content.bundled.false_heiress:false_heiress_scenario",
    ])
    assert external_console.scenario is None
    assert external_console.scenario_ref.endswith(
        ":build_minimal_goal_growth_scenario"
    )
    assert external_web.scenario is None
    assert external_web.scenario_ref.endswith(":false_heiress_scenario")
    with pytest.raises(SystemExit):
        console.parse_args([])
    with pytest.raises(SystemExit):
        web.parse_args([])
    assert console.parse_args.__defaults__ == (None,)
    assert web.parse_args.__defaults__ == (None,)


def test_explicit_scenario_reference_loads_external_object_or_factory():
    from_object = load_scenario_reference(
        "src.story_engine_content.bundled.false_heiress:false_heiress_scenario"
    )
    from_factory = load_scenario_reference(
        "src.story_engine_content.evaluation.minimal_goal_growth:"
        "build_minimal_goal_growth_scenario"
    )

    assert isinstance(from_object, ScenarioConfig)
    assert isinstance(from_factory, ScenarioConfig)
    assert from_object.name != from_factory.name

    with pytest.raises(ValueError, match="module.path:attribute"):
        load_scenario_reference("missing-separator")
    with pytest.raises(TypeError, match="did not produce ScenarioConfig"):
        load_scenario_reference("builtins:list")
