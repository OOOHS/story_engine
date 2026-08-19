from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.policy import CharacterPolicy
from src.story_engine.agents.types import (
    AgentDecision,
    AgentMotiveReference,
    AgentPerception,
)
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.goal_state import GoalState
from src.story_engine.components.trait_state import TraitState
from src.story_engine.core.entity import Entity
from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.common.action_features import resolve_social_response_kind
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.social import RelationshipBook
from src.story_engine.systems.input import InputSystem


def _entity(traits=(), risk_tolerance=0.5):
    entity = Entity("甲")
    entity.add_component(TraitState.from_initial(traits))
    entity.add_component(DriveState(risk_tolerance=risk_tolerance))
    entity.add_component(
        AgentController(runtime="llm", config={"policy": {"temperature": 0.8}})
    )
    return entity


def _decision():
    candidates = (
        AgentAction("interact", "冒险冲进火场救出乙。", "乙"),
        AgentAction("communicate", "站在窗外呼喊乙，告诉他出口方向。", "乙"),
        AgentAction("move", "撤退到安全地点等待支援。", "安全地点"),
    )
    return AgentDecision(
        action=candidates[0].detail,
        action_spec=candidates[0],
        candidates=candidates,
    )


def _probability(trace, candidate_id):
    return next(
        item["probability"]
        for item in trace["candidates"]
        if item["candidate_id"] == candidate_id
    )


def test_policy_collapses_paraphrases_but_preserves_material_alternatives():
    policy = CharacterPolicy()
    first = AgentAction("observe", "查看门上的划痕。", "木门")
    paraphrase = AgentAction("observe", "更仔细地观察木门划痕。")
    other_target = AgentAction("observe", "查看窗边的泥迹。", "窗户")
    confrontation = AgentAction("communicate", "质问乙为何隐瞒。", "乙")
    neutral_question = AgentAction("communicate", "询问乙是否见过来客。", "乙")

    assert policy.materially_distinct(first, paraphrase) is False
    assert policy.materially_distinct(first, other_target) is True
    assert policy.materially_distinct(confrontation, neutral_question) is True

    decision = AgentDecision(
        action=first.detail,
        action_spec=first,
        candidates=(first, paraphrase, other_target),
    )
    collected = policy._collect_candidates(
        AgentPerception(actor_name="甲", step=1), decision
    )
    runtime_actions = [
        item.action for item in collected if item.source == "runtime"
    ]
    assert runtime_actions == [first, other_target]


def test_formal_references_make_same_target_actions_materially_distinct():
    policy = CharacterPolicy()
    left = AgentAction(
        "communicate",
        "说明第一条线索。",
        "乙",
        claim_id="claim:first",
        claim_stance="supports",
    )
    right = AgentAction(
        "communicate",
        "说明第二条线索。",
        "乙",
        claim_id="claim:second",
        claim_stance="supports",
    )

    assert policy.materially_distinct(left, right) is True


def test_social_response_kinds_are_distinct_host_probability_features():
    policy = CharacterPolicy()
    apology = AgentAction(
        "communicate",
        "向乙郑重道歉，并承认自己造成了损失。",
        "乙",
    )
    request = AgentAction(
        "communicate",
        "请求乙暂时放下争执，先一起检查现场。",
        "乙",
    )
    decision = AgentDecision(
        action=apology.detail,
        action_spec=apology,
        candidates=(apology, request),
    )
    perception = AgentPerception(actor_name="甲", step=2)
    neutral = policy.select(
        entity=_entity(),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(12),
        world_version=1,
    )
    apologetic = policy.select(
        entity=_entity([
            {
                "trait_id": "remorseful",
                "intensity": 1.0,
                "policy_weights": {"apologize": 1.0},
            }
        ]),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(12),
        world_version=1,
    )

    assert policy.materially_distinct(apology, request) is True
    apology_candidate = next(
        item
        for item in apologetic.trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )
    request_candidate = next(
        item
        for item in apologetic.trace["candidates"]
        if item["candidate_id"] == "runtime:1"
    )
    assert {"acknowledge", "apologize", "social"}.issubset(
        apology_candidate["tags"]
    )
    assert "request" in request_candidate["tags"]
    assert _probability(apologetic.trace, "runtime:0") > _probability(
        neutral.trace, "runtime:0"
    )


def test_social_response_classifier_prefers_clear_text_then_valid_gm_fallback():
    assert resolve_social_response_kind(
        "我为此前的隐瞒向你道歉。", "accuse"
    ) == "apologize"
    assert resolve_social_response_kind(
        "我需要和你谈谈这件事。", "request"
    ) == "request"
    assert resolve_social_response_kind(
        "我需要和你谈谈这件事。", "force_plot"
    ) == "report"
    assert resolve_social_response_kind(
        "我向你确认自己听懂了解释。", "acknowledge"
    ) == "acknowledge"


def test_social_response_tags_never_leak_onto_noncommunication_actions():
    policy = CharacterPolicy()
    observe = AgentAction("observe", "确认木门是否已经锁好。", "木门")
    interact = AgentAction("interact", "请求机关释放卡住的锁舌。", "机关")
    communicate = AgentAction("communicate", "向乙确认自己已经理解。", "乙")

    assert "acknowledge" not in policy._infer_tags(observe)
    assert "request" not in policy._infer_tags(interact)
    assert "acknowledge" in policy._infer_tags(communicate)


def test_private_continuity_weakly_biases_host_sampling_without_forcing_it():
    policy = CharacterPolicy()
    continue_plan = AgentAction(
        "interact",
        "继续检查窗边痕迹，确认闯入者身份后再告诉乙。",
        "窗户",
    )
    rest = AgentAction("wait", "坐下休息，暂时不再调查。")
    decision = AgentDecision(
        action=continue_plan.detail,
        action_spec=continue_plan,
        candidates=(continue_plan, rest),
    )
    perception = AgentPerception(
        actor_name="甲",
        step=3,
        current_plan="检查窗边痕迹后告诉乙",
        private_cognition={
            "current_focus": "窗边痕迹",
            "commitments": ["确认闯入者身份"],
        },
    )

    selection = policy.select(
        entity=_entity(),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(41),
        world_version=2,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert 0 < by_id["runtime:0"]["continuity_contribution"] <= 0.6
    assert by_id["runtime:0"]["continuity_contributions"].keys() == {
        "plan",
        "focus",
        "commitment:0",
    }
    assert by_id["runtime:1"]["continuity_contribution"] == 0
    assert by_id["runtime:1"]["probability"] > 0


def test_repeated_policy_choice_gets_soft_diminishing_return():
    policy = CharacterPolicy()
    repeat = AgentAction("observe", "再次检查木门上的划痕。", "木门")
    alternative = AgentAction("communicate", "询问乙是否听见门外动静。", "乙")
    entity = _entity()
    controller = entity.get_component("AgentController")
    controller.last_policy_action_signature = policy.repetition_signature(repeat)
    controller.last_policy_action_target = "木门"
    controller.repeated_policy_action_count = 2
    decision = AgentDecision(
        action=repeat.detail,
        action_spec=repeat,
        candidates=(repeat, alternative),
    )

    selection = policy.select(
        entity=entity,
        perception=AgentPerception(actor_name="甲", step=4),
        decision=decision,
        random_streams=DeterministicRandomStreams(7),
        world_version=3,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["repetition_contribution"] == -0.3
    assert by_id["runtime:1"]["repetition_contribution"] == 0
    assert by_id["runtime:0"]["probability"] > 0

    omitted_target = policy._candidate(
        "omitted",
        AgentAction("observe", "换一种措辞再次查看木门。"),
        "runtime",
        base_utility=0.0,
    )
    other_target = policy._candidate(
        "other",
        AgentAction("observe", "检查窗边痕迹。", "窗户"),
        "runtime",
        base_utility=0.0,
    )
    assert policy._repetition_score(controller, omitted_target) == -0.3
    assert policy._repetition_score(controller, other_target) == 0


def test_wait_repetition_penalty_is_lighter_and_controller_ledger_resets():
    controller = AgentController(runtime="llm")
    wait = AgentAction("wait", "继续等待。")
    observe = AgentAction("observe", "再次检查房间。", "房间")
    wait_signature = CharacterPolicy.repetition_signature(wait)
    observe_signature = CharacterPolicy.repetition_signature(observe)

    for _ in range(4):
        controller.record_policy_action(wait_signature)

    wait_candidate = CharacterPolicy()._candidate(
        "wait", wait, "runtime", base_utility=0.0
    )
    assert CharacterPolicy._repetition_score(controller, wait_candidate) == -0.2
    assert controller.repeated_policy_action_count == 4
    assert controller.max_repeated_policy_action_count == 4

    controller.record_policy_action(observe_signature, "木门")
    controller.record_policy_action(observe_signature, "窗户")

    assert controller.repeated_policy_action_count == 1
    assert controller.last_policy_action_target == "窗户"
    assert controller.max_repeated_policy_action_count == 4


def test_structured_trait_changes_host_probability_not_llm_claims():
    policy = CharacterPolicy()
    perception = AgentPerception(actor_name="甲", step=3)
    neutral = policy.select(
        entity=_entity(),
        perception=perception,
        decision=_decision(),
        random_streams=DeterministicRandomStreams(11),
        world_version=5,
    )
    brave = policy.select(
        entity=_entity(
            [
                {
                    "trait_id": "brave",
                    "intensity": 0.9,
                    "policy_weights": {"risk": 1.4, "confront": 0.5, "retreat": -1.0},
                }
            ],
            risk_tolerance=0.8,
        ),
        perception=perception,
        decision=_decision(),
        random_streams=DeterministicRandomStreams(11),
        world_version=5,
    )

    assert _probability(brave.trace, "runtime:0") > _probability(
        neutral.trace, "runtime:0"
    )
    assert _probability(brave.trace, "runtime:2") < _probability(
        neutral.trace, "runtime:2"
    )
    risky = next(
        item for item in brave.trace["candidates"] if item["candidate_id"] == "runtime:0"
    )
    assert risky["trait_contributions"]["brave"] > 0
    assert risky["risk_contribution"] > 0


def test_host_affordance_features_drive_trait_probability_without_keywords():
    policy = CharacterPolicy()
    perception = AgentPerception(actor_name="甲", step=3)
    candidates = (
        AgentAction(
            "interact",
            "使用这个装置。",
            "救生绳",
            affordance_id="rescue",
        ),
        AgentAction("wait", "留在原地。"),
    )
    decision = AgentDecision(
        action=candidates[0].detail,
        action_spec=candidates[0],
        candidates=candidates,
    )
    inputs = dict(
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(17),
        world_version=2,
        host_action_features={
            ("救生绳", "rescue"): ("aid", "risk"),
        },
    )
    neutral = policy.select(entity=_entity(), **inputs)
    protective = policy.select(
        entity=_entity([
            {
                "trait_id": "protective",
                "intensity": 1.0,
                "policy_weights": {"aid": 1.2, "risk": 0.4},
            }
        ]),
        **inputs,
    )

    assert _probability(protective.trace, "runtime:0") > _probability(
        neutral.trace, "runtime:0"
    )
    candidate = next(
        item for item in protective.trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )
    assert {"aid", "risk"}.issubset(candidate["tags"])
    assert candidate["trait_contributions"]["protective"] > 0


def test_affordance_policy_features_stay_host_private_during_perception():
    entity = _entity()
    scene = SceneState(
        world_objects={
            "房间": {},
            "救生绳": {
                "is_location": False,
                "location": "房间",
                "portable": False,
                "hidden": False,
                "affordances": [{
                    "id": "rescue",
                    "label": "使用救生绳",
                    "need_effects": {},
                    "policy_tags": ["aid", "risk"],
                }],
            },
            "钥匙": {
                "is_location": False,
                "location": "房间",
                "portable": True,
                "hidden": False,
                "owner": None,
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )
    context = {"clock": None, "intents": []}

    perception = InputSystem().build_agent_perception(
        entity=entity,
        scene_state=scene,
        intents_buffer=[],
        context=context,
    )

    opportunity = next(
        item for item in perception.affordance_opportunities
        if item["affordance_id"] == "rescue"
    )
    assert "policy_tags" not in opportunity
    assert context["_host_action_features"]["甲"] == {
        ("救生绳", "rescue"): ("aid", "risk"),
        ("钥匙", "engine:take"): ("acquire",),
    }


def test_policy_sampling_is_replayable_and_records_the_roll():
    policy = CharacterPolicy()
    inputs = dict(
        entity=_entity(),
        perception=AgentPerception(actor_name="甲", step=7),
        decision=_decision(),
        world_version=12,
    )
    first = policy.select(
        **inputs,
        random_streams=DeterministicRandomStreams("same-session"),
    )
    second = policy.select(
        **inputs,
        random_streams=DeterministicRandomStreams("same-session"),
    )

    assert first.action == second.action
    assert first.trace["roll"] == second.trace["roll"]
    assert first.trace["roll_key"] == "甲|7|12"
    assert first.trace["stream"] == "policy"


def test_random_streams_separate_policy_world_and_observation_rolls():
    streams = DeterministicRandomStreams(123)

    policy = streams.uniform("policy", "甲", 1).value
    world = streams.uniform("world", "甲", 1).value
    observation = streams.uniform("observation", "甲", 1).value

    assert len({policy, world, observation}) == 3
    assert streams.uniform("policy", "甲", 1).value == policy


def test_deterministic_runtime_can_still_commit_one_host_action():
    selection = CharacterPolicy().select(
        entity=_entity(),
        perception=AgentPerception(actor_name="甲", step=0),
        decision=AgentDecision(action="等待约定的人。"),
        random_streams=DeterministicRandomStreams(1),
        world_version=0,
    )

    assert selection.trace["mode"] == "runtime_committed"
    assert selection.action.detail == "等待约定的人。"


def test_environment_affordance_candidate_preserves_exact_host_reference():
    perception = AgentPerception(
        actor_name="甲",
        step=0,
        affordance_opportunities=[
            {
                "object_id": "面包",
                "affordance_id": "eat",
                "label": "进食",
                "available": True,
                "relief_score": 0.4,
            }
        ],
    )

    candidates = CharacterPolicy()._environment_candidates(perception)
    candidate = next(
        item
        for item in candidates
        if item.candidate_id == "environment:affordance:面包:eat"
    )

    assert candidate.action.kind == "interact"
    assert candidate.action.target == "面包"
    assert candidate.action.affordance_id == "eat"


def test_bare_physical_possibility_does_not_manufacture_character_motivation():
    perception = AgentPerception(
        actor_name="甲",
        step=0,
        affordance_opportunities=[
            {
                "object_id": "包裹",
                "affordance_id": "engine:drop",
                "label": "放下",
                "available": True,
                "source": "engine_physics",
            }
        ],
    )

    candidates = CharacterPolicy()._environment_candidates(perception)

    assert all(
        item.action.affordance_id != "engine:drop" for item in candidates
    )


def test_authored_capability_without_need_relief_does_not_create_motivation():
    perception = AgentPerception(
        actor_name="甲",
        step=0,
        affordance_opportunities=[
            {
                "object_id": "救生绳",
                "affordance_id": "rescue",
                "label": "使用救生绳",
                "available": True,
                "relief_score": 0.0,
                "source": "object_definition",
            }
        ],
    )

    candidates = CharacterPolicy()._environment_candidates(perception)

    assert all(
        item.action.affordance_id != "rescue" for item in candidates
    )


def test_directional_relationship_tracks_modify_social_action_probability():
    relationship = RelationshipBook()
    relationship.set_track("甲", "乙", "favor", 5)
    relationship.set_track("甲", "乙", "trust", 4)
    relationship.set_track("乙", "甲", "malice", 5)
    visible = relationship.get_visible_relations(
        "甲", ["甲", "乙"], {"乙": {}}
    )
    assert visible[0]["viewer_toward_actor_states"] == [
        "non_hostile",
        "trusted",
        "friendly",
        "close",
    ]
    assert "viewer_toward_actor" not in visible[0]
    assert "toward_viewer" not in visible[0]
    perception = AgentPerception(
        actor_name="甲",
        step=1,
        relationship_context={"visible_relations": visible},
    )
    candidates = (
        AgentAction("interact", "帮助乙搬开挡路的重物。", "乙"),
        AgentAction("wait", "暂时不理会乙。"),
    )
    decision = AgentDecision(
        action=candidates[0].detail,
        action_spec=candidates[0],
        candidates=candidates,
    )
    selection = CharacterPolicy().select(
        entity=_entity(),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(3),
        world_version=1,
        relationship_book=relationship,
    )
    aid = next(
        item
        for item in selection.trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )

    assert aid["relationship_contribution"] > 0
    assert set(aid["relationship_contributions"]) == {"favor", "trust"}


def test_policy_trace_names_the_goals_that_supported_each_candidate():
    entity = _entity()
    entity.add_component(
        GoalState.from_initial(
            structured=[
                {
                    "goal_id": "rescue-乙",
                    "title": "帮助乙摆脱危险",
                }
            ]
        )
    )
    candidates = (
        AgentAction("interact", "帮助乙摆脱眼前的危险。", "乙"),
        AgentAction("wait", "留在原地等待。"),
    )
    selection = CharacterPolicy().select(
        entity=entity,
        perception=AgentPerception(actor_name="甲", step=2),
        decision=AgentDecision(
            action=candidates[0].detail,
            action_spec=candidates[0],
            candidates=candidates,
        ),
        random_streams=DeterministicRandomStreams(8),
        world_version=1,
    )

    rescue = next(
        item
        for item in selection.trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )
    assert rescue["goal_contribution"] > 0
    assert rescue["goal_contributions"]["rescue-乙"] > 0


def test_policy_trace_names_commitment_knowledge_and_need_motives():
    perception = AgentPerception(
        actor_name="甲",
        step=4,
        world_view={
            "location": "房间",
            "visible_actors": ["甲", "乙"],
        },
        private_obligations={
            "active": [
                {
                    "obligation_id": "deliver-letter",
                    "title": "把信交给乙",
                    "creditor": "乙",
                    "steps_remaining": 0,
                }
            ]
        },
        private_schedule={
            "active": [
                {
                    "commitment_id": "meeting",
                    "title": "赴约",
                    "location": "会客室",
                    "steps_until_due": 1,
                }
            ]
        },
        private_knowledge={
            "claims": [
                {
                    "claim_id": "hidden-deal",
                    "statement": "乙隐瞒了交易",
                    "stance": "supports",
                }
            ],
            "potential_leverage": [
                {
                    "claim_id": "hidden-deal",
                    "targets": ["乙"],
                    "confidence": 0.8,
                    "evidence_backed": True,
                    "owned_supporting_evidence": ["账册"],
                }
            ],
        },
        affordance_opportunities=[
            {
                "object_id": "面包",
                "affordance_id": "eat",
                "label": "进食",
                "available": True,
                "relief_score": 0.3,
                "relief_contributions": {"hunger": 0.3},
            }
        ],
    )
    obligation_action = AgentAction(
        "interact", "把信交给乙，履行眼前的交付责任。", "乙"
    )
    agreement_action = AgentAction(
        "communicate",
        "接受乙提出的协议。",
        "乙",
        agreement_operation="accept",
        agreement_id="deal",
    )
    selection = CharacterPolicy().select(
        entity=_entity(),
        perception=perception,
        decision=AgentDecision(
            action=obligation_action.detail,
            action_spec=obligation_action,
            candidates=(obligation_action, agreement_action),
        ),
        random_streams=DeterministicRandomStreams(13),
        world_version=2,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["obligation_contributions"] == {
        "deliver-letter": 1.2
    }
    assert by_id["runtime:1"]["agreement_contributions"] == {"deal": 1.5}
    assert by_id["schedule:move:meeting:会客室"]["schedule_contributions"] == {
        "meeting": 0.4
    }
    assert by_id["environment:affordance:面包:eat"][
        "relief_contributions"
    ] == {"hunger": 0.6}
    assert by_id["environment:leverage:hidden-deal:乙"][
        "knowledge_contributions"
    ] == {"hidden-deal": 0.62}


def test_explicit_candidate_motive_refs_survive_paraphrase_and_are_host_audited():
    entity = _entity()
    entity.add_component(
        GoalState.from_initial(
            structured=[
                {"goal_id": "find-exit", "title": "找到离开这里的方法"}
            ]
        )
    )
    indirect = AgentAction(
        "interact",
        "把松动的铜片压进墙缝，试试看会发生什么。",
        "墙缝",
    )
    unrelated = AgentAction("wait", "先坐下来保持安静。")
    perception = AgentPerception(
        actor_name="甲",
        step=5,
        private_obligations={
            "active": [
                {
                    "obligation_id": "keep-watch",
                    "title": "守住入口",
                    "creditor": "乙",
                    "steps_remaining": 1,
                }
            ]
        },
    )
    decision = AgentDecision(
        action=indirect.detail,
        action_spec=indirect,
        candidates=(indirect, unrelated),
        candidate_motive_refs=(
            (
                AgentMotiveReference("goal", "find-exit"),
                AgentMotiveReference("obligation", "keep-watch"),
            ),
            (
                AgentMotiveReference("goal", "invented-goal"),
                AgentMotiveReference("obligation", "other-actor-duty"),
            ),
        ),
    )

    selection = CharacterPolicy().select(
        entity=entity,
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(29),
        world_version=3,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["goal_contributions"] == {"find-exit": 0.8}
    assert by_id["runtime:0"]["obligation_contributions"] == {
        "keep-watch": 0.8
    }
    assert by_id["runtime:0"]["validated_motive_refs"] == [
        {"kind": "goal", "ref": "find-exit"},
        {"kind": "obligation", "ref": "keep-watch"},
    ]
    assert by_id["runtime:1"]["goal_contribution"] == 0
    assert by_id["runtime:1"]["obligation_contribution"] == 0
    assert by_id["runtime:1"]["rejected_motive_refs"] == [
        {"kind": "goal", "ref": "invented-goal"},
        {"kind": "obligation", "ref": "other-actor-duty"},
    ]


def test_pending_attention_can_motivate_reaction_but_unknown_or_handled_events_cannot():
    entity = _entity()
    cognition = Cognition()
    cognition.record_world_event(
        event_id="alarm:raised",
        statement="大厅的警报响了。",
        step=4,
        location="大厅",
        witness_mode="observed",
        attention_priority=95,
    )
    cognition.record_world_event(
        event_id="door:closed:yesterday",
        statement="昨天门被关上了。",
        step=1,
        location="大厅",
        witness_mode="observed",
        attention_priority=50,
    )
    cognition.acknowledge_world_events(["door:closed:yesterday"])
    response_id = "event-response:alarm:乙->甲:request"
    cognition.record_event_response(
        response_id=response_id,
        event_id="alarm:raised",
        source="乙",
        response_kind="request",
        statement="乙请求甲一起离开。",
        step=4,
        location="大厅",
        attention_priority=90,
    )
    entity.add_component(cognition)
    react = AgentAction("move", "立即转向最近的安全出口。", "出口")
    ignore = AgentAction("wait", "继续做原来的事情。")
    decision = AgentDecision(
        action=react.detail,
        action_spec=react,
        candidates=(react, ignore),
        candidate_motive_refs=(
            (
                AgentMotiveReference("world_event", "alarm:raised"),
                AgentMotiveReference("event_response", response_id),
            ),
            (
                AgentMotiveReference(
                    "world_event", "door:closed:yesterday"
                ),
                AgentMotiveReference("world_event", "remote:explosion"),
            ),
        ),
    )
    perception = AgentPerception(
        actor_name="甲",
        step=5,
        private_cognition=cognition.get_private_snapshot(current_step=5),
    )

    selection = CharacterPolicy().select(
        entity=entity,
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(31),
        world_version=4,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["world_event_contributions"] == {
        "alarm:raised": 0.815
    }
    assert by_id["runtime:0"]["event_response_contributions"] == {
        response_id: 0.78
    }
    assert by_id["runtime:0"]["validated_motive_refs"] == [
        {"kind": "world_event", "ref": "alarm:raised"},
        {"kind": "event_response", "ref": response_id},
    ]
    assert by_id["runtime:1"]["world_event_contribution"] == 0
    assert by_id["runtime:1"]["rejected_motive_refs"] == [
        {"kind": "world_event", "ref": "door:closed:yesterday"},
        {"kind": "world_event", "ref": "remote:explosion"},
    ]


def test_active_navigation_problem_can_motivate_recovery_without_prescribing_it():
    recover = AgentAction("move", "改走南路前往城镇。", "南路")
    investigate = AgentAction("observe", "检查断桥附近是否还有通路。", "断桥")
    problem_id = "navigation:blocked-bridge"
    decision = AgentDecision(
        action=recover.detail,
        action_spec=recover,
        candidates=(recover, investigate),
        candidate_motive_refs=(
            (AgentMotiveReference("navigation_problem", problem_id),),
            (AgentMotiveReference("navigation_problem", "invented-route"),),
        ),
    )
    perception = AgentPerception(
        actor_name="甲",
        step=6,
        private_navigation={
            "active": [
                {
                    "problem_id": problem_id,
                    "route_source": "村口",
                    "route_target": "断桥",
                    "destination": "城镇",
                    "alternative_path": ["村口", "南路", "城镇"],
                    "steps_remaining": 2,
                }
            ]
        },
    )

    selection = CharacterPolicy().select(
        entity=_entity(),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(37),
        world_version=5,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["navigation_contributions"] == {
        problem_id: 0.7
    }
    assert by_id["runtime:0"]["validated_motive_refs"] == [
        {"kind": "navigation_problem", "ref": problem_id}
    ]
    assert by_id["runtime:1"]["navigation_contribution"] == 0
    assert by_id["runtime:1"]["rejected_motive_refs"] == [
        {"kind": "navigation_problem", "ref": "invented-route"}
    ]


def test_recent_failed_action_can_motivate_a_method_change_without_becoming_world_fact():
    change_method = AgentAction(
        "observe", "换个角度检查锁舌的结构。", "铁门"
    )
    repeat = AgentAction("interact", "再次直接拧动铁门。", "铁门")
    failed_event = "action:17"
    decision = AgentDecision(
        action=change_method.detail,
        action_spec=change_method,
        candidates=(change_method, repeat),
        candidate_motive_refs=(
            (AgentMotiveReference("action_failure", failed_event),),
            (AgentMotiveReference("action_failure", "action:success"),),
        ),
    )
    perception = AgentPerception(
        actor_name="甲",
        step=5,
        private_cognition={
            "recent_experiences": [
                {
                    "step": 4,
                    "events": [
                        {
                            "event_id": failed_event,
                            "outcome": "blocked",
                            "action_kind": "interact",
                            "action_target": "铁门",
                            "result": "锁舌没有移动。",
                        },
                        {
                            "event_id": "action:success",
                            "outcome": "success",
                            "action_kind": "observe",
                            "action_target": "窗户",
                        },
                    ],
                }
            ]
        },
    )

    selection = CharacterPolicy().select(
        entity=_entity(),
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(41),
        world_version=6,
    )
    by_id = {
        item["candidate_id"]: item for item in selection.trace["candidates"]
    }

    assert by_id["runtime:0"]["action_failure_contributions"] == {
        failed_event: 0.57
    }
    assert by_id["runtime:0"]["validated_motive_refs"] == [
        {"kind": "action_failure", "ref": failed_event}
    ]
    assert by_id["runtime:1"]["action_failure_contribution"] == 0
    assert by_id["runtime:1"]["rejected_motive_refs"] == [
        {"kind": "action_failure", "ref": "action:success"}
    ]


def test_input_samples_llm_candidates_on_host_and_keeps_weights_private():
    class SimulationControl(Component):
        pass

    class CandidateRuntime:
        def __init__(self):
            self.perception = None

        def decide(self, _entity, perception):
            self.perception = perception
            candidates = (
                AgentAction("interact", "冒险冲过火线帮助乙。", "乙"),
                AgentAction("wait", "留在原地等待。"),
            )
            return AgentDecision(
                action=candidates[0].detail,
                action_spec=candidates[0],
                candidates=candidates,
            )

    entity = _entity(
        [
            {
                "trait_id": "brave",
                "intensity": 0.8,
                "policy_weights": {"risk": 1.2},
            }
        ],
        risk_tolerance=0.7,
    )
    runtime = CandidateRuntime()
    registry = AgentRegistry()
    registry.register(entity, runtime)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"大厅": {}},
            actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
        )
    )
    context = {
        "agent_registry": registry,
        "overrides": {},
        "clock": type("Clock", (), {"current_step": 1})(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
        "random_streams": DeterministicRandomStreams(77),
    }

    InputSystem().update({"GameMaster": gm, "甲": entity}, context)

    assert context["policy_traces"]["甲"]["mode"] == "host_sampled"
    assert context["intents"][0]["action"] == context["policy_traces"]["甲"][
        "selected_action"
    ]
    assert "policy_weights" not in str(runtime.perception.private_traits)
    assert "probability" not in context["intents"][0]
    controller = entity.get_component("AgentController")
    assert controller.repeated_policy_action_count == 1
    assert controller.max_repeated_policy_action_count == 1
    assert controller.last_policy_action_signature == (
        CharacterPolicy.repetition_signature(
            AgentAction.from_value(context["intents"][0]["action"])
        )
    )
    assert controller.last_policy_action_target == (
        CharacterPolicy.repetition_target(
            AgentAction.from_value(context["intents"][0]["action"])
        )
    )
