import ast
from pathlib import Path

import src.story_engine.systems as systems
from src.story_engine.environment.runner import Runner


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = PROJECT_ROOT / "src" / "story_engine"


def test_public_system_api_only_exposes_authoritative_runner_phases():
    legacy = {"AgentActionSystem", "NarrativeSystem", "ObservationSystem"}

    assert legacy.isdisjoint(set(systems.__all__))
    assert all(not hasattr(systems, name) for name in legacy)
    assert [system.__class__.__name__ for system in Runner().systems] == [
        "InputSystem",
        "ActionSchedulingSystem",
        "SimulationSystem",
        "ClaimSystem",
        "GoalSystem",
        "ModifierSystem",
        "RelationshipSystem",
        "DriveSystem",
        "ClaimKnowledgeSystem",
        "RouteKnowledgeSystem",
        "NavigationSystem",
        "CognitionSystem",
        "SentimentSystem",
        "WorldEventSystem",
        "RenderingSystem",
        "MemorySystem",
    ]


def test_legacy_direct_narration_and_entity_spawn_modules_are_removed():
    removed = [
        ENGINE_ROOT / "systems" / "agent_action.py",
        ENGINE_ROOT / "systems" / "narrative.py",
        ENGINE_ROOT / "systems" / "observation.py",
        ENGINE_ROOT / "systems" / "contracts.py",
        ENGINE_ROOT / "systems" / "agreements.py",
        ENGINE_ROOT / "systems" / "obligations.py",
        ENGINE_ROOT / "environment" / "contracts.py",
        ENGINE_ROOT / "environment" / "escrows.py",
        ENGINE_ROOT / "environment" / "agreement_offers.py",
        ENGINE_ROOT / "components" / "narrative_control.py",
        ENGINE_ROOT / "components" / "persona.py",
        ENGINE_ROOT / "components" / "relationship_state.py",
    ]

    assert all(not path.exists() for path in removed)
    system_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ENGINE_ROOT / "systems").glob("*.py"))
    )
    assert "NarrativeControl" not in system_source
    assert 'get_component("Persona")' not in system_source
    assert "_spawn_character_if_needed" not in system_source
    assert "ContractSystem" not in system_source
    simulation_control_source = (
        ENGINE_ROOT / "components" / "simulation_control.py"
    ).read_text(encoding="utf-8")
    assert "_build_forced_conflict_actions" not in simulation_control_source
    assert "_build_forced_storylet_actions" not in simulation_control_source
    assert "_enforce_storylets" not in simulation_control_source
    assert "_enforce_conflict" not in simulation_control_source
    assert "require_visible_conflict" not in simulation_control_source
    assert "entities[new_name]" not in system_source


def test_character_lifecycle_has_no_direct_spawn_compatibility_bypass():
    from src.story_engine.environment.character_lifecycle import CharacterLifecycle

    assert not hasattr(CharacterLifecycle, "spawn")
    assert all(
        hasattr(CharacterLifecycle, phase)
        for phase in ("prepare", "stage", "finalize")
    )


def test_live_agent_registry_requires_ecs_agent_controller_declaration():
    registry_source = (
        ENGINE_ROOT / "agents" / "registry.py"
    ).read_text(encoding="utf-8")
    input_source = (
        ENGINE_ROOT / "systems" / "input.py"
    ).read_text(encoding="utf-8")

    assert 'get_component("AgentController") is None' in registry_source
    assert "Cannot register entity without AgentController" in registry_source
    assert "entry is also sufficient at compatibility/test boundaries" not in input_source


def test_semantic_spawn_requires_host_character_entry_authority():
    simulation_source = (ENGINE_ROOT / "systems" / "simulation.py").read_text(
        encoding="utf-8"
    )
    input_source = (ENGINE_ROOT / "systems" / "input.py").read_text(
        encoding="utf-8"
    )

    assert "CharacterEntryAuthority" in simulation_source
    assert "character_entry_rejections" in simulation_source
    assert "character_spawn_authorizations" in input_source
    assert "consumed_character_entry_authorizations" in (
        ENGINE_ROOT / "environment" / "character_lifecycle.py"
    ).read_text(encoding="utf-8")


def test_timeline_has_no_actor_staging_or_teleportation_path():
    timeline_source = (
        ENGINE_ROOT / "narrative" / "timeline.py"
    ).read_text(encoding="utf-8")
    scenario_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ENGINE_ROOT / "scenarios").glob("*.py")
    )

    assert "stage_actors" not in timeline_source
    assert "apply_commitment_staging" not in timeline_source
    assert "stage_actors" not in scenario_source
    assert "private_schedule" in timeline_source


def test_live_runtime_has_no_gm_owned_contract_state_fallback():
    runtime_files = [
        ENGINE_ROOT / "systems" / "input.py",
        ENGINE_ROOT / "systems" / "simulation.py",
        ENGINE_ROOT / "agents" / "scheduler.py",
        ENGINE_ROOT / "agents" / "types.py",
    ]
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_files
    )

    assert 'get_component("ContractState")' not in runtime_source
    assert 'context["contract_state"]' not in runtime_source
    assert 'context.get("contract_state")' not in runtime_source
    assert "private_contracts" not in runtime_source


def test_agent_and_semantic_gm_relationship_packets_hide_exact_track_values():
    runtime_files = [
        ENGINE_ROOT / "social" / "relation_registry.py",
        ENGINE_ROOT / "social" / "dynamics.py",
        ENGINE_ROOT / "components" / "simulation_control.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert '"toward_viewer": self.get_metrics' not in source
    assert '"viewer_toward_actor": self.get_metrics' not in source
    assert 'item.get("toward_viewer", {})' not in source


def test_all_semantic_resolvers_cross_the_host_authority_filter():
    simulation_source = (ENGINE_ROOT / "systems" / "simulation.py").read_text(
        encoding="utf-8"
    )
    control_source = (
        ENGINE_ROOT / "components" / "simulation_control.py"
    ).read_text(encoding="utf-8")

    assert "SemanticAuthorityFilter" in simulation_source
    assert "simulation.simulate(input_payload)" in simulation_source
    assert "semantic_authority_rejections" in simulation_source
    assert 'result["plot_updates"] = []' in control_source
    assert 'result["relationship_updates"] = []' in control_source


def test_semantic_resolver_receives_no_director_or_storylet_packet():
    from src.story_engine.components.host_rule_simulation import (
        HostRuleSimulationControl,
    )
    from src.story_engine.scenarios.config import (
        CharacterConfig,
        ConflictTemplateConfig,
        ScenarioConfig,
        StoryletConfig,
    )
    from src.story_engine.session import create_session

    captured = {}

    class CapturingSimulationControl(HostRuleSimulationControl):
        def simulate(self, input_payload):
            captured["payload"] = input_payload
            return super().simulate(input_payload)

    scenario = ScenarioConfig(
        name="导演隔离测试",
        default_agent_runtime="llm",
        description="测试",
        environment="房间",
        initial_state="甲在房间里。",
        initial_world_objects={"房间": {}},
        initial_actor_states={
            "甲": {
                "location": "房间",
                "dramatic_motive": "制造冲突",
                "bias": "甲",
            }
        },
        characters=[
            CharacterConfig(
                name="甲",
                agent_runtime="llm",
                role="测试角色",
                personality="平静",
                goals=["留在房间"],
                is_player=True,
            )
        ],
        storylets=[
            StoryletConfig(storylet_id="forced_beat", intent="制造一次争吵")
        ],
        conflict_templates=[
            ConflictTemplateConfig(
                template_id="forced_conflict",
                instruction="把本轮行动解释为冲突",
            )
        ],
    )
    from src.story_engine.agents.types import AgentDecision

    class _WaitingRuntime:
        def decide(self, _entity, _perception):
            return AgentDecision(action="安静等待。")

    session = create_session(
        scenario,
        random_seed=3,
        agent_runtime_factories={"llm": lambda entity, cfg: _WaitingRuntime()},
    )
    gm = session.entities["GameMaster"]
    gm.add_component(CapturingSimulationControl(scenario=scenario))

    context = session.run_step(overrides={"甲": "安静等待。"})
    payload = captured["payload"]

    assert {
        "active_storylets",
        "storylet_pressure",
        "director_packet",
        "plot_snapshot",
        "situations",
        "reaction_context",
        "intent_focus",
        "motive_pressure",
        "conflict",
    }.isdisjoint(payload)
    assert payload["intents"][0]["actor"] == "甲"
    assert "legality" in payload
    assert "narrative_pressure" in payload
    assert "directive" in payload["narrative_pressure"]
    assert context["storylet_pressure"]["salient_storylet_id"] == "forced_beat"
    assert "director_packet" in context
    assert "conflict" in context


def test_attention_priority_catalog_has_one_host_owned_implementation():
    attention_source = (ENGINE_ROOT / "attention.py").read_text(encoding="utf-8")
    world_event_source = (ENGINE_ROOT / "systems" / "world_events.py").read_text(
        encoding="utf-8"
    )
    cognition_source = (ENGINE_ROOT / "systems" / "cognition.py").read_text(
        encoding="utf-8"
    )

    assert "class HostAttentionPolicy" in attention_source
    assert "def event_priority" in attention_source
    assert "def response_priority" in attention_source
    assert "def _attention_priority" not in world_event_source
    assert "def _event_priority" not in cognition_source
    assert "def _response_priority" not in cognition_source
    assert "HostAttentionPolicy.event_priority" in world_event_source
    assert "HostAttentionPolicy.event_priority" in cognition_source


def test_host_world_edits_have_no_direct_scene_mutation_bypass():
    runner_source = (ENGINE_ROOT / "environment" / "runner.py").read_text(
        encoding="utf-8"
    )
    edit_source = (ENGINE_ROOT / "environment" / "world_edits.py").read_text(
        encoding="utf-8"
    )

    assert "HostMutationTransaction" in runner_source
    assert "def _apply_world_edits" not in runner_source
    assert "scene.update_object_state(obj_name, state)" not in runner_source
    assert "PROTECTED_FIELDS" in edit_source
    assert '"host_object_state_changes"' in runner_source


def test_prestep_host_mutations_share_one_fail_closed_coordinator():
    runner_source = (ENGINE_ROOT / "environment" / "runner.py").read_text(
        encoding="utf-8"
    )
    coordinator_source = (
        ENGINE_ROOT / "environment" / "host_mutations.py"
    ).read_text(encoding="utf-8")

    assert "HostMutationTransaction" in runner_source
    assert "HostTopologyTransaction()" not in runner_source
    assert "HostWorldEditTransaction()" not in runner_source
    assert "HostTopologyTransaction()" in coordinator_source
    assert "HostWorldEditTransaction()" in coordinator_source
    assert 'context["step_aborted"] = True' in runner_source


def test_runner_has_authoritative_checkpoint_and_world_event_commit_barrier():
    runner_source = (ENGINE_ROOT / "environment" / "runner.py").read_text(
        encoding="utf-8"
    )
    checkpoint_source = (
        ENGINE_ROOT / "environment" / "step_checkpoint.py"
    ).read_text(encoding="utf-8")

    assert "RunnerStepCheckpoint.capture" in runner_source
    assert "step_checkpoint.restore(self)" in runner_source
    assert 'system_name == "WorldEventSystem"' in runner_source
    assert "dispatcher.begin_transaction()" in runner_source
    assert "dispatcher.rollback_transaction()" in runner_source
    assert "dispatcher.commit_transaction()" in runner_source
    assert "agent_registry.restore_runtimes" in checkpoint_source
    assert "relation_registry.restore_bindings" in checkpoint_source
    assert "action_queue.restore" in checkpoint_source


def test_manual_and_agent_decisions_share_bounded_perception_acknowledgement():
    input_source = (ENGINE_ROOT / "systems" / "input.py").read_text(
        encoding="utf-8"
    )

    assert "def build_agent_perception" in input_source
    assert "_acknowledge_perception_attention(entity, perception)" in input_source
    assert 'context.setdefault("manual_perceptions", {})' in input_source
    assert "cognition.acknowledge_world_events()" not in input_source
    assert "cognition.acknowledge_event_responses()" not in input_source


def test_delivery_retry_never_reenters_authoritative_phase_chain():
    runner_source = (ENGINE_ROOT / "environment" / "runner.py").read_text(
        encoding="utf-8"
    )
    memory_source = (ENGINE_ROOT / "systems" / "memory.py").read_text(
        encoding="utf-8"
    )
    server_source = (ENGINE_ROOT / "web" / "server.py").read_text(
        encoding="utf-8"
    )
    app_source = (ENGINE_ROOT / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "def retry_delivery" in runner_source
    assert "delivery receipt cannot rerun authoritative phases" in runner_source
    assert '"pending_delivery_retry"' in runner_source
    assert "memory_id=memory_id" in memory_source
    assert '"/api/retry-delivery"' in server_source
    assert 'request("/api/retry-delivery"' in app_source


def test_engine_modules_do_not_name_bundled_story_people_or_places():
    bundled_story_tokens = {
        "阿卡姆港",
        "海员之眠",
        "真假千金",
        "十三楼",
        "Whispers in Arkham",
        "Sherlock",
        "Moriarty",
        "white_lotus",
        "沈昭宁",
        "林见微",
    }
    sources = []
    for path in ENGINE_ROOT.rglob("*.py"):
        sources.append(path.read_text(encoding="utf-8"))
    engine_source = "\n".join(sources)

    assert all(token not in engine_source for token in bundled_story_tokens)


def test_core_simulation_policy_does_not_force_a_story_pacing_style():
    source = (
        ENGINE_ROOT / "components" / "simulation_control.py"
    ).read_text(encoding="utf-8")

    assert all(
        pacing not in source
        for pacing in ("快节奏", "慢节奏", "慢热", "爽文")
    )
    assert "有效推进" in source
    assert "self.scenario.rules" in source


def test_core_narrator_has_neutral_defaults_and_content_owned_style():
    source = (
        ENGINE_ROOT / "components" / "narrative_renderer.py"
    ).read_text(encoding="utf-8")

    assert "self.scenario.narration" in source
    assert "节奏要快" not in source
    assert "锋利感" not in source
    assert "prefer_fast_scene_change" not in source


def test_player_narration_never_becomes_character_observation_or_memory():
    rendering_source = (
        ENGINE_ROOT / "systems" / "rendering.py"
    ).read_text(encoding="utf-8")
    memory_source = (
        ENGINE_ROOT / "systems" / "memory.py"
    ).read_text(encoding="utf-8")

    assert 'get_component("Observation")' not in rendering_source
    assert "add_observation" not in rendering_source
    assert "rendered_text" not in memory_source
    assert 'get_component("Cognition")' in memory_source
    assert "Personally Observed Outcomes" in memory_source


def test_engine_scenario_package_contains_schema_only():
    package_source = (ENGINE_ROOT / "scenarios" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert {
        path.name
        for path in (ENGINE_ROOT / "scenarios").glob("*.py")
    } == {"__init__.py", "config.py"}
    assert "false_heiress" not in package_source
    assert "cthulhu_arkham" not in package_source
    assert "thirteenth_floor" not in package_source


def test_engine_dependency_graph_never_imports_optional_story_content():
    engine_source = "\n".join(
        path.read_text(encoding="utf-8") for path in ENGINE_ROOT.rglob("*.py")
    )

    assert "story_engine_content" not in engine_source


def test_default_host_runtime_never_imports_vendor_hermes():
    host_roots = [ENGINE_ROOT, PROJECT_ROOT / "scripts" / "eval"]
    host_source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in host_roots
        for path in root.rglob("*.py")
    )
    entrypoint = (
        PROJECT_ROOT / "docker" / "hermes-story" / "entrypoint.py"
    ).read_text(encoding="utf-8")

    assert "from run_agent import AIAgent" not in host_source
    assert "import hermes_agent" not in host_source
    assert "from run_agent import AIAgent" in entrypoint


def test_hermes_story_image_uses_pep668_compatible_vendor_install():
    dockerfile = (
        PROJECT_ROOT / "docker" / "hermes-story" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "python3 -m venv /opt/story-venv" in dockerfile
    assert "/opt/story-venv/bin/pip install" in dockerfile
    assert 'ENTRYPOINT ["/opt/story-venv/bin/python"' in dockerfile
    assert "RUN pip install" not in dockerfile


def test_engine_core_defines_scenario_schema_but_never_instantiates_a_story():
    forbidden_calls = {
        "ScenarioConfig",
        "CharacterConfig",
        "InitialRelationshipConfig",
    }
    violations = []
    for path in ENGINE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else ""
            )
            if name in forbidden_calls:
                violations.append(f"{path.relative_to(ENGINE_ROOT)}:{node.lineno}:{name}")

    assert violations == []


def test_engine_package_contains_no_evaluation_story_content_namespace():
    legacy_root = ENGINE_ROOT / "evaluation" / "content"
    assert not legacy_root.exists() or not list(legacy_root.glob("*.py"))
    engine_source = "\n".join(
        path.read_text(encoding="utf-8") for path in ENGINE_ROOT.rglob("*.py")
    )
    assert "story_engine.evaluation.content" not in engine_source


def test_bundled_content_package_selects_no_default_story():
    content_root = PROJECT_ROOT / "src" / "story_engine_content"
    root_source = (content_root / "__init__.py").read_text(encoding="utf-8")
    bundled_source = (content_root / "bundled" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert "false_heiress" not in root_source + bundled_source
    assert "cthulhu_arkham" not in root_source + bundled_source
    assert "thirteenth_floor" not in root_source + bundled_source
    assert {
        path.name for path in (content_root / "bundled").glob("*.py")
    } == {
        "__init__.py",
        "false_heiress.py",
        "cthulhu_arkham.py",
        "thirteenth_floor.py",
    }
