import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.story_engine.agents import (
    AgentPerception,
    HermesContainerConfig,
    HermesContainerConversation,
    HermesCharacterAgent,
    HermesInvocationBudget,
    HermesInvocationBudgetExceeded,
    make_hermes_container_runtime_factory,
)
from src.story_engine.prefabs.templates import create_agent


def _marked(payload, *, agent_id="x"):
    envelope = {
        "protocol_version": 1,
        "agent_id": agent_id,
        **payload,
    }
    return (
        "vendor debug output\n"
        "===STORY_AGENT_JSON_BEGIN===\n"
        + json.dumps(envelope, ensure_ascii=False)
        + "\n===STORY_AGENT_JSON_END===\n"
    )


def _entrypoint_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "docker"
        / "hermes-story"
        / "entrypoint.py"
    )
    spec = importlib.util.spec_from_file_location("hermes_story_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hermes_system_prompt_assigns_an_agent_to_operate_the_character():
    prompt = _entrypoint_module().SUBJECT_SYSTEM_PROMPT
    assert "assigned to one character" in prompt
    assert "Choose the next intentional action" in prompt
    assert "You are one character inside" not in prompt
    assert "You are not the fictional person" in prompt


def test_container_factory_always_requests_memory_toolset():
    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(allowed_toolsets=("memory", "file")),
        command_runner=lambda *args, **kwargs: None,
    )
    entity = create_agent(
        name="测试者",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
        agent_config={"enabled_toolsets": ["file"]},
    )
    runtime = factory(entity, entity.get_component("AgentController").config)
    conversation = runtime._factory(entity, {})
    assert conversation.requested_toolsets == ("memory", "file")
    assert conversation.enabled_toolsets == ("memory", "file")


def test_container_factory_memory_request_respects_explicit_allowlist():
    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(allowed_toolsets=("file",)),
        command_runner=lambda *args, **kwargs: None,
    )
    entity = create_agent(
        name="测试者",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
        agent_config={"enabled_toolsets": ["file"]},
    )
    runtime = factory(entity, entity.get_component("AgentController").config)
    conversation = runtime._factory(entity, {})
    assert conversation.requested_toolsets == ("memory", "file")
    assert conversation.enabled_toolsets == ("file",)


def test_container_conversation_filters_requested_toolsets_by_allowlist():
    conversation = HermesContainerConversation(
        "x",
        HermesContainerConfig(allowed_toolsets=("file",)),
        requested_toolsets=("memory", "file"),
    )
    assert conversation.enabled_toolsets == ("file",)


def test_container_conversation_builds_shell_free_command_and_parses_markers():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {"content": '{"thought":"谨慎","action":"检查门锁"}'},
                agent_id="actor-1",
            ),
            stderr="",
        )

    conversation = HermesContainerConversation(
        agent_id="actor-1",
        host_config=HermesContainerConfig(
            image="registry.local/hermes-story:v1",
            allowed_toolsets=("file",),
            environment_keys=("OPENAI_API_KEY",),
        ),
        requested_toolsets=("file", "terminal"),
        command_runner=runner,
    )
    result = conversation.run_conversation("决定下一步")

    command, kwargs = calls[0]
    assert command == [
        "docker", "run", "--rm", "-i", "--network", "bridge",
        "-e", "OPENAI_API_KEY", "registry.local/hermes-story:v1",
    ]
    assert kwargs["check"] is False
    request = json.loads(kwargs["input"])
    assert request["agent_id"] == "actor-1"
    assert request["enabled_toolsets"] == ["file"]
    assert result["content"].endswith('"检查门锁"}')


def test_container_invocation_budget_counts_attempts_and_fails_closed():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=_marked({"content": "{}"}),
            stderr="",
        )

    budget = HermesInvocationBudget(1)
    conversation = HermesContainerConversation(
        "x",
        HermesContainerConfig(invocation_budget=budget),
        command_runner=runner,
    )

    conversation.run_conversation("first")
    assert budget.snapshot()["exhausted"] is True
    with pytest.raises(HermesInvocationBudgetExceeded, match="1/1"):
        conversation.run_conversation("second")

    assert len(calls) == 1
    assert budget.snapshot() == {
        "configured": 1,
        "consumed": 1,
        "remaining": 0,
        "exhausted": True,
    }


def test_failed_container_invocation_still_consumes_budget():
    def runner(command, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="failed")

    budget = HermesInvocationBudget(1)
    conversation = HermesContainerConversation(
        "x",
        HermesContainerConfig(invocation_budget=budget),
        command_runner=runner,
    )

    with pytest.raises(RuntimeError, match="failed"):
        conversation.run_conversation("prompt")

    assert budget.snapshot()["consumed"] == 1


def test_container_command_never_contains_environment_secret_values():
    config = HermesContainerConfig(
        environment_keys=("OPENAI_API_KEY", "bad-key", "ALSO_GOOD_2"),
    )
    command = HermesContainerConversation("x", config).build_command()

    assert "OPENAI_API_KEY" in command
    assert "ALSO_GOOD_2" in command
    assert "bad-key" not in command
    assert all("=" not in item for item in command if item.startswith("OPENAI"))


def test_container_policy_rejects_unsafe_transport_configuration():
    with pytest.raises(ValueError, match="network mode"):
        HermesContainerConversation(
            "x", HermesContainerConfig(network_mode="--privileged")
        )
    with pytest.raises(ValueError, match="positive"):
        HermesContainerConversation(
            "x", HermesContainerConfig(timeout_seconds=0)
        )
    with pytest.raises(ValueError, match="agent_id"):
        HermesContainerConversation("", HermesContainerConfig())


def test_container_entrypoint_emits_versioned_agent_bound_envelope(
    monkeypatch, capsys
):
    module = _entrypoint_module()
    request = {
        "protocol_version": 1,
        "agent_id": "actor-7",
        "prompt": "决定下一步",
        "enabled_toolsets": ["file", "file"],
    }
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(module, "_construct_agent", lambda toolsets: toolsets)
    monkeypatch.setattr(
        module,
        "_invoke",
        lambda agent, prompt: json.dumps(
            {"action": {"kind": "wait", "detail": prompt}},
            ensure_ascii=False,
        ),
    )

    module.main()

    output = capsys.readouterr().out
    raw = output.split(module.BEGIN_MARKER, 1)[1].split(module.END_MARKER, 1)[0]
    envelope = json.loads(raw)
    assert envelope["protocol_version"] == 1
    assert envelope["agent_id"] == "actor-7"
    assert json.loads(envelope["content"])["action"]["kind"] == "wait"


def test_container_entrypoint_rejects_malformed_toolset_list(monkeypatch):
    module = _entrypoint_module()
    request = {
        "protocol_version": 1,
        "agent_id": "actor-7",
        "prompt": "决定下一步",
        "enabled_toolsets": ["file", {"not": "a string"}],
    }
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(request)))

    with pytest.raises(ValueError, match="string list"):
        module._read_request()


def test_container_entrypoint_subject_server_reuses_one_agent(monkeypatch, capsys):
    module = _entrypoint_module()
    created = []
    invoked = []

    class FakeAgent:
        pass

    def construct(toolsets):
        created.append(tuple(toolsets))
        return FakeAgent()

    def request(step):
        return json.dumps({
            "protocol_version": 1,
            "agent_id": "actor-7",
            "enabled_toolsets": ["memory"],
            "subject_packet": {
                "subject_protocol_version": 1,
                "subject_id": "actor-7",
                "wake": {"step": step},
            },
        }, ensure_ascii=False)

    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request(1) + "\n" + request(2) + "\n"))
    monkeypatch.setattr(module, "_construct_agent", construct)
    monkeypatch.setattr(
        module,
        "_invoke",
        lambda agent, prompt: invoked.append((agent, json.loads(prompt))) or '{"action":{"kind":"wait","detail":"等待"}}',
    )

    module.serve()

    output = capsys.readouterr().out
    assert created == [("memory",)]
    assert len(invoked) == 2
    assert invoked[0][0] is invoked[1][0]
    assert [item[1]["wake"]["step"] for item in invoked] == [1, 2]
    assert output.count(module.BEGIN_MARKER) == 2
    assert output.count(module.END_MARKER) == 2


def test_container_builds_persistent_subject_server_command():
    conversation = HermesContainerConversation("actor-1", HermesContainerConfig())

    assert conversation.build_command(subject_server=True)[-2:] == [
        "hermes-story:latest",
        "--subject-server",
    ]


def test_container_entrypoint_maps_host_model_environment_to_agent(
    monkeypatch, tmp_path
):
    module = _entrypoint_module()
    captured = {}

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module, "VENDOR_ROOT", tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "run_agent",
        SimpleNamespace(AIAgent=FakeAIAgent),
    )
    monkeypatch.setenv("HERMES_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("HERMES_MODEL", "story-model")
    monkeypatch.setenv("IKUN_API_KEY", "secret-value")
    monkeypatch.delenv("HERMES_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    module._construct_agent(["file"])

    assert captured["provider"] == "custom"
    assert captured["base_url"] == "https://model.example/v1"
    assert captured["model"] == "story-model"
    assert captured["api_key"] == "secret-value"
    assert captured["enabled_toolsets"] == ["file"]
    assert captured["quiet_mode"] is True


def test_container_toolsets_are_allowlisted_and_deduplicated():
    conversation = HermesContainerConversation(
        "x",
        HermesContainerConfig(allowed_toolsets=("file", "terminal")),
        requested_toolsets=("file", "file", "unknown", "terminal"),
    )

    assert conversation.enabled_toolsets == ("file", "terminal")


def test_container_can_bind_project_shell_files_read_only(tmp_path):
    entrypoint = tmp_path / "entrypoint.py"
    config = tmp_path / "config.yaml"
    entrypoint.write_text("print('shell')\n", encoding="utf-8")
    config.write_text("terminal:\n  backend: local\n", encoding="utf-8")
    conversation = HermesContainerConversation(
        "x",
        HermesContainerConfig(
            entrypoint_path=str(entrypoint),
            config_path=str(config),
        ),
    )

    command = conversation.build_command()

    assert command[-1] == "hermes-story:latest"
    mounts = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--mount"
    ]
    assert mounts == [
        f"type=bind,src={entrypoint.resolve()},dst=/opt/story-entrypoint.py,readonly",
        f"type=bind,src={config.resolve()},dst=/root/.hermes/config.yaml,readonly",
    ]


def test_container_rejects_missing_or_unsafe_shell_mount(tmp_path):
    with pytest.raises(ValueError, match="not a file"):
        HermesContainerConversation(
            "x",
            HermesContainerConfig(entrypoint_path=str(tmp_path / "missing.py")),
        )
    bad = tmp_path / "bad,name.py"
    bad.write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsafe"):
        HermesContainerConversation(
            "x", HermesContainerConfig(entrypoint_path=str(bad))
        )


def test_container_protocol_rejects_missing_or_invalid_marker_payload():
    conversation = HermesContainerConversation("x", HermesContainerConfig())
    with pytest.raises(ValueError, match="exactly one"):
        conversation.parse_output("ordinary stdout")
    with pytest.raises(ValueError, match="valid JSON"):
        conversation.parse_output(
            "===STORY_AGENT_JSON_BEGIN===\nnot-json\n===STORY_AGENT_JSON_END==="
        )
    with pytest.raises(ValueError, match="content"):
        conversation.parse_output(_marked({"other": "value"}))
    with pytest.raises(ValueError, match="protocol version"):
        conversation.parse_output(
            _marked({"protocol_version": 2, "content": "{}"})
        )
    with pytest.raises(ValueError, match="agent_id"):
        conversation.parse_output(_marked({"content": "{}"}, agent_id="other"))
    with pytest.raises(ValueError, match="exactly one"):
        conversation.parse_output(
            _marked({"content": "{}"}) + _marked({"content": "{}"})
        )
    with pytest.raises(ValueError, match="non-empty"):
        conversation.parse_output(_marked({"content": "  "}))


def test_nonzero_container_exit_is_reported_without_stdout_guessing():
    def runner(command, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="vendor failed")

    conversation = HermesContainerConversation(
        "x", HermesContainerConfig(), command_runner=runner
    )
    with pytest.raises(RuntimeError, match="vendor failed"):
        conversation.run_conversation("prompt")


def test_runner_factory_connects_container_protocol_to_character_agent():
    requests = []

    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        requests.append(request)
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {
                    "content": json.dumps(
                        {
                            "thought": "先确认情况",
                            "candidates": [
                                {
                                    "option_id": "investigate",
                                    "utility": 0.4,
                                    "motive_lens": "确认安全",
                                    "intent_signature": {
                                        "strategy": "主动观察",
                                        "stakes": ["safety", "knowledge"],
                                    },
                                    "action": {
                                        "kind": "observe",
                                        "detail": "走到窗边查看街道",
                                        "target": "街道",
                                    },
                                },
                                {
                                    "option_id": "wait",
                                    "utility": -0.2,
                                    "motive_lens": "规避危险",
                                    "intent_signature": {
                                        "strategy": "延后暴露",
                                        "stakes": ["safety"],
                                    },
                                    "action": {
                                        "kind": "wait",
                                        "detail": "留在原地继续听外面的动静",
                                    },
                                },
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
                agent_id=request["agent_id"],
            ),
            stderr="",
        )

    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(allowed_toolsets=("file",)),
        command_runner=runner,
    )
    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=["确认街道是否安全"],
        agent_runtime="hermes",
        agent_config={
            "enabled_toolsets": ["file", "terminal"],
            "character_seed": "observer-seed",
        },
    )
    runtime = factory(entity, entity.get_component("AgentController").config)
    decision = runtime.decide(
        entity,
        AgentPerception(
            actor_name="观察者",
            step=5,
            activation_scope="background",
            world_view={
                "location": "旅馆",
                "visible_objects": ["行囊", "窗户"],
                "visible_world": {
                    "行囊": {"owner": "观察者", "portable": True},
                    "窗户": {"location": "旅馆"},
                },
            },
            self_state={"location": "旅馆"},
        ),
    )

    assert decision.action in {
        "走到窗边查看街道",
        "留在原地继续听外面的动静",
    }
    assert decision.candidates == ()
    assert decision.metadata == {"subject_runtime": True}
    assert requests[0]["enabled_toolsets"] == ["file"]
    subject_packet = requests[0]["subject_packet"]
    assert subject_packet["wake"]["step"] == 5
    assert subject_packet["wake"]["activation_scope"] == "background"
    assert subject_packet["identity_bootstrap"]["name"] == "观察者"
    assert "行囊" in subject_packet["wake"]["visible_world"]["visible_world"]


def test_hermes_runtime_rejects_non_json_instead_of_inventing_an_action():
    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {"content": "I cannot decide right now."},
                agent_id=request["agent_id"],
            ),
            stderr="",
        )

    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(), command_runner=runner
    )
    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=["确认情况"],
        agent_runtime="hermes",
    )
    runtime = factory(entity, entity.get_component("AgentController").config)

    with pytest.raises(ValueError, match="valid decision JSON"):
        runtime.decide(entity, AgentPerception(actor_name="观察者", step=1))


def test_hermes_runtime_rejects_json_without_an_executable_action():
    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {"content": '{"thought":"还没有决定"}'},
                agent_id=request["agent_id"],
            ),
            stderr="",
        )

    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(), command_runner=runner
    )
    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=["确认情况"],
        agent_runtime="hermes",
    )
    runtime = factory(entity, entity.get_component("AgentController").config)

    with pytest.raises(
        ValueError,
        match="structured agent action must be an object with kind",
    ):
        runtime.decide(entity, AgentPerception(actor_name="观察者", step=1))


def test_hermes_runtime_allows_a_direct_subject_owned_action():
    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {
                    "content": json.dumps(
                        {"action": {"kind": "wait", "detail": "等待。"}},
                        ensure_ascii=False,
                    )
                },
                agent_id=request["agent_id"],
            ),
            stderr="",
        )

    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(), command_runner=runner
    )
    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=["确认情况"],
        agent_runtime="hermes",
    )
    runtime = factory(entity, entity.get_component("AgentController").config)

    decision = runtime.decide(
        entity, AgentPerception(actor_name="观察者", step=1)
    )

    assert decision.action_spec.kind == "wait"
    assert decision.candidates == ()


def test_hermes_runtime_rejects_paraphrases_as_fake_choice_diversity():
    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                {
                    "content": json.dumps(
                        {
                            "candidates": [
                                {
                                    "option_id": "first",
                                    "utility": 0.0,
                                    "motive_lens": "调查",
                                    "intent_signature": {
                                        "strategy": "检查划痕",
                                        "stakes": ["knowledge"],
                                    },
                                    "action": {
                                        "kind": "observe",
                                        "detail": "查看门上的划痕。",
                                        "target": "木门",
                                    },
                                },
                                {
                                    "option_id": "paraphrase",
                                    "utility": 0.0,
                                    "motive_lens": "调查",
                                    "intent_signature": {
                                        "strategy": "检查划痕",
                                        "stakes": ["knowledge"],
                                    },
                                    "action": {
                                        "kind": "observe",
                                        "detail": "更仔细地观察木门上的划痕。",
                                        "target": "木门",
                                    },
                                },
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
                agent_id=request["agent_id"],
            ),
            stderr="",
        )

    factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(), command_runner=runner
    )
    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=["确认情况"],
        agent_runtime="hermes",
    )
    runtime = factory(entity, entity.get_component("AgentController").config)

    with pytest.raises(ValueError, match="at least two motive lenses"):
        runtime.decide(entity, AgentPerception(actor_name="观察者", step=1))


def test_hermes_runtime_rejects_non_protocol_result_aliases():
    class LegacyConversation:
        def __init__(self, agent_id):
            self.agent_id = agent_id

        def run_conversation(self, prompt):
            del prompt
            return {
                "protocol_version": 1,
                "agent_id": self.agent_id,
                "final_response": json.dumps(
                    {"action": {"kind": "wait", "detail": "等待。"}},
                    ensure_ascii=False,
                )
            }

    entity = create_agent(
        name="观察者",
        role="旅客",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    runtime = HermesCharacterAgent(
        conversation_factory=lambda actor, _config: LegacyConversation(actor.id)
    )

    with pytest.raises(ValueError, match="protocol content"):
        runtime.decide(entity, AgentPerception(actor_name="观察者", step=1))
