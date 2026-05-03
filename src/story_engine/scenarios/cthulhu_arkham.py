# 克苏鲁风格剧本：1920 年代新英格兰滨海小镇，禁忌知识与不可名状之物。
from src.story_engine.scenarios.config import (
    ScenarioConfig,
    CharacterConfig,
    StoryletConfig,
    StateCondition,
    DramaConfig,
    PlotEntityConfig,
    PlotStageConfig,
)


cthulhu_arkham_scenario = ScenarioConfig(
    name="阿卡姆港的低语 (Whispers in Arkham Harbor)",
    description="1920 年代，马萨诸塞州阿卡姆港。调查员因一桩离奇失踪案来到镇上，逐渐卷入与禁忌典籍、古老崇拜与不可名状存在相关的阴谋。",
    environment="""
    [阿卡姆港，1920 年代]
    新英格兰滨海小镇，雾霭、码头、老式煤气灯与维多利亚式建筑。密斯卡托尼克大学在不远处；镇上有一家老旅馆、一座废弃的码头仓库、以及传闻藏有禁书的私人图书馆。人们说话谨慎，对陌生人既好奇又戒备；夜晚常有奇怪的声响与梦境。

    [GM 参考]
    宇宙中存在人类不应知晓的真相；过度接触禁忌知识会导致理智动摇。某些「存在」不可直呼其名，仅能以暗示、符号或梦境描述。cult 与古老仪式可能真实存在；线索应零散、暧昧，真相逐步浮现而非一次性揭露。
    """,
    rules=[
        "时代与常识：所有角色行为须符合 1920 年代新英格兰的习俗与科技；无无线电、无现代医学常识泛滥。",
        "理智与禁忌：接触禁忌典籍、目睹不可名状或参与邪教仪式时，须描写角色动摇、噩梦或恍惚；GM 可引入「理智检定」式的失败或代价，但不必用数值，以叙述体现。",
        "不可名状：对上古存在、旧日支配者等仅作暗示（如「梦中的巨大轮廓」「碑文上的符号」），避免直呼其名；NPC 若知晓过多会语焉不详或拒绝谈论。",
        "线索与悬念：线索零散、可矛盾；允许 red herring；真相可逐步拼凑，不必一步到位。",
        "结算优先：所有后果先经 Simulation 层形成结构化状态，再由 Rendering 层负责表现。",
    ],
    initial_state="阿卡姆港，秋夜，雾浓。调查员刚抵达老旅馆「海员之眠」；镇上流传着码头仓库附近有人失踪、以及某位收藏家私藏禁书的传闻。旅馆老板神色不安，欲言又止。其余由剧情自然发展。",
    initial_world_objects={
        "海员之眠": {
            "kind": "inn",
            "lighting": "dim",
            "crowd": "sparse",
            "keeper_mood": "uneasy",
        },
        "码头仓库": {
            "kind": "warehouse",
            "state": "sealed",
            "rumor": "disappearances",
            "watch_level": "low",
        },
        "私人图书馆": {
            "kind": "library",
            "state": "quiet",
            "forbidden_shelf": "hidden",
            "access": "restricted",
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
            situation_kinds=["frontstage", "plot_pressure"],
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
            situation_kinds=["plot_pressure"],
            situation_tags=["harbor"],
            conditions=[
                StateCondition(scope="world_object", target="码头仓库", path="state", operator="eq", value="sealed"),
                StateCondition(scope="plot", target="harbor_ritual", path="clock", operator="gte", value=1),
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
    plot_entities=[
        PlotEntityConfig(
            plot_id="harbor_ritual",
            title="港口仪式",
            description="阿卡姆港外缘的某个隐秘团体正在为不祥仪式做准备。",
            clock=0,
            max_clock=4,
            current_stage=0,
            tags=["cult", "harbor"],
            stages=[
                PlotStageConfig(
                    label="潜流",
                    summary="失踪与耳语刚开始连成线索。",
                    pressure_hint="让失踪案与码头仓库的传闻开始互相印证。",
                ),
                PlotStageConfig(
                    label="预兆",
                    summary="更多人开始做同样的怪梦，码头附近出现仪式痕迹。",
                    pressure_hint="用梦境、符号或见不得光的搬运活动提升不安。",
                ),
                PlotStageConfig(
                    label="逼近",
                    summary="邪教行动变得更冒险，调查者可能被盯上。",
                    pressure_hint="让调查者感到自己已被某种力量注意到。",
                ),
                PlotStageConfig(
                    label="开门",
                    summary="仪式接近完成，现实与不可名状之物的边界变薄。",
                    pressure_hint="兑现一次高压危机，但仍保持暗示感。",
                ),
            ],
        ),
        PlotEntityConfig(
            plot_id="forbidden_tome",
            title="禁书余波",
            description="某部不该被翻开的典籍正在悄悄影响知情者。",
            clock=0,
            max_clock=3,
            current_stage=0,
            tags=["book", "sanity"],
            stages=[
                PlotStageConfig(
                    label="封存",
                    summary="禁书仍被隐藏，只有零碎耳语流出。",
                    pressure_hint="用只言片语暗示图书馆里有更危险的东西。",
                ),
                PlotStageConfig(
                    label="泄露",
                    summary="禁书的片段内容已经开始扩散并污染梦境。",
                    pressure_hint="当玩家接近真相时，优先以梦境或怪异引文制造代价。",
                ),
                PlotStageConfig(
                    label="侵蚀",
                    summary="知情者的理智和判断力持续下滑。",
                    pressure_hint="让与禁书相关的线索附带明确的精神代价。",
                ),
            ],
        ),
    ],
    characters=[
        CharacterConfig(
            name="托马斯·韦伯",
            role="旅馆老板 (Innkeeper)",
            personality="谨慎、迷信、对陌生人客气但不愿多谈镇上怪事；偶尔会压低声音提到「不该看的书」或「码头那边的仪式」。",
            goals=[
                "维持旅馆生意，不惹麻烦",
                "对失踪案和禁书有所耳闻但不愿深谈，若被逼问会闪烁其词或转移话题",
            ],
            is_player=False,
            llm_config={
                "system_instruction_extras": "你是 1920 年代新英格兰滨海小镇旅馆老板。你知道镇上有人失踪、有人私藏怪书，但你不愿细说，怕惹祸上身。若被追问，可暗示「去问密大的人」或「别去码头仓库附近」。说话带一点方言与迷信。"
            },
        ),
        CharacterConfig(
            name="调查员",
            role="调查员 (Investigator)",
            personality="理性、好奇、略带紧张；为查案而来，会追问线索但也会因诡异现象而动摇。",
            goals=[
                "查明失踪案与镇上怪事的真相",
                "寻找禁忌典籍或邪教线索，但需警惕理智代价",
            ],
            is_player=True,
        ),
        CharacterConfig(
            name="艾琳·沃斯",
            role="私人图书馆管理员 (Librarian)",
            personality="冷淡、博学、对「不该存在的书」既恐惧又着迷；知道一些禁书的下落但不会轻易透露。",
            goals=[
                "保护某些藏书不被滥用",
                "对真正想了解真相的人会给出晦涩的指引，对轻浮者则闭口不谈",
            ],
            is_player=False,
            llm_config={
                "system_instruction_extras": "你是私人图书馆的管理员，知晓部分禁书与传说。你说话迂回、引用古籍片段，从不直接说出禁忌之名。若调查员表现出诚意与谨慎，可暗示某本书的位置或「不要在有新月时打开」。"
            },
        ),
    ],
)
