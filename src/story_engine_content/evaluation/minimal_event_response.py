"""A story-agnostic seed for an objective event growing a response chain."""

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.core.component import Component
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


OBSERVER = "现场观察者"
MESSENGER = "缺席者"
RECIPIENT = "同事"
HALL = "礼堂"
OFFICE = "办公室"
EVENT_ID = "timeline:ceremony:missed"


class EventResponseRuntime:
    def decide(self, entity, perception):
        pending = set(
            perception.private_cognition.get("pending_world_events", []) or []
        )
        pending_responses = list(
            perception.private_cognition.get("pending_event_responses", []) or []
        )
        active_agent_goal = next(
            (
                item
                for item in perception.private_goals.get("active", [])
                if item.get("origin") == "agent"
            ),
            None,
        )
        visible = set(perception.world_view.get("visible_actors", []) or [])
        if entity.name == MESSENGER and EVENT_ID in pending and RECIPIENT in visible:
            return AgentDecision(
                action=f"把自己错过仪式的事实告诉{RECIPIENT}。",
                thought="缺席已经发生，应该让同事知道真实情况。",
                action_spec=AgentAction(
                    "communicate",
                    f"把自己错过仪式的事实告诉{RECIPIENT}。",
                    RECIPIENT,
                ),
                metadata={
                    "goal_requests": [
                        {
                            "operation": "adopt",
                            "title": f"向{RECIPIENT}解释自己的缺席",
                            "source_kind": "world_event",
                            "source_ref": EVENT_ID,
                            "reason": "缺席事件自然产生了告知同事的需要",
                            "resolution_kind": "respond_to_event",
                            "resolution_target": RECIPIENT,
                            "resolution_response": "explain",
                        }
                    ]
                },
            )
        if entity.name == RECIPIENT and EVENT_ID in pending and pending_responses:
            return AgentDecision(
                action=f"先向{MESSENGER}确认自己听懂了解释。",
                thought="我已经知道新事实，先回应对方，再亲自前往现场。",
                action_spec=AgentAction(
                    "communicate",
                    f"先向{MESSENGER}确认自己听懂了解释。",
                    MESSENGER,
                ),
                metadata={
                    "goal_requests": [
                        {
                            "operation": "adopt",
                            "title": f"前往{HALL}了解缺席后的情况",
                            "source_kind": "event_response",
                            "source_ref": pending_responses[0],
                            "reason": "缺席者的真实解释使礼堂现场成为新的行动目标",
                            "resolution_kind": "reach_location",
                            "resolution_target": HALL,
                        }
                    ]
                },
            )
        if entity.name == RECIPIENT and active_agent_goal:
            return AgentDecision(
                action=f"继续推进自己的目标，前往{HALL}查看情况。",
                thought="回应已经完成，现在继续执行自己形成的现场确认目标。",
                action_spec=AgentAction(
                    "move",
                    f"继续推进自己的目标，前往{HALL}查看情况。",
                    HALL,
                ),
            )
        return AgentDecision(
            action="暂时等待新的事实。",
            action_spec=AgentAction("wait", "暂时等待新的事实。"),
        )


class SimulationControl(Component):
    scenario: object = None

    def simulate(self, payload):
        scene = self.entity.get_component("SceneState") if self.entity else None
        resolved = []
        actor_updates = {}
        knowledge_updates = []
        social_impacts = []
        for intent in payload.get("intents", []) or []:
            if not isinstance(intent, dict):
                continue
            actor = str(intent.get("actor", "")).strip()
            if actor not in {OBSERVER, MESSENGER, RECIPIENT}:
                continue
            kind = str(intent.get("action_kind", "wait"))
            target = str(intent.get("action_target", ""))
            location = scene.get_actor_location(actor) if scene else ""
            action = {
                "actor": actor,
                "intent": str(intent.get("intent", "")),
                "action_kind": kind,
                "action_target": target,
                "outcome": "success",
                "location": location,
                "visibility": "local",
                "private_result": "",
                "result": f"{actor}暂时等待。",
            }
            if kind == "communicate" and actor == MESSENGER and target == RECIPIENT:
                action["result"] = f"{MESSENGER}把自己错过仪式的事实告诉了{RECIPIENT}。"
                knowledge_updates.append(
                    {
                        "source": MESSENGER,
                        "target": RECIPIENT,
                        "event_id": EVENT_ID,
                        "response_kind": "explain",
                        "mode": "told",
                        "reason": "缺席者通过真实沟通转述自己知道的客观事件",
                    }
                )
                social_impacts.append(
                    {
                        "source": MESSENGER,
                        "affected": RECIPIENT,
                        "kind": "relieved",
                        "magnitude": 0.25,
                        "reason": "缺席者主动说明了已经发生的真实情况",
                        "source_event": EVENT_ID,
                    }
                )
            elif kind == "communicate" and actor == RECIPIENT and target == MESSENGER:
                action["result"] = f"{RECIPIENT}向{MESSENGER}确认自己听懂了解释。"
                knowledge_updates.append(
                    {
                        "source": RECIPIENT,
                        "target": MESSENGER,
                        "event_id": EVENT_ID,
                        "response_kind": "acknowledge",
                        "mode": "told",
                        "reason": "同事围绕双方都知道的事件作出确认回应",
                    }
                )
            elif kind == "move" and actor == RECIPIENT and target == HALL:
                action["result"] = f"{RECIPIENT}从{OFFICE}前往了{HALL}。"
                actor_updates[RECIPIENT] = {"location": HALL}
            resolved.append(action)
        return {
            "resolved_actions": resolved,
            "uncertain_outcomes": [],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": actor_updates,
            },
            "relationship_updates": [],
            "social_impacts": social_impacts,
            "modifier_updates": [],
            "knowledge_updates": knowledge_updates,
            "claim_discoveries": [],
            "object_lifecycle": [],
            "exchanges": [],
            "drive_updates": [],
            "tension_delta": 0.0,
        }


class NarrativeRenderer(Component):
    def render(self, payload):
        return "；".join(
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ) or "局面暂时平静。"


def build_minimal_event_response_scenario() -> ScenarioConfig:
    return ScenarioConfig(
        name="最小事件响应链",
        default_agent_runtime="event-response",
        description="一次客观缺席事件经真实转述产生新的角色行动。",
        environment="礼堂与相邻办公室组成的最小世界。",
        initial_state="公开仪式即将开始，缺席者仍在办公室。",
        initial_world_objects={
            HALL: {"connected_to": [OFFICE]},
            OFFICE: {"connected_to": [HALL]},
        },
        initial_actor_states={
            OBSERVER: {"location": HALL},
            MESSENGER: {"location": OFFICE},
            RECIPIENT: {"location": OFFICE},
        },
        initial_scene_flags={
            "upcoming_commitments": [
                {
                    "commitment_id": "ceremony",
                    "title": "公开仪式",
                    "summary": "受邀者可以出席，也可以承担缺席的后果。",
                    "participants": [MESSENGER],
                    "location": HALL,
                    "due_step": 1,
                    "grace_steps": 0,
                    "wake_before_steps": 0,
                }
            ]
        },
        characters=[
            CharacterConfig(
                name=OBSERVER,
                role="仪式现场观察者",
                personality="耐心记录已经发生的事实",
                goals=["留在礼堂见证仪式"],
                goal_specs=[
                    {
                        "goal_id": "witness_ceremony",
                        "title": "留在礼堂见证仪式",
                        "completion_conditions": [
                            {
                                "scope": "actor",
                                "target": OBSERVER,
                                "path": "location",
                                "operator": "eq",
                                "value": HALL,
                            }
                        ],
                    }
                ],
                is_player=True,
                agent_runtime="event-response",
            ),
            CharacterConfig(
                name=MESSENGER,
                role="未能出席的受邀者",
                personality="愿意说明已经发生的后果",
                goals=[],
                agent_runtime="event-response",
                background_interval=99,
            ),
            CharacterConfig(
                name=RECIPIENT,
                role="缺席者的同事",
                personality="得知新事实后会亲自确认现场",
                goals=[],
                agent_runtime="event-response",
                background_interval=99,
            ),
        ],
    )


def create_minimal_event_response_session(seed):
    scenario = build_minimal_event_response_scenario()
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "event-response": lambda entity, config: EventResponseRuntime()
        },
    )
    gm = session.entities["WorldHost"]
    gm.add_component(SimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session
