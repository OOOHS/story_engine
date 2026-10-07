"""Versioned whole-session snapshots with native local Hermes subject files.

The manifest contains data only. Component types are resolved from the engine's
built-in component directory; runtime adapters and credentials come from the
current deployment. A temporary archive replaces the destination atomically.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.delivery import DeliveryReceipt, _REFERENCE_KEYS
from src.story_engine.environment.action_queue import ScheduledAction
from src.story_engine.environment.runner import Runner, _default_phase_order
from src.story_engine.scenarios.config import ScenarioConfig

FORMAT_VERSION = 1
_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "access_token", "refresh_token", "secret", "credentials"}


def _public_data(value):
    if isinstance(value, dict):
        return {str(k): _public_data(v) for k, v in value.items()
                if str(k).lower() not in _SECRET_KEYS}
    if isinstance(value, (list, tuple, set)):
        return [_public_data(v) for v in value]
    return value


def _component_types():
    result = {}
    root = Path(__file__).resolve().parents[1] / "components"
    for path in sorted(root.glob("*.py")):
        module = importlib.import_module(f"src.story_engine.components.{path.stem}")
        for cls in vars(module).values():
            if isinstance(cls, type) and issubclass(cls, Component) and cls is not Component:
                result[f"{cls.__module__}:{cls.__name__}"] = cls
    return result


def save_session(session, path):
    runner = session.runner
    if session._closed:
        raise RuntimeError("cannot save a closed session")
    if [s.__class__.__name__ for s in runner.systems] != _default_phase_order():
        raise ValueError("durable saves require the production system chain")
    component_types = _component_types()
    entities, memories = [], {}
    for name, entity in runner.entities.items():
        components = []
        for key, component in entity.components.items():
            type_id = f"{component.__class__.__module__}:{component.__class__.__name__}"
            if type_id not in component_types:
                raise ValueError(f"unsupported save component: {type_id}")
            components.append({"slot": key, "type": type_id,
                               "data": _public_data(component.model_dump(mode="json"))})
            if key == "Memory":
                memories[entity.id] = component.list_memories()
        entities.append({"id": entity.id, "name": name, "components": components})
    queue = runner.action_queue
    receipt = runner._pending_delivery
    delivery = None
    if receipt:
        delivery = {"start_index": receipt.start_index, "attempts": receipt.attempts,
                    "context": {k: v for k, v in receipt.context.items()
                                if k not in _REFERENCE_KEYS and k not in {"scenario", "_relationship_book_view"}}}
    definitions = {}
    for system in runner.systems:
        if system.__class__.__name__ in {"ModifierSystem", "SentimentSystem"}:
            definitions[system.__class__.__name__] = {
                k: dataclasses.asdict(v) for k, v in system.dynamics.definitions.items()}
    manifest = {
        "format_version": FORMAT_VERSION,
        "scenario": _public_data(session.scenario.model_dump(mode="json")),
        "presentation": session.presentation_state,
        "step_count": session.step_count, "random_seed": runner.random_seed,
        "clock": {"start": runner.clock.start_time.isoformat(),
                  "time": runner.clock.current_time.isoformat(),
                  "step": runner.clock.current_step,
                  "duration": runner.clock.step_duration.total_seconds()},
        "entities": entities, "memories": memories, "definitions": definitions,
        "relations": runner.relation_registry.binding_snapshot(),
        "claims": runner.claim_registry.binding_snapshot(),
        "queue": {"time": queue.current_time, "sequence": queue._sequence,
                  "pending": [dataclasses.asdict(e) for e in queue._heap],
                  "owed_immunity": sorted(queue._owed_immunity)},
        "delivery": delivery, "subjects": {},
    }
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    captured = []
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for registered in runner.agent_registry.agents():
                runtime = registered.runtime
                capture = getattr(runtime, "capture_subject_checkpoint", None)
                if not callable(capture):
                    if registered.entity.get_component("AgentController").runtime == "hermes":
                        raise ValueError("Hermes runtime cannot capture its session")
                    if registered.entity.get_component("AgentController").runtime != "offline":
                        raise ValueError("stateful runtimes require a durable subject snapshot interface")
                    continue
                payload = capture()
                captured.append((runtime, payload))
                # All awakened conversations must have a native snapshot. Docker
                # and incomplete adapters fail before replacing an existing save.
                native = payload.get("conversations", {})
                if set(payload.get("conversation_ids", [])) != set(native):
                    raise ValueError("every live subject conversation requires a native snapshot")
                exported = {k: v for k, v in payload.items() if k != "conversations"}
                exported["conversations"] = {}
                for entity_id, checkpoint in native.items():
                    directory = Path(checkpoint.get("checkpoint_dir", ""))
                    if not directory.is_dir():
                        raise ValueError("durable Hermes saves require local checkpoint files")
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", registered.entity.id) or entity_id != registered.entity.id:
                        raise ValueError("invalid saved subject identity")
                    prefix = f"subjects/{registered.entity.id}/{entity_id}/"
                    for file in directory.rglob("*"):
                        if file.is_symlink():
                            raise ValueError("subject snapshots cannot contain symbolic links")
                        if file.is_file():
                            archive.write(file, prefix + file.relative_to(directory).as_posix())
                    exported["conversations"][entity_id] = {"archive_prefix": prefix}
                manifest["subjects"][registered.entity.name] = exported
            archive.writestr("session.json", json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        with open(temporary, "rb") as saved:
            os.fsync(saved.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        for runtime, payload in captured:
            release = getattr(runtime, "release_subject_checkpoint", None)
            if callable(release):
                release(payload)
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return target


def load_session(path, *, agent_runtime_factories=None):
    from src.story_engine.session.session import Session
    from src.story_engine.session.play_profile import runtime_factories_for_profile
    from src.story_engine.social.sentiments import SentimentDefinition
    from src.story_engine.simulation.modifiers import ModifierDefinition
    from src.config.config import config

    runner = None
    with ZipFile(Path(path).expanduser()) as archive, tempfile.TemporaryDirectory(prefix="story-load-") as scratch:
        data = json.loads(archive.read("session.json"))
        if data.get("format_version") != FORMAT_VERSION:
            raise ValueError("unsupported Story Engine save version")
        classes = _component_types()
        # Validate component identities before constructing any native runtime.
        for row in data["entities"]:
            for component in row["components"]:
                if component["type"] not in classes:
                    raise ValueError(f"unsupported save component: {component['type']}")
        scenario = ScenarioConfig.model_validate(data["scenario"])
        if agent_runtime_factories is None:
            profile = scenario.metadata.get("play_profile", "production")
            agent_runtime_factories = runtime_factories_for_profile(profile)
        definitions = data["definitions"]
        runner = Runner(agent_runtime_factories=agent_runtime_factories,
                        random_seed=data["random_seed"],
                        modifier_definitions={k: ModifierDefinition(**v) for k, v in definitions["ModifierSystem"].items()},
                        sentiment_definitions={k: SentimentDefinition(**v) for k, v in definitions["SentimentSystem"].items()})
        runner.scenario = scenario
        try:
            ids = set()
            for row in data["entities"]:
                if row["id"] in ids or row["name"] in runner.entities:
                    raise ValueError("duplicate save entity identity")
                ids.add(row["id"])
                entity = Entity(row["name"], entity_id=row["id"])
                for component in row["components"]:
                    payload = dict(component["data"])
                    slot = component["slot"]
                    if "scenario" in classes[component["type"]].model_fields:
                        payload["scenario"] = scenario
                    if "llm_config" in payload:
                        role = "narrator" if slot == "NarrativeRenderer" else "game_master"
                        payload["llm_config"] = {**config.get_component_config(role), **payload["llm_config"]}
                    if slot == "Memory":
                        payload["namespace"] = runner.memory_namespace
                        payload["collection_name"] = None
                    instance = classes[component["type"]](**payload)
                    entity.add_component(instance)
                    if slot != str(instance.component_slot or instance.__class__.__name__):
                        raise ValueError("save component slot does not match its type")
                    if slot == "Memory":
                        for memory in data["memories"].get(entity.id, []):
                            instance.add_memory(memory["content"], metadata=memory["metadata"], memory_id=memory["id"])
                runner.add_entity(entity)
            # Upgrade older production snapshots using their existing shared
            # narrative and consumed-storylet ledger. No completed block restarts.
            if scenario.simulation_mode == "llm":
                from src.story_engine.components.story_planner import StoryPlanner
                from src.story_engine.components.story_tracking import StoryTracking
                for host in runner.entities.values():
                    if host.get_component("SimulationControl") is None:
                        continue
                    if host.get_component("StoryPlanner") is None:
                        planner = StoryPlanner(scenario=scenario,
                            llm_config=config.get_component_config("game_master"),
                            interval_turns=scenario.story_planner_interval_turns,
                            interval_seconds=scenario.story_planner_interval_seconds)
                        planner.record_opening(scenario.initial_state)
                        host.add_component(planner)
                    if host.get_component("StoryTracking") is None:
                        host.add_component(StoryTracking(scenario=scenario,
                            llm_config=config.get_component_config("game_master")))
                    host.get_component("StoryTracking").reconcile(host.get_component("SceneState"))
            runner.relation_registry.restore_bindings(data["relations"], runner.entities)
            runner.claim_registry.restore_bindings(data["claims"], runner.entities)
            clock = data["clock"]
            runner.clock.start_time = datetime.fromisoformat(clock["start"])
            runner.clock.current_time = datetime.fromisoformat(clock["time"])
            runner.clock.current_step = int(clock["step"])
            runner.clock.step_duration = timedelta(seconds=clock["duration"])
            queue = data["queue"]
            pending = [ScheduledAction(**e) for e in queue["pending"]]
            runner.action_queue.restore((queue["time"], pending, queue["sequence"],
                                         {e.actor: e for e in pending if e.actor != "World"}, set(queue["owed_immunity"])))
            for entity in runner.entities.values():
                if entity.get_component("AgentController"):
                    runner.register_agent(entity)
            errors = runner.agent_boundary_errors()
            if errors:
                raise ValueError("saved agent boundaries are invalid: " + "; ".join(errors))
            for name, payload in data["subjects"].items():
                registered = runner.agent_registry.get(name)
                restore = getattr(registered.runtime, "restore_saved_subject", None) if registered else None
                if not callable(restore):
                    raise ValueError(f"runtime cannot restore saved subject: {name}")
                for entity_id, checkpoint in payload["conversations"].items():
                    expected = f"subjects/{registered.entity.id}/{entity_id}/"
                    if checkpoint["archive_prefix"] != expected or entity_id != registered.entity.id:
                        raise ValueError("saved subject identity mismatch")
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", entity_id):
                        raise ValueError("invalid saved subject identity")
                    directory = Path(scratch) / registered.entity.id
                    directory.mkdir(parents=True, exist_ok=True)
                    for member in archive.namelist():
                        if member.startswith(expected):
                            relative = Path(member[len(expected):])
                            if relative.is_absolute() or ".." in relative.parts:
                                raise ValueError("unsafe subject archive path")
                            destination = directory / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(archive.read(member))
                    payload["conversations"][entity_id] = {"checkpoint_dir": str(directory)}
                restore(registered.entity, payload)
            if data["delivery"]:
                receipt = data["delivery"]
                context = receipt["context"]
                context.update({"scenario": scenario, "dispatcher": runner.dispatcher,
                                "clock": runner.clock, "agent_registry": runner.agent_registry,
                                "action_queue": runner.action_queue, "relation_registry": runner.relation_registry,
                                "random_streams": runner.random_streams, "check_resolver": runner.check_resolver,
                                "register_agent": runner.register_agent, "unregister_agent": runner.unregister_agent,
                                "claim_registry": runner.claim_registry, "memory_namespace": runner.memory_namespace})
                start = int(receipt["start_index"])
                if start <= _default_phase_order().index("WorldEventSystem") or start >= len(runner.systems):
                    raise ValueError("invalid saved delivery phase")
                runner._pending_delivery = DeliveryReceipt.capture(start_index=start, context=context, attempts=receipt["attempts"])
            session = Session(runner, scenario)
            session.step_count = int(data["step_count"])
            session.presentation_state = data.get("presentation", {})
            return session
        except Exception:
            runner.close()
            raise
