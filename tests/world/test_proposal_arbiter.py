from src.story_engine.simulation import ProposalArbiter


def test_manual_player_action_is_anchor_but_auto_player_is_only_a_proposal():
    arbiter = ProposalArbiter()
    manual = {
        "actor": "玩家",
        "intent": "打开门",
        "source": "manual",
        "proposal_role": "player_override",
        "proposal_priority": 1.0,
    }
    automatic = {
        **manual,
        "source": "ai",
        "proposal_role": "character_proposal",
        "proposal_priority": 0.48,
    }

    manual_packet = arbiter.build_focus_packet(
        [manual], "玩家", manual, {}, {}
    )
    auto_packet = arbiter.build_focus_packet(
        [automatic], "玩家", automatic, {}, {}
    )

    assert manual_packet["anchor_intent"]["intent"] == "打开门"
    assert manual_packet["player_override_active"] is True
    assert auto_packet["anchor_intent"] == {}
    assert auto_packet["player_proposal"]["intent"] == "打开门"


def test_proposals_are_ranked_by_priority_with_stable_tie_order():
    intents = [
        {"actor": "甲", "intent": "甲动作", "proposal_priority": 0.4},
        {"actor": "乙", "intent": "乙动作", "proposal_priority": 0.8},
        {"actor": "丙", "intent": "丙动作", "proposal_priority": 0.8},
        {"actor": "丁", "intent": "丁动作", "proposal_priority": 0.2},
    ]

    packet = ProposalArbiter().build_focus_packet(intents, None, None, {}, {})

    assert [item["actor"] for item in packet["proposals"]] == ["乙", "丙", "甲", "丁"]
    assert packet["proposal_semantics"] == "simultaneous"
