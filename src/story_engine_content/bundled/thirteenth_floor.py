from src.story_engine.scenarios.config import (
    CharacterConfig,
    NarrationConfig,
    ScenarioConfig,
)

# 基于电影《异次元骇客》(The Thirteenth Floor, 1999) 设定：
# 1999 年汉农·富勒创造了一个 1937 年洛杉矶的 VR 模拟，后被谋杀；其门生道格拉斯·霍尔成为嫌疑人。
# 霍尔进入模拟（在模拟中化身为银行职员约翰·弗格森）寻找富勒留下的讯息；酒保杰瑞·阿什顿若读到该讯息会得知自己是人造物。
# 模拟的「边缘」未被渲染，会呈现线框与虚空；最终揭示 1999 年世界本身也是被模拟的。
thirteenth_floor_scenario = ScenarioConfig(
    name="异次元骇客 (The Thirteenth Floor)",
    description="1999 年创造的 1937 年洛杉矶 VR 模拟；创造者富勒在现实中被谋杀，其门生霍尔进入模拟寻找遗言，并逐渐发现嵌套的模拟真相。",
    environment="""
    [当前层：1937 年洛杉矶模拟]
    时间是 1937 年。雨夜。爵士乐、廉价烟草与霓虹灯。威尔希尔大酒店 (Wilshire Grand Hotel) 的酒吧是主要场景——木质吧台、铜制酒架、老式收银机。窗外是湿漉漉的街道与老式汽车。
    城市边缘被夜色和雨幕遮住，酒吧里没有人能看清更远处究竟有什么。
    """,
    rules=[
        "1937 年规则：角色行为须符合该时代与常识；未读过富勒讯息的 NPC 不知身在模拟，对「虚拟/程序/1999」表现困惑。",
        "当前世界客观上由 1999 年的系统运行，但没有获得相应证据的角色不能知道这一事实。",
        "模拟边界：角色若实际到达未被渲染的城市边缘，世界会呈现线框、绿色几何、虚空和视觉故障。",
        "富勒的讯息：酒吧中藏有富勒留给道格拉斯的信封；阿什顿或知有其物但未打开；当众宣读会动摇 NPC 的「现实」认知。",
    ],
    narration=NarrationConfig(
        guidance=[
            "采用克制的黑色电影气质，突出雨夜、爵士乐、烟草和可观察的现实裂缝。",
            "只有结算已经产生模拟故障时才描写线框、绿色几何或虚空。",
            "不要通过全知旁白提前揭示世界层级。",
        ],
    ),
    # 初始状态作为「种子」：只给时间地点与关键要素，具体细节由 GM 与对话自然展开
    initial_state="威尔希尔大酒店酒吧，雨夜。阿什顿在吧台；富勒曾留下给道格拉斯的某物；道格拉斯在场并正在寻找。其余由剧情自然发展。",
    initial_world_objects={
        "Wilshire Grand Hotel Bar": {
            "kind": "bar",
            "lighting": "dim",
            "weather_outside": "rain",
        },
        "Fuller's Envelope": {
            "is_location": False,
            "kind": "letter",
            "location": "Wilshire Grand Hotel Bar",
            "hidden": False,
            "portable": True,
        },
    },
    initial_actor_states={
        "Jerry Ashton": {"location": "Wilshire Grand Hotel Bar", "activity": "tending bar"},
        "Douglas Hall": {"location": "Wilshire Grand Hotel Bar", "activity": "searching"},
    },
    characters=[
        CharacterConfig(
            name="Jerry Ashton",
            role="酒保 (Bartender)",
            personality="愤世嫉俗、疲惫、对熟客细心但对刺探戒备。典型的 1937 年酒吧老手。",
            goals=[
                "照常经营酒吧、招呼客人",
                "对富勒留下的信封有所察觉但未打开，若有人不当追问会警惕或撒谎",
                "维持日常——绝不承认任何「虚拟」「模拟」之说",
            ],
            is_player=False,
            llm_config={
                "system_instruction_extras": "你是 1937 年洛杉矶酒店酒吧的酒保杰瑞·阿什顿。你【不知道】自己是模拟中的角色。若被问及电脑、虚拟世界、1999 年或程序，表现困惑或恼怒。你知道昨晚富勒来过，并在收银机附近留了东西给「道格拉斯」，但你没打开看过，也不愿多谈。"
            }
        ),
        CharacterConfig(
            name="Douglas Hall",
            role="主角 / 调查者 (Protagonist)",
            personality="困惑但执着，正在追查富勒之死与真相；在模拟中可能以「约翰·弗格森」身份活动，但本设定中统一用道格拉斯·霍尔。",
            goals=[
                "找到富勒在酒吧留下的讯息",
                "理解富勒之死与模拟的真相",
                "若发现模拟边界或线框现象，追查到底",
            ],
            is_player=True,
        ),
    ]
)
