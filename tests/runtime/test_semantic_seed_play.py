from tests.semantic_support import action_reply, narration_check_reply
import json

import pytest

from src.story_engine.agents import HermesCharacterAgent, default_offline_runtime_factories
from src.story_engine.session import (
    ScenarioSeedError,
    bind_play_profile,
    compile_play_seed,
    create_session,
)
from src.story_engine.web.adapter import WebGameAdapter


class _Provider:
    def __init__(self, facts, verdict=None):
        self.facts = facts
        self.verdict = verdict or {"valid": True, "issues": []}
        self.calls = 0

    def generate(self, prompt, system_prompt=None):
        self.calls += 1
        assert system_prompt and "evidence" in system_prompt
        if prompt.startswith("## 初始设定语义校对"):
            return {"content": json.dumps(self.verdict)}
        return {"content": json.dumps(self.facts, ensure_ascii=False)}


def test_prose_seed_creates_grounded_world_and_runs_first_turn():
    seed = "在雪山小屋里，甲和乙是姐妹。甲藏着一把钥匙，她知道山路已经封死。乙想下山。"
    provider = _Provider({
        "locations": [{"name": "雪山小屋", "evidence": "雪山小屋"}],
        "characters": [
            {"name": "甲", "location": "雪山小屋", "evidence": "甲和乙是姐妹"},
            {"name": "乙", "location": "雪山小屋", "goals": ["下山"], "evidence": "乙想下山"},
        ],
        "objects": [{"name": "钥匙", "owner": "甲", "evidence": "一把钥匙"}],
        "claims": [{"subjects": ["甲"], "known_by": ["甲"], "evidence": "山路已经封死"}],
        "relationships": [{"source": "甲", "target": "乙", "evidence": "甲和乙是姐妹"}],
    })

    scenario = compile_play_seed(seed, provider=provider)

    assert provider.calls == 2
    assert scenario.metadata["seed_compiler"] == "semantic-grounded-v1"
    assert set(scenario.initial_actor_states) == {"甲", "乙"}
    assert scenario.characters[0].goals == []
    assert scenario.characters[1].goals == ["下山"]
    assert scenario.initial_world_objects["钥匙"]["owner"] == "甲"
    assert len(scenario.claims) == 1
    assert scenario.claims[0].statement == "山路已经封死"
    assert scenario.characters[0].initial_claim_knowledge[0].claim_id == scenario.claims[0].claim_id
    assert scenario.initial_relationships[0].participants == ["甲", "乙"]
    assert scenario.private_author_premise == seed
    for public_text in (scenario.description, scenario.environment, scenario.initial_state):
        assert "山路已经封死" not in public_text
        assert "甲藏着一把钥匙" not in public_text

    session = create_session(
        bind_play_profile(scenario, "offline"),
        agent_runtime_factories=default_offline_runtime_factories(),
        random_seed="semantic-seed-turn",
    )
    try:
        result = session.run_step(overrides={"甲": "观察小屋。"})
        assert result["step_committed"] is True
        assert session.step_count == 1
        assert session.runner.agent_boundary_errors() == []
    finally:
        session.close()


def test_every_character_secret_premise_requires_private_world_state():
    seed = "三个陌生人被困在一座雪山小屋里，每个人都有秘密。"
    provider = _Provider({
        "locations": [{"name": "雪山小屋", "evidence": "雪山小屋"}],
        "characters": [
            {"name": name, "role": "陌生人", "location": "雪山小屋", "evidence": "三个陌生人"}
            for name in ("陌生人甲", "陌生人乙", "陌生人丙")
        ],
    }, verdict={"valid": False, "issues": ["one secret per character required by author"]})

    with pytest.raises(ScenarioSeedError, match="one secret per character"):
        compile_play_seed(seed, provider=provider)


def test_semantic_seed_retries_a_rejected_extraction_before_creating_world():
    seed = "甲在小屋里，每个人都有秘密。"
    base = {
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [{"name": "甲", "location": "小屋", "evidence": "甲在小屋里"}],
    }

    class CorrectingProvider:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, system_prompt=None):
            self.calls += 1
            if prompt.startswith("## 初始设定语义校对"):
                return {"content": json.dumps({"valid": self.calls >= 4, "issues": [] if self.calls >= 4 else ["one secret per character required"]})}
            if self.calls == 1:
                return {"content": json.dumps(base, ensure_ascii=False)}
            assert "one secret per character" in system_prompt
            return {"content": json.dumps({
                **base,
                "generated_secrets": [{
                    "owner": "甲",
                    "statement": "甲曾背叛过一位旧友。",
                    "evidence": "每个人都有秘密",
                }],
            }, ensure_ascii=False)}

    provider = CorrectingProvider()
    scenario = compile_play_seed(seed, provider=provider)

    assert provider.calls == 4
    assert scenario.claims[0].statement == "甲曾背叛过一位旧友。"


def test_authorized_creative_secrets_become_private_grounded_claims():
    seed = "三个陌生人被困在一座雪山小屋里，每个人都有秘密。"
    actors = ("陌生人甲", "陌生人乙", "陌生人丙")
    provider = _Provider({
        "locations": [{"name": "雪山小屋", "evidence": "雪山小屋"}],
        "characters": [
            {"name": actor, "role": "陌生人", "location": "雪山小屋", "evidence": "三个陌生人"}
            for actor in actors
        ],
        "generated_secrets": [
            {
                "owner": actor,
                "statement": statement,
                "evidence": "每个人都有秘密",
            }
            for actor, statement in zip(actors, (
                "陌生人甲曾背叛一位旧友。",
                "陌生人乙暗中怀疑陌生人甲。",
                "陌生人丙隐瞒了自己的身份。",
            ))
        ],
    })

    scenario = compile_play_seed(seed, provider=provider)

    assert len(scenario.claims) == 3
    assert all(claim.tags == ["generated_seed"] for claim in scenario.claims)
    assert all(claim.visibility == "secret" for claim in scenario.claims)
    for actor in scenario.characters:
        assert len(actor.initial_claim_knowledge) == 1
        own_claim = next(
            claim for claim in scenario.claims
            if claim.claim_id == actor.initial_claim_knowledge[0].claim_id
        )
        assert own_claim.subjects == [actor.name]
        assert own_claim.statement not in scenario.initial_state

    player = actors[0]
    player_secret = scenario.claims[0].statement
    other_secrets = [claim.statement for claim in scenario.claims[1:]]
    adapter = WebGameAdapter(
        bind_play_profile(scenario, "offline"),
        agent_runtime_factories=default_offline_runtime_factories(),
    )
    try:
        state = adapter.get_state()
        assert state["player"]["name"] == player
        assert state["player"]["decision_context"]["known_claims"] == [
            {"statement": player_secret, "stance": "supports"}
        ]
        assert all(secret not in json.dumps(state, ensure_ascii=False) for secret in other_secrets)
    finally:
        adapter._session.close()


def test_semantic_seed_rejects_hallucinated_or_unplaced_facts():
    seed = "甲在小屋里。"
    false_location = _Provider({
        "locations": [{"name": "地宫", "evidence": "地宫"}],
        "characters": [{"name": "甲", "evidence": "甲在小屋里"}],
    })
    with pytest.raises(ScenarioSeedError, match="verbatim source span"):
        compile_play_seed(seed, provider=false_location)

    missing_location = _Provider({
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [{"name": "甲", "location": "地宫", "evidence": "甲在小屋里"}],
    })
    with pytest.raises(ScenarioSeedError, match="unknown location"):
        compile_play_seed(seed, provider=missing_location)

    unplaced_actor = _Provider({
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [{"name": "甲", "evidence": "甲在小屋里"}],
    })
    with pytest.raises(ScenarioSeedError, match="confirmed location"):
        compile_play_seed(seed, provider=unplaced_actor)

    invented_goal = _Provider({
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [{
            "name": "甲", "location": "小屋", "goals": ["篡位"],
            "evidence": "甲在小屋里",
        }],
    }, verdict={"valid": False, "issues": ["detail lacks source evidence"]})
    with pytest.raises(ScenarioSeedError, match="detail lacks source evidence"):
        compile_play_seed(seed, provider=invented_goal)


def test_model_outage_does_not_create_a_generic_world():
    class Unavailable:
        def generate(self, prompt, system_prompt=None):
            return {"content": "[LLM disabled] missing key"}

    with pytest.raises(ScenarioSeedError, match="unavailable"):
        compile_play_seed("甲被困在雪山小屋里。", provider=Unavailable())


def test_structured_seed_uses_deterministic_path_without_model_call():
    class MustNotCall:
        def generate(self, prompt, system_prompt=None):
            raise AssertionError("structured seed should not call a model")

    scenario = compile_play_seed(
        "地点：雪山小屋\n角色：甲|旅客|警觉|求生|地点=雪山小屋",
        provider=MustNotCall(),
    )
    assert scenario.metadata["seed_compiler"] == "deterministic-v1"


def test_prose_only_json_seed_uses_semantic_path_and_keeps_title():
    premise = "甲在小屋里等候。"
    provider = _Provider({
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [{"name": "甲", "location": "小屋", "evidence": premise}],
    })

    scenario = compile_play_seed(
        json.dumps({"name": "夜访", "premise": premise}, ensure_ascii=False),
        provider=provider,
    )

    assert scenario.name == "夜访"
    assert scenario.private_author_premise == premise
    assert scenario.initial_state == "你身处小屋。"
    assert provider.calls == 2


def test_full_model_path_commits_narrates_and_registers_future_director_direction():
    source = "甲和乙被困在小屋里。"
    seed_provider = _Provider({
        "locations": [{"name": "小屋", "evidence": "小屋"}],
        "characters": [
            {"name": "甲", "location": "小屋", "evidence": "甲和乙被困在小屋里"},
            {"name": "乙", "location": "小屋", "evidence": "甲和乙被困在小屋里"},
        ],
    })
    scenario = compile_play_seed(source, provider=seed_provider)
    scenario = scenario.model_copy(update={
        "characters": [
            scenario.characters[0],
            scenario.characters[1].model_copy(update={"activation_policy": "foreground"}),
        ],
    })
    conversations = {}

    class Conversation:
        def __init__(self, agent_id):
            self.agent_id = agent_id
            self.packets = []

        def run_subject_turn(self, packet):
            self.packets.append(packet)
            return {
                "protocol_version": 1,
                "agent_id": self.agent_id,
                "content": json.dumps({"action": "等待并观察小屋。"}, ensure_ascii=False),
            }

    def runtime_factory(entity, config):
        return HermesCharacterAgent(
            lambda actor, _: conversations.setdefault(actor.name, Conversation(actor.id)),
            config,
        )

    class Settlement:
        def generate(self, prompt):
            if reply := action_reply(prompt):
                return reply
            if reply := narration_check_reply(prompt):
                return reply
            if prompt.startswith("## 提交前语义校对"):
                return {"content": json.dumps({"valid": True, "issues": []})}
            intents = json.loads(
                prompt.split("## 本轮意图\n", 1)[1].split("\n## 宿主允许", 1)[0]
            )
            topology_authorizations = json.loads(
                prompt.split("## 本轮宿主签发的空间图新增授权\n", 1)[1]
                .split("\n## 本轮已触发故事块", 1)[0]
            )
            payload = {
                "resolved_actions": [
                    {
                        "actor": item["actor"],
                        "outcome": "inactive" if item.get("source_storylet_id") else "success",
                        "location": item["location"],
                        "source_storylet_id": item.get("source_storylet_id", ""),
                        "result": "" if item.get("source_storylet_id") else f"{item['actor']}停留在原地。",
                        "visibility": "local",
                    }
                    for item in intents
                ],
                "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
            }
            if topology_authorizations:
                payload["topology_candidate"] = {
                    "authorization_id": topology_authorizations[0]["authorization_id"]
                }
            return {"content": json.dumps(payload, ensure_ascii=False)}

    class Narrator:
        def generate(self, prompt):
            if reply := action_reply(prompt):
                return reply
            if reply := narration_check_reply(prompt):
                return reply
            assert "甲和乙被困在小屋里" not in prompt
            return {"content": "小屋里一时安静下来。"}

    class Director:
        def __init__(self):
            self.calls = 0

        def generate_messages(self, messages, **kwargs):
            assert "甲和乙被困在小屋里" in messages[0]["content"]
            self.calls += 1
            if self.calls == 1:
                return {"content": "", "tool_calls": [{
                    "id": "proposal-1", "type": "function", "function": {
                        "name": "propose_storylet", "arguments": json.dumps({
                            "reason": "探索可能的出口", "storylet": {
                                "storylet_id": "exit_clue", "intent": "小屋里存在指向出口的线索",
                                "trigger": "玩家开始探索出口",
                            },
                        }, ensure_ascii=False),
                    },
                }]}
            return {"content": "等待玩家的探索。"}

    session = create_session(
        scenario,
        agent_runtime_factories={"hermes": runtime_factory},
        random_seed="full-model-path",
    )
    gm = session.entities["WorldHost"]
    gm.get_component("SimulationControl")._llm = Settlement()
    gm.get_component("NarrativeRenderer")._llm = Narrator()
    gm.get_component("StoryPlanner")._llm = Director()
    gm.get_component("StoryPlanner").interval_turns = 1
    try:
        result = session.run_step(overrides={"甲": "等待。"})
        assert result["step_committed"] is True
        assert result["rendered_text"]
        assert len(result["simulation_result"]["resolved_actions"]) >= 2
        assert conversations["乙"].packets
        scene = gm.get_component("SceneState")
        pending = scene.get_scene_flag("pending_story_planner_proposals")
        assert pending[0]["payload"]["storylet_id"] == "exit_clue"
        assert pending[0]["status"] == "accepted"
        assert "山道" not in scene.world_objects
        assert scene.get_scene_flag("dynamic_storylet_ids") == ["exit_clue"]

        next_step = session.run_step(overrides={"甲": "继续等待。"})
        assert next_step["step_committed"] is True
        assert next_step["storylet_definition_authorizations"] == []
        assert scene.get_scene_flag("dynamic_storylet_ids") == ["exit_clue"]
        director = gm.get_component("StoryPlanner")
        assert result["rendered_text"] in [item["content"] for item in director.narrative_messages]
        assert director.narrative_messages[-2]["content"] == "继续等待。"
    finally:
        session.close()


def test_prose_physical_attributes_are_extracted_by_model_and_preserved():
    provider = _Provider({
        'locations': [{'name': '房间', 'evidence': '房间'}],
        'characters': [{'name': '甲', 'location': '房间', 'is_player': True, 'evidence': '甲在房间里'}],
        'objects': [{'name': '铁门', 'kind': 'door', 'location': '房间',
                     'portable': False, 'attributes': {'locked': True, 'open': False},
                     'evidence': '铁门固定在房间里，锁着且关着'}],
    })
    scenario = compile_play_seed('甲在房间里。铁门固定在房间里，锁着且关着。', provider=provider)
    door = scenario.initial_world_objects['铁门']
    assert door['portable'] is False
    assert door['locked'] is True
    assert door['open'] is False


def test_seed_semantics_accepts_group_authorization_without_secret_keywords():
    source = '两位无名旅人在山舍歇脚，各自都有一桩尚未透露的往事。'
    facts = {
        'locations': [{'name': '山舍', 'evidence': '山舍'}],
        'characters': [{'name': name, 'location': '山舍', 'evidence': '两位无名旅人在山舍歇脚'}
                       for name in ('旅人甲', '旅人乙')],
        'generated_secrets': [
            {'owner': '旅人甲', 'statement': '旅人甲曾出卖过一位朋友。', 'evidence': '各自都有一桩尚未透露的往事'},
            {'owner': '旅人乙', 'statement': '旅人乙曾冒用过他人身份。', 'evidence': '各自都有一桩尚未透露的往事'},
        ],
    }
    scenario = compile_play_seed(source, provider=_Provider(facts))
    assert len(scenario.characters) == 2
    assert len(scenario.claims) == 2
    assert all(c.visibility == 'secret' for c in scenario.claims)
    assert all(c.initial_claim_knowledge for c in scenario.characters)


def test_seed_semantic_review_accepts_faithful_personality_paraphrase():
    source = '甲在小屋，每一步都小心翼翼。'
    facts = {'locations': [{'name': '小屋', 'evidence': '小屋'}],
             'characters': [{'name': '甲', 'location': '小屋', 'personality': '谨慎',
                             'evidence': '甲在小屋，每一步都小心翼翼'}]}
    scenario = compile_play_seed(source, provider=_Provider(facts))
    assert scenario.characters[0].personality == '谨慎'
