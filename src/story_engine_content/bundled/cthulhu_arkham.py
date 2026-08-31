# 克苏鲁风格剧本：1920 年代新英格兰滨海小镇，禁忌知识与不可名状之物。
from src.story_engine.scenarios.config import (
    ScenarioConfig,
    CharacterConfig,
    StoryletConfig,
    StateCondition,
    DramaConfig,
    NeedConfig,
    TraitConfig,
    NarrationConfig,
)


cthulhu_arkham_scenario = ScenarioConfig(
    name="阿卡姆港的低语 (Whispers in Arkham Harbor)",
    description="1920 年代，马萨诸塞州阿卡姆港。调查员因一桩离奇失踪案来到镇上，逐渐卷入与禁忌典籍、古老崇拜与不可名状存在相关的阴谋。",
    default_agent_runtime="hermes",
    environment="""
    [阿卡姆港，1920 年代]
    新英格兰滨海小镇，雾霭、码头、老式煤气灯与维多利亚式建筑。密斯卡托尼克大学在不远处；镇上有一家老旅馆、一座废弃的码头仓库、以及传闻藏有禁书的私人图书馆。人们说话谨慎，对陌生人既好奇又戒备；夜晚常有奇怪的声响与梦境。
    """,
    rules=[
        "时代与常识：所有角色行为须符合 1920 年代新英格兰的习俗与科技；无无线电、无现代医学常识泛滥。",
        "宇宙中存在人类通常无法理解的客观存在，古老仪式与崇拜可能真实产生物理后果。",
        "接触禁忌典籍、目睹异常存在或参与仪式可能造成持续的动摇、噩梦或恍惚；只能依据角色真实经历结算。",
    ],
    narration=NarrationConfig(
        guidance=[
            "使用克制的宇宙恐怖语气；异常存在主要通过轮廓、符号、梦境和可观察余波呈现。",
            "只渲染已经结算的线索，不替玩家拼出尚未知晓的完整真相。",
            "人物应像现实中的人一样谨慎、迟疑或拒绝谈论，而不是主动倾倒设定。",
        ],
    ),
    initial_state="阿卡姆港，秋夜，雾浓。调查员刚抵达老旅馆「海员之眠」；镇上流传着码头仓库附近有人失踪、以及某位收藏家私藏禁书的传闻。旅馆老板神色不安，欲言又止。其余由剧情自然发展。",
    initial_world_objects={
        "海员之眠": {
            "kind": "inn",
            "tags": ["inn", "harbor", "clue"],
            "lighting": "dim",
            "crowd": "sparse",
            "keeper_mood": "uneasy",
            "rear_door_locked": False,
        },
        "码头仓库": {
            "kind": "warehouse",
            "tags": ["warehouse", "harbor", "danger"],
            "state": "sealed",
            "rumor": "disappearances",
            "watch_level": "low",
        },
        "私人图书馆": {
            "kind": "library",
            "tags": ["library", "book", "clue"],
            "state": "quiet",
            "forbidden_shelf": "hidden",
            "access": "restricted",
            "lock_inspected": False,
        },
        "一壶黑咖啡": {
            "is_location": False,
            "kind": "drink",
            "location": "海员之眠",
            "owner": None,
            "hidden": False,
            "portable": True,
            "quantity": 3,
            "affordances": [
                {
                    "id": "drink_coffee",
                    "label": "喝一杯浓黑咖啡",
                    "need_effects": {"疲惫": -0.35},
                    "consumes": True,
                }
            ],
        },
    },
    initial_actor_states={
        "调查员": {"location": "海员之眠", "sanity": "steady", "suspicion": 0},
        "托马斯·韦伯": {"location": "海员之眠", "trust": -1, "fear": 2},
        "艾琳·沃斯": {"location": "私人图书馆", "trust": 0, "guarded": True},
    },
    storylets=[
        StoryletConfig(
            storylet_id="innkeeper_slips_harbor_hint",
            intent="让旅馆老板在压力下吐露关于码头仓库的含糊线索。",
            priority=80,
            one_shot=True,
            tags=["clue", "harbor"],
            situation_kinds=["frontstage"],
            situation_tags=["harbor"],
            conditions=[
                StateCondition(scope="actor", target="托马斯·韦伯", path="fear", operator="gte", value=2),
                StateCondition(scope="actor", target="调查员", path="location", operator="eq", value="海员之眠"),
            ],
        ),
        StoryletConfig(
            storylet_id="library_occult_warning",
            intent="当调查员接近禁书线索时，让图书馆管理员给出警告或晦涩指引。",
            priority=70,
            one_shot=False,
            tags=["clue", "library"],
            situation_kinds=["frontstage"],
            situation_tags=["library"],
            conditions=[
                StateCondition(scope="actor", target="调查员", path="location", operator="eq", value="私人图书馆"),
                StateCondition(scope="world_object", target="私人图书馆", path="forbidden_shelf", operator="eq", value="hidden"),
            ],
        ),
        StoryletConfig(
            storylet_id="warehouse_pressure_rises",
            intent="在张力偏低时，从码头仓库方向制造更直接的危险征兆。",
            priority=60,
            one_shot=False,
            tags=["pressure", "harbor"],
            situation_kinds=["frontstage"],
            situation_tags=["harbor"],
            conditions=[
                StateCondition(scope="world_object", target="码头仓库", path="state", operator="eq", value="sealed"),
            ],
        ),
    ],
    drama=DramaConfig(
        initial_tension=0.48,
        target_min=0.45,
        target_max=0.78,
        crisis_threshold=0.30,
        recovery_bias=0.05,
    ),
    characters=[
        CharacterConfig(
            name="托马斯·韦伯",
            agent_runtime="hermes",
            role="旅馆老板 (Innkeeper)",
            personality="谨慎、迷信、对陌生人客气但不愿多谈镇上怪事；偶尔会压低声音提到「不该看的书」或「码头那边的仪式」。",
            goals=[
                "维持旅馆生意，不惹麻烦",
                "对失踪案和禁书有所耳闻但不愿深谈，若被逼问会闪烁其词或转移话题",
            ],
            initial_beliefs=[
                {"statement": "码头附近的失踪与夜间搬运有关", "confidence": 0.65, "source": "旅客和水手的零碎传闻"},
            ],
            initial_secrets=["他曾在雾夜看见有人把刻有怪异符号的箱子运进仓库"],
            initial_needs=[
                NeedConfig(name="避祸", pressure=0.68, drift_per_turn=0.02, description="避免自己和旅馆卷入港口的危险"),
                NeedConfig(name="疲惫", pressure=0.35, drift_per_turn=0.05, description="守了一整天旅馆后需要休息或提神"),
            ],
            initial_traits=[
                TraitConfig(trait_id="superstitious_caution", intensity=0.85),
                TraitConfig(trait_id="hospitable", intensity=0.45),
            ],
            risk_tolerance=0.2,
            is_player=False,
            llm_config={
                "system_instruction_extras": "你是 1920 年代新英格兰滨海小镇旅馆老板。你知道镇上有人失踪、有人私藏怪书，但你不愿细说，怕惹祸上身。若被追问，可暗示「去问密大的人」或「别去码头仓库附近」。说话带一点方言与迷信。"
            },
        ),
        CharacterConfig(
            name="调查员",
            agent_runtime="hermes",
            role="调查员 (Investigator)",
            personality="理性、好奇、略带紧张；为查案而来，会追问线索但也会因诡异现象而动摇。",
            goals=[
                "查明失踪案与镇上怪事的真相",
                "寻找禁忌典籍或邪教线索，但需警惕理智代价",
            ],
            initial_beliefs=[
                {"statement": "失踪案背后存在尚未被解释的共同模式", "confidence": 0.6, "source": "来镇前收集的案情"},
            ],
            initial_needs=[
                NeedConfig(name="查明真相", pressure=0.62, drift_per_turn=0.035, description="失踪案没有答案时会持续推高行动压力"),
                NeedConfig(name="疲惫", pressure=0.25, drift_per_turn=0.08, description="长时间调查会削弱耐心与判断"),
            ],
            initial_traits=[
                TraitConfig(trait_id="curious", intensity=0.9),
                TraitConfig(trait_id="brave", intensity=0.6),
            ],
            risk_tolerance=0.62,
            is_player=True,
        ),
        CharacterConfig(
            name="艾琳·沃斯",
            agent_runtime="hermes",
            role="私人图书馆管理员 (Librarian)",
            personality="冷淡、博学、对「不该存在的书」既恐惧又着迷；知道一些禁书的下落但不会轻易透露。",
            goals=[
                "保护某些藏书不被滥用",
                "对真正想了解真相的人会给出晦涩的指引，对轻浮者则闭口不谈",
            ],
            initial_beliefs=[
                {"statement": "禁书中的部分内容会改变阅读者的梦境", "confidence": 0.85, "source": "亲身观察"},
            ],
            initial_secrets=["她知道禁书书架的机关，但不会仅因好奇心就透露"],
            initial_needs=[
                NeedConfig(name="守住禁书", pressure=0.72, drift_per_turn=0.03, description="陌生人接近藏书会加剧保护冲动"),
                NeedConfig(name="理解禁忌", pressure=0.48, drift_per_turn=0.02, description="恐惧之外仍无法放弃对禁书的求知欲"),
            ],
            initial_traits=[
                TraitConfig(trait_id="scholarly", intensity=0.9),
                TraitConfig(trait_id="fearful_of_forbidden", intensity=0.65),
            ],
            risk_tolerance=0.38,
            activation_policy="background",
            background_interval=2,
            is_player=False,
            llm_config={
                "system_instruction_extras": "你是私人图书馆的管理员，知晓部分禁书与传说。你说话迂回、引用古籍片段，从不直接说出禁忌之名。若调查员表现出诚意与谨慎，可暗示某本书的位置或「不要在有新月时打开」。"
            },
        ),
    ],
)
