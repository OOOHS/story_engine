import re
from typing import Dict, Any
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity


class InputSystem(System):
    """
    Collect free-form intents from the player and autonomous actors.
    This is the Input phase of the engine loop.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        dispatcher = context.get("dispatcher")
        intents_buffer = context.setdefault("intents", [])
        scene_state = self._get_scene_state(entities)
        player_name = context.get("player_name")
        player_location = scene_state.get_actor_location(player_name) if scene_state and player_name else None

        for event in context.get("inject_events", []):
            intents_buffer.append(
                {
                    "actor": "World",
                    "intent": event,
                    "thought": "",
                    "source": "injected",
                    "location": player_location,
                    "proposal_role": "world_event",
                    "proposal_priority": 0.9,
                }
            )

        self._inject_commitment_events(scene_state, context, intents_buffer, player_location)

        ordered_entities = self._order_entities(entities, player_name)
        for name, entity in ordered_entities:
            if entity.get_component("SimulationControl") or entity.get_component("NarrativeControl"):
                continue

            intent = None
            thought = ""
            source = "ai"
            actor_location = scene_state.get_actor_location(name) if scene_state else None
            is_player = bool(player_name and name == player_name)

            # Off-screen NPCs stay in the background by default. They can still be advanced
            # indirectly by plots/director pressure inside Simulation, but they do not enter
            # the foreground action list every turn.
            if (not is_player) and player_location and actor_location and actor_location != player_location:
                continue

            if name in overrides:
                intent = overrides[name]
                source = "manual"
                print(f"> {name} (INTENT/MANUAL): {intent}")
                self.logger.info(f"{name} (INTENT/MANUAL): {intent}")
            elif is_player and context.get("allow_auto_player") is False:
                # Players default to the same autonomous proposal loop as other actors.
                # The only special case is an explicit manual override for the turn.
                continue
            else:
                persona = entity.get_component("Persona")
                if not persona or not hasattr(persona, "act"):
                    continue

                print(f"... {name} is thinking ...", end="\r", flush=True)
                immediate_context = (
                    self._build_player_auto_context(
                        actor_name=name,
                        scene_state=scene_state,
                        intents_buffer=intents_buffer,
                        current_step=(context.get("clock").current_step if context.get("clock") else 0),
                    )
                    if is_player
                    else self._build_immediate_context(
                        actor_name=name,
                        scene_state=scene_state,
                        intents_buffer=intents_buffer,
                        player_name=player_name,
                        player_location=player_location,
                    )
                )
                result = persona.act(immediate_context=immediate_context)
                print(" " * 50 + "\r", end="", flush=True)

                if isinstance(result, dict):
                    thought = result.get("thought", "")
                    intent = result.get("action", "")
                else:
                    intent = str(result)

                if is_player:
                    intent = self._stabilize_player_auto_intent(
                        intent=intent,
                        scene_state=scene_state,
                        player_name=player_name,
                        current_step=(context.get("clock").current_step if context.get("clock") else 0),
                    )
                    intent = self._sanitize_auto_intent(intent, is_player=True)
                else:
                    intent = self._stabilize_npc_auto_intent(
                        intent=intent,
                        actor_name=name,
                        scene_state=scene_state,
                        player_name=player_name,
                        current_step=(context.get("clock").current_step if context.get("clock") else 0),
                    )
                    intent = self._sanitize_auto_intent(intent, is_player=False)

                if thought:
                    print(f"{name} (Thought): {thought}")
                    self.logger.info(f"{name} (Thought): {thought}")

                if intent:
                    print(f"> {name} (INTENT): {intent}")
                    self.logger.info(f"{name} (INTENT): {intent}")

            if not intent:
                continue

            intent_record = {
                "actor": name,
                "intent": intent,
                "thought": thought,
                "source": source,
                "location": actor_location,
                "is_player": is_player,
                "proposal_role": self._proposal_role(is_player=is_player, source=source),
                "proposal_priority": self._proposal_priority(is_player=is_player, source=source),
            }
            intents_buffer.append(intent_record)
            if dispatcher:
                dispatcher.publish({"type": "intent", "agent": name, "content": intent})

    def _get_scene_state(self, entities: Dict[str, Entity]):
        for entity in entities.values():
            if entity.get_component("SimulationControl"):
                return entity.get_component("SceneState")
        return None

    def _order_entities(self, entities: Dict[str, Entity], player_name: Any):
        ordered = list(entities.items())
        if not player_name:
            return ordered

        def sort_key(item: Any):
            name, entity = item
            if entity.get_component("SimulationControl") or entity.get_component("NarrativeControl"):
                return (2, 0)
            if name == player_name:
                return (0, 0)
            return (1, 0)

        return sorted(ordered, key=sort_key)

    def _inject_commitment_events(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        intents_buffer: Any,
        player_location: Any,
    ) -> None:
        if not scene_state:
            return

        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        for item in scene_state.get_scene_flag("upcoming_commitments", []):
            if not isinstance(item, dict):
                continue
            if int(item.get("due_step", -1)) != current_step:
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue

            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            content = f"{title}：{summary}" if title and summary else (summary or title)
            if not content:
                continue

            intents_buffer.append(
                {
                    "actor": "World",
                    "intent": content,
                    "thought": "",
                    "source": "timeline",
                    "location": item.get("location") or player_location,
                    "proposal_role": "world_pressure",
                    "proposal_priority": 0.78,
                }
            )

    def _build_immediate_context(
        self,
        actor_name: Any,
        scene_state: Any,
        intents_buffer: Any,
        player_name: Any,
        player_location: Any,
    ) -> str:
        if not scene_state or not player_name or actor_name == player_name:
            return ""

        actor_location = scene_state.get_actor_location(actor_name)
        if player_location and actor_location and actor_location != player_location:
            return ""

        spatial_lines = []
        actor_pov = scene_state.get_view_pov(actor_name)
        visible_spatial_facts = actor_pov.get("visible_spatial_facts", []) if isinstance(actor_pov, dict) else []
        actor_state = scene_state.get_actor_state(actor_name) if scene_state else {}
        if visible_spatial_facts:
            spatial_lines.append("当前站位：")
            spatial_lines.extend(visible_spatial_facts[:4])

        pressure_lines = []
        if isinstance(actor_state, dict):
            dramatic_motive = str(actor_state.get("dramatic_motive", "")).strip()
            pressure_profile = str(actor_state.get("pressure_profile", "")).strip()
            public_lever = str(actor_state.get("public_lever", "")).strip()
            if dramatic_motive:
                pressure_lines.append(f"你此刻最想达成：{dramatic_motive}")
            if pressure_profile:
                pressure_lines.append(f"你惯用的出手方式：{pressure_profile}")
            if public_lever:
                pressure_lines.append(f"你最顺手的施压点：{public_lever}")
            if player_name in (actor_pov.get("visible_actors", []) or []):
                pressure_lines.append(f"{player_name}现在就在你面前。")
                if pressure_profile in {"white_lotus", "order_control", "polite_comparison", "cutoff_guard", "shielding"}:
                    pressure_lines.append("这轮不要只观察，请立刻用你最顺手的方式先出手。")

        visible_current_events = []
        for item in intents_buffer:
            source = item.get("source")
            if item.get("actor") == actor_name:
                continue
            if item.get("location") and actor_location and item.get("location") != actor_location:
                continue
            if item.get("actor") == player_name:
                visible_current_events.append(f"玩家刚明确表示：{item.get('intent', '')}")
            elif source in {"timeline", "injected"}:
                visible_current_events.append(f"当前场上变化：{item.get('intent', '')}")

        return "\n".join(pressure_lines + spatial_lines + visible_current_events[:3])

    def _build_player_auto_context(
        self,
        actor_name: Any,
        scene_state: Any,
        intents_buffer: Any,
        current_step: int,
    ) -> str:
        if not scene_state or not actor_name:
            return ""

        player_pov = scene_state.get_view_pov(actor_name)
        visible_actor_states = player_pov.get("visible_actor_states", {}) or {}
        visible_spatial_facts = player_pov.get("visible_spatial_facts", []) or []
        pressure_names = []
        for name, state in visible_actor_states.items():
            if name == actor_name or not isinstance(state, dict):
                continue
            if state.get("bias") or state.get("framing_style") or state.get("territorial") or any(
                key.startswith(("malice_", "trust_")) for key in state.keys()
            ):
                pressure_names.append(name)

        current_events = []
        for item in intents_buffer or []:
            if not isinstance(item, dict):
                continue
            if item.get("actor") == actor_name:
                continue
            if item.get("source") in {"timeline", "injected"}:
                current_events.append(f"当前变化：{item.get('intent', '')}")

        guidance = [
            "AUTO GUIDANCE:",
            "- 你也是场中的一个真实角色，不要把自己演成背景板。",
            "- 若场上有人明显偏心或拿规矩压你，优先选择占位、发问、先手落座、试探或轻微反击。",
            "- 不要连续几轮都只做“安静吃饭/低头不语/不参与交流”这类动作。",
            "- 若这轮没人替你争位置，你可以自己先争位置。",
            f"- 当前回合：{current_step}",
        ]
        if pressure_names:
            guidance.append(f"- 当前对你有明显压力的人：{', '.join(pressure_names[:4])}")
        if visible_spatial_facts:
            guidance.append("当前站位：")
            guidance.extend(visible_spatial_facts[:5])
        guidance.extend(current_events[:2])
        return "\n".join(guidance)

    def _stabilize_player_auto_intent(
        self,
        intent: str,
        scene_state: Any,
        player_name: Any,
        current_step: int,
    ) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized or not scene_state or not player_name:
            return normalized

        passive_patterns = [
            "先观察局势",
            "先看情况",
            "按兵不动",
            "安静地用餐",
            "安静用餐",
            "开始用餐",
            "目光低垂",
            "不参与任何交流",
            "专注于自己碗里",
            "低头吃饭",
            "沉默地吃",
        ]
        if not any(pattern in normalized for pattern in passive_patterns):
            return normalized

        player_pov = scene_state.get_view_pov(player_name)
        visible_actor_states = player_pov.get("visible_actor_states", {}) or {}
        location = str(player_pov.get("location") or "")
        pressure_targets = [
            name
            for name, state in visible_actor_states.items()
            if name != player_name
            and isinstance(state, dict)
            and (
                state.get("framing_style")
                or state.get("territorial")
                or state.get("bias")
                or any(key.startswith("malice_") for key in state.keys())
            )
        ]
        primary_target = pressure_targets[0] if pressure_targets else ""
        if not primary_target and current_step <= 0:
            return normalized

        if "餐厅" in location:
            alternatives = [
                "直接拉开空着的椅子坐下，抬眼看他们谁先出声。",
                f"看着{primary_target}，当面问一句这位置是不是特意留给你的。" if primary_target else "",
                "落座前先伸手碰了碰面前的杯子，问一句谁在替你安排顺序。",
                "没有等人招呼，先坐到桌边，再慢慢看清谁先被照顾。",
            ]
        else:
            alternatives = [
                f"看着{primary_target}，直接问她是不是打算一直替你安排位置。" if primary_target else "",
                "没有立刻顺着催促走，而是先问清这顿饭准备把你放在哪个位置上。",
                "朝餐厅方向走了一步，却先把目光落回众人脸上，等谁先拿规矩压你。",
            ]
        alternatives = [item for item in alternatives if item]
        if not alternatives:
            return normalized
        return alternatives[current_step % len(alternatives)]

    def _stabilize_npc_auto_intent(
        self,
        intent: str,
        actor_name: Any,
        scene_state: Any,
        player_name: Any,
        current_step: int,
    ) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized or not scene_state or not actor_name or not player_name:
            return normalized
        if not self._is_passive_observe_intent(normalized):
            return normalized

        actor_location = scene_state.get_actor_location(actor_name)
        player_location = scene_state.get_actor_location(player_name)
        if not actor_location or actor_location != player_location:
            return normalized

        actor_state = scene_state.get_actor_state(actor_name)
        if not isinstance(actor_state, dict):
            return normalized

        profile = str(actor_state.get("pressure_profile", "")).strip()
        day_phase = str(scene_state.get_scene_flag("day_phase", "")).strip()
        location = str(actor_location).strip()
        player_target = str(player_name).strip()

        alternatives = []
        if profile == "white_lotus":
            if location == "餐厅":
                alternatives = [
                    f"看着{player_target}温声开口，说主位对面那边平时不是这么坐的。",
                    f"伸手把{player_target}面前的杯子轻轻挪了挪，笑着说别一回来就坐错位置。",
                    f"先替{player_target}圆一句场，说她刚回家还不懂家里的顺序。",
                    f"柔声提醒{player_target}先别急着动筷，说这道菜平时是先给长辈夹的。",
                ]
            else:
                alternatives = [
                    f"朝{player_target}走近半步，笑着说她一个人站着会不自在，先跟大家入席吧。",
                    f"先替{player_target}解释一句，说她刚回来还不懂家里的顺序。",
                    f"柔声催{player_target}跟着大家走，说别让长辈久等。",
                ]
        elif profile == "order_control":
            alternatives = [
                f"沉声提醒{player_target}先按家里的位置坐，别在饭桌前失了规矩。",
                f"不轻不重地点了{player_target}一句，说现在不是由着性子来的时候。",
                f"先把场子定住，直接让{player_target}照规矩来。",
            ]
        elif profile == "polite_comparison":
            alternatives = [
                f"温声招呼沈昭宁先坐稳，又顺手把{player_target}引到靠边的位置。",
                f"轻声说了句大家都在等着，顺势把{player_target}往次位带。",
                f"像是在照顾{player_target}，却先把更体面的位置留给了沈昭宁。",
            ]
        elif profile == "cutoff_guard":
            alternatives = [
                f"在{player_target}开口前先把话截住，说别把场面弄得太难看。",
                f"抢先替沈昭宁定调，说大家吃顿饭没必要闹僵。",
                f"冷淡地看了{player_target}一眼，先一步把解释口子堵上。",
            ]
        elif profile == "shielding":
            alternatives = [
                "先替沈昭宁接过话头，说她只是怕大家都尴尬。",
                f"顺手站到沈昭宁那边，替她把{player_target}的解释压了回去。",
                "用一副讲道理的口气替沈昭宁铺台阶，先把她护住。",
            ]
        elif profile == "quiet_hint":
            alternatives = [
                f"趁人不注意，低声提醒{player_target}别碰主位边那只杯子。",
                f"压低声音提醒{player_target}先别跟着他们的话头硬顶。",
            ]
        elif actor_state.get("framing_style") or actor_state.get("bias") or actor_state.get("territorial"):
            if day_phase in {"arrival", "pre_dinner", "dinner"}:
                alternatives = [
                    f"顺着眼前的场面先压{player_target}一句，把位置和规矩都摆到台面上。",
                    f"不等{player_target}多说，就先拿场合把人压住。",
                ]

        alternatives = [item for item in alternatives if item]
        if not alternatives:
            return normalized
        return alternatives[current_step % len(alternatives)]

    def _is_passive_observe_intent(self, intent: str) -> bool:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return True
        passive_patterns = [
            "先观察局势，避免贸然暴露自己。",
            "先观察局势",
            "先看情况",
            "暂时按兵不动",
            "保持沉默",
            "静观其变",
        ]
        return any(pattern in normalized for pattern in passive_patterns)

    def _sanitize_auto_intent(self, intent: str, is_player: bool) -> str:
        normalized = " ".join(str(intent or "").split())
        if not normalized:
            return normalized

        normalized = normalized.replace("……", "，").replace("...", "，").replace("..", "，")
        normalized = re.sub(r"[“”\"]", "", normalized)

        if is_player:
            decorative_patterns = [
                r"，手[^，。！？]{0,14}(搭|落|扶|按|抚)[^，。！？]{0,18}",
                r"，目光[^，。！？]{0,18}",
                r"，视线[^，。！？]{0,18}",
                r"，眼神[^，。！？]{0,18}",
                r"，(?:刻意|故意|平静地|慢慢地|轻轻地|从容地)[^，。！？]{0,16}",
                r"，用恰好能让[^，。！？]{0,24}",
            ]
            for pattern in decorative_patterns:
                normalized = re.sub(pattern, "", normalized)
            normalized = re.sub(r"(?<!^)(我平静地|我轻轻地|我慢慢地)", "我", normalized)

        normalized = re.sub(r"，{2,}", "，", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip("，； ")
        if normalized and normalized[-1] not in "。！？!?":
            normalized += "。"
        return normalized

    def _proposal_role(self, is_player: bool, source: str) -> str:
        if source == "manual" and is_player:
            return "player_override"
        if source == "manual":
            return "manual_override"
        if source == "timeline":
            return "world_pressure"
        if source == "injected":
            return "world_event"
        return "character_proposal"

    def _proposal_priority(self, is_player: bool, source: str) -> float:
        if is_player and source == "manual":
            return 1.0
        if source == "manual":
            return 0.7
        if source == "timeline":
            return 0.78
        if source == "injected":
            return 0.9
        return 0.48
