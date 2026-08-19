from copy import deepcopy

from src.story_engine.agents import AgentDecision
from src.story_engine.core.entity import Entity
from src.story_engine.evaluation import EpisodeRunner
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session
from src.story_engine.session import public_step_status
from src.story_engine.systems.system import System


class WaitRuntime:
    def __init__(self):
        self.calls = 0

    def decide(self, entity, perception):
        self.calls += 1
        return AgentDecision(
            action="等待并观察周围。",
            thought="先保持耐心。",
            metadata={"focus": "观察当前局面"},
        )


def _session():
    scenario = ScenarioConfig(
        name="步骤回滚测试",
        default_agent_runtime="llm",
        description="验证系统异常不会留下半状态",
        environment="一间安静的大厅。",
        initial_state="甲在大厅中。",
        initial_world_objects={"大厅": {"lighting": "暗"}},
        initial_actor_states={"甲": {"location": "大厅"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="居民",
                personality="谨慎",
                goals=[],
                is_player=True,
                agent_runtime="test",
            )
        ],
    )
    runtime = WaitRuntime()
    session = create_session(
        scenario,
        agent_runtime_factories={"test": lambda entity, config: runtime},
        random_seed="step-rollback",
    )
    return session, runtime


class FaultingAuthoritativeSystem(System):
    def update(self, entities, context):
        scene = entities["GameMaster"].get_component("SceneState")
        scene.update_object_state("大厅", {"lighting": "错误的半状态"})
        scene.update_actor_state("临时人", {"location": "大厅"})
        temporary = create_agent(
            "临时人",
            "不应存在的角色",
            "短暂",
            [],
            agent_runtime="test",
        )
        entities[temporary.name] = temporary
        context["register_agent"](temporary)
        context["relation_registry"].ensure_context(
            ["甲", "临时人"],
            world_entities=entities,
            created_step=context["clock"].current_step,
        )
        raise RuntimeError("injected authoritative failure")


class FaultingDeliverySystem(System):
    def update(self, entities, context):
        entities["GameMaster"].get_component("SceneState").update_object_state(
            "大厅", {"delivery_half_state": True}
        )
        context["rendered_text"] = "不应保留的半段渲染"
        raise RuntimeError("injected renderer failure")


class FailOnceRenderingSystem(System):
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def update(self, entities, context):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient rendering failure")
        return self.delegate.update(entities, context)


def test_public_step_status_has_four_bounded_product_states():
    assert public_step_status({"step_aborted": True})["status"] == "aborted"
    assert public_step_status({"authoritative_step_failed": True})[
        "status"
    ] == "rolled_back"
    delivery = public_step_status(
        {
            "step_failed": True,
            "step_committed": True,
            "phase_errors": [
                {
                    "phase": "RenderingSystem",
                    "error_type": "RuntimeError",
                    "message": "secret details must stay private",
                }
            ],
        }
    )
    assert delivery == {
        "status": "delivery_failed",
        "committed": True,
        "failure_phase": "RenderingSystem",
        "failure_type": "RuntimeError",
    }
    assert public_step_status({"step_committed": True})["status"] == "committed"
    assert "secret details" not in str(delivery)


def test_episode_stops_after_first_authoritative_failure_without_recalling_agent():
    session, runtime = _session()
    session.runner.systems.insert(1, FaultingAuthoritativeSystem())

    report = EpisodeRunner().run(session, steps=5)

    assert len(report.steps) == 1
    assert report.termination_reason == "authoritative_failure"
    assert report.authoritative is False
    assert report.violations == ("authoritative_step_failed",)
    assert runtime.calls == 1
    assert session.simulation_time == 0


def test_authoritative_phase_exception_restores_entire_step_and_buffers_events():
    session, runtime = _session()
    runner = session.runner
    original_systems = list(runner.systems)
    runner.systems[2] = FaultingAuthoritativeSystem()
    scene = session.entities["GameMaster"].get_component("SceneState")
    cognition = session.entities["甲"].get_component("Cognition")
    controller = session.entities["甲"].get_component("AgentController")
    scene_identity = id(scene)
    cognition_identity = id(cognition)
    before_scene = deepcopy(scene.model_dump())
    before_cognition = deepcopy(cognition.model_dump())
    before_entities = set(session.entities)
    before_queue = runner.action_queue.snapshot()
    delivered = []
    runner.dispatcher.subscribe(delivered.append)

    failed = session.run_step(
        world_edits=[("大厅", {"lighting": "亮"})]
    )

    assert failed["step_failed"] is True
    assert failed["authoritative_step_failed"] is True
    assert failed["step_committed"] is False
    assert failed["step_failure_reason"] == "authoritative_phase_exception"
    assert failed["phase_errors"][0]["phase"] == "FaultingAuthoritativeSystem"
    assert failed["host_object_state_changes"] == []
    assert session.step_count == 0
    assert runner.clock.current_step == 0
    assert runner.action_queue.snapshot() == before_queue
    assert set(session.entities) == before_entities
    assert not runner.agent_registry.is_registered("临时人")
    assert runner.relation_registry.binding_snapshot() == {}
    assert delivered == []
    assert runner.dispatcher.get_events() == []
    restored_scene = session.entities["GameMaster"].get_component("SceneState")
    restored_cognition = session.entities["甲"].get_component("Cognition")
    assert id(restored_scene) == scene_identity
    assert id(restored_cognition) == cognition_identity
    assert restored_scene.model_dump() == before_scene
    assert restored_cognition.model_dump() == before_cognition
    assert controller.decision_count == 0
    assert "rendered_text" not in failed
    assert "visible_simulation_result" not in failed

    runner.systems = original_systems
    retried = session.run_step()

    assert retried["step_failed"] is False
    assert retried["step_committed"] is True
    assert retried["scheduled_actions"][0]["event_id"] == "action:1"
    assert session.step_count == 1
    assert runner.clock.current_step == 1
    assert runtime.calls == 2  # the discarded thought is not world truth
    assert controller.decision_count == 1
    assert delivered  # buffered dispatcher output is released only on commit


def test_callback_exception_before_commit_barrier_also_rolls_back():
    session, _ = _session()
    scene = session.entities["GameMaster"].get_component("SceneState")
    before = deepcopy(scene.model_dump())

    def fail_after_scheduling(phase, context, entities):
        if phase == "ActionSchedulingSystem":
            entities["GameMaster"].get_component("SceneState").update_object_state(
                "大厅", {"lighting": "callback half-state"}
            )
            raise ValueError("callback failed")
        return None

    context = session.run_step(on_phase_done=fail_after_scheduling)

    assert context["authoritative_step_failed"] is True
    assert context["phase_errors"][0]["phase"] == "ActionSchedulingSystem"
    assert scene.model_dump() == before
    assert session.step_count == 0
    assert session.runner.clock.current_step == 0


def test_delivery_exception_after_world_event_keeps_committed_world():
    session, runtime = _session()
    runner = session.runner
    rendering_index = next(
        index
        for index, system in enumerate(runner.systems)
        if system.__class__.__name__ == "RenderingSystem"
    )
    original_renderer = runner.systems[rendering_index]
    runner.systems[rendering_index] = FaultingDeliverySystem()
    scene = session.entities["GameMaster"].get_component("SceneState")
    before_version = int(scene.get_scene_flag("world_version", 0) or 0)

    context = session.run_step()

    assert context["step_failed"] is True
    assert context["authoritative_step_failed"] is False
    assert context["step_committed"] is True
    assert context["step_failure_reason"] == "delivery_phase_exception"
    assert context["phase_errors"][0]["phase"] == "FaultingDeliverySystem"
    assert session.step_count == 1
    assert runner.clock.current_step == 1
    assert int(scene.get_scene_flag("world_version", 0) or 0) > before_version
    assert "delivery_half_state" not in scene.get_object_state("大厅")
    assert runtime.calls == 1
    assert "rendered_text" not in context
    assert session.delivery_pending is True

    blocked = session.run_step()
    assert blocked["step_aborted"] is True
    assert blocked["step_abort_reason"] == "pending_delivery_retry"
    assert session.step_count == 1
    assert runner.clock.current_step == 1
    assert runtime.calls == 1

    runner.systems[rendering_index] = original_renderer
    retried = session.retry_delivery()
    assert retried["step_failed"] is False
    assert retried["step_committed"] is True
    assert retried["delivery_retry_status"] == "completed"
    assert session.delivery_pending is False
    assert session.step_count == 1
    assert runner.clock.current_step == 1
    assert runtime.calls == 1


def test_memory_delivery_retry_upserts_stable_episode_ids_without_duplicates():
    session, _ = _session()
    actor_memory = session.entities["甲"].get_component("Memory")
    original_upsert = actor_memory._vector_store.upsert_texts

    def fail_upsert(*args, **kwargs):
        raise RuntimeError("injected memory storage failure")

    actor_memory._vector_store.upsert_texts = fail_upsert
    failed = session.run_step()

    assert failed["step_failure_reason"] == "delivery_phase_exception"
    assert failed["phase_errors"][0]["phase"] == "MemorySystem"
    assert session.delivery_pending is True
    gm_memory = session.entities["GameMaster"].get_component("Memory")
    gm_before_retry = [
        item
        for item in gm_memory.list_memories()
        if item.get("metadata", {}).get("type") == "episodic_log"
        and int(item.get("metadata", {}).get("step", -1)) == 1
    ]
    assert len(gm_before_retry) == 1

    actor_memory._vector_store.upsert_texts = original_upsert
    retried = session.retry_delivery()

    assert retried["delivery_retry_status"] == "completed"
    gm_after_retry = [
        item
        for item in gm_memory.list_memories()
        if item.get("metadata", {}).get("type") == "episodic_log"
        and int(item.get("metadata", {}).get("step", -1)) == 1
    ]
    actor_after_retry = [
        item
        for item in actor_memory.list_memories()
        if item.get("metadata", {}).get("type") == "episodic_log"
        and int(item.get("metadata", {}).get("step", -1)) == 1
    ]
    assert len(gm_after_retry) == 1
    assert len(actor_after_retry) == 1
    assert session.step_count == 1


def test_episode_runner_recovers_transient_delivery_without_second_world_step():
    session, runtime = _session()
    rendering_index = next(
        index
        for index, system in enumerate(session.runner.systems)
        if system.__class__.__name__ == "RenderingSystem"
    )
    fail_once = FailOnceRenderingSystem(session.runner.systems[rendering_index])
    session.runner.systems[rendering_index] = fail_once

    report = EpisodeRunner().run(session, steps=1)

    assert len(report.steps) == 1
    assert report.steps[0].violations == ()
    assert session.step_count == 1
    assert session.runner.clock.current_step == 1
    assert session.delivery_pending is False
    assert runtime.calls == 1
    assert fail_once.calls == 2
