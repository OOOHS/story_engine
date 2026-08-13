import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Sequence

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision, AgentPerception
from src.story_engine.common.action_features import infer_social_response_kinds
from src.story_engine.simulation.randomness import DeterministicRandomStreams


@dataclass(frozen=True)
class ActionCandidate:
    candidate_id: str
    action: AgentAction
    source: str
    tags: tuple[str, ...] = ()
    base_utility: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicySelection:
    action: AgentAction
    trace: Dict[str, Any]


class CharacterPolicy:
    """Host-owned utility and seeded sampling around semantic agent candidates."""

    _TAG_PATTERNS = {
        "risk": ("冒险", "冲", "闯", "强行", "攻击", "威胁", "危险", "risk", "attack"),
        "confront": ("质问", "对峙", "反驳", "拒绝", "挑战", "威胁", "施压", "指责", "控告", "confront", "accuse"),
        "retreat": ("撤退", "逃", "离开", "退开", "避开", "withdraw", "flee"),
        "aid": ("帮助", "保护", "救", "照顾", "支援", "help", "protect", "rescue"),
        "information": ("观察", "检查", "搜索", "询问", "打听", "试探", "偷听", "observe", "ask"),
        "deception": ("欺骗", "撒谎", "假装", "隐瞒", "误导", "deceive", "lie"),
        "rest": ("等待", "休息", "停留", "冷静", "wait", "rest"),
    }

    def select(
        self,
        *,
        entity: Any,
        perception: AgentPerception,
        decision: AgentDecision,
        random_streams: DeterministicRandomStreams,
        world_version: int,
        relationship_book: Any = None,
        host_action_features: Dict[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> PolicySelection:
        candidates = self._collect_candidates(
            perception,
            decision,
            host_action_features=host_action_features,
        )
        candidates = [
            self._validate_motive_refs(entity, perception, candidate)
            for candidate in candidates
        ]
        attention_opportunities = self._attention_motive_opportunities(
            entity, perception
        )
        if not decision.candidates:
            action = decision.normalized_action()
            return PolicySelection(
                action=action,
                trace={
                    "mode": "runtime_committed",
                    "selected_candidate_id": "runtime:0",
                    "selected_action": action.to_dict(),
                    "candidates": [
                        {
                            "candidate_id": "runtime:0",
                            "source": "runtime",
                            "action": action.to_dict(),
                        }
                    ],
                },
            )

        trait_state = entity.get_component("TraitState")
        drive_state = entity.get_component("DriveState")
        risk_tolerance = float(getattr(drive_state, "risk_tolerance", 0.5) or 0.5)
        controller = entity.get_component("AgentController")
        policy_config = dict(getattr(controller, "config", {}).get("policy", {}) or {})
        temperature = max(0.05, min(5.0, float(policy_config.get("temperature", 0.8))))

        scored = []
        for candidate in candidates:
            trait_score, trait_contributions = (
                trait_state.score_tags(candidate.tags) if trait_state else (0.0, {})
            )
            risk_score = 0.0
            if "risk" in candidate.tags:
                risk_score = (risk_tolerance - 0.5) * 2.0
            relief_score = float(candidate.metadata.get("relief_score", 0.0) or 0.0) * 2.0
            relief_contributions = {
                str(need_id): round(float(value or 0.0) * 2.0, 6)
                for need_id, value in (
                    candidate.metadata.get("relief_contributions", {}) or {}
                ).items()
                if str(need_id).strip() and float(value or 0.0) > 0
            }
            relation_score, relation_contributions = self._relationship_score(
                perception, candidate, relationship_book
            )
            obligation_score, obligation_contributions = (
                self._obligation_score_with_contributions(perception, candidate)
            )
            navigation_score, navigation_contributions = (
                self._navigation_score_with_contributions(
                    perception, candidate
                )
            )
            action_failure_score, action_failure_contributions = (
                self._action_failure_score_with_contributions(candidate)
            )
            goal_score, goal_contributions = self._goal_score_with_contributions(
                entity, candidate
            )
            sentiment_score, sentiment_contributions = self._sentiment_score(
                entity, candidate
            )
            modifier_score, modifier_contributions = self._modifier_score(
                entity, candidate
            )
            knowledge_score, knowledge_contributions = (
                self._knowledge_score_with_contributions(perception, candidate)
            )
            agreement_score, agreement_contributions = (
                self._agreement_score_with_contributions(candidate)
            )
            world_event_score, world_event_contributions = (
                self._attention_motive_score_with_contributions(
                    candidate, "world_event"
                )
            )
            event_response_score, event_response_contributions = (
                self._attention_motive_score_with_contributions(
                    candidate, "event_response"
                )
            )
            continuity_score, continuity_contributions = (
                self._continuity_score_with_contributions(perception, candidate)
            )
            repetition_score = self._repetition_score(controller, candidate)
            schedule_contributions = {
                str(candidate.metadata.get("commitment_id", "")).strip(): round(
                    float(candidate.metadata.get("schedule_contribution", 0.0) or 0.0),
                    6,
                )
            } if (
                str(candidate.metadata.get("commitment_id", "")).strip()
                and float(candidate.metadata.get("schedule_contribution", 0.0) or 0.0) > 0
            ) else {}
            utility = (
                candidate.base_utility
                + trait_score
                + risk_score
                + relief_score
                + relation_score
                + obligation_score
                + navigation_score
                + action_failure_score
                + goal_score
                + sentiment_score
                + modifier_score
                + knowledge_score
                + agreement_score
                + world_event_score
                + event_response_score
                + continuity_score
                + repetition_score
            )
            weight = math.exp(max(-20.0, min(20.0, utility / temperature)))
            scored.append(
                {
                    "candidate": candidate,
                    "utility": utility,
                    "weight": weight,
                    "trait_contributions": trait_contributions,
                    "risk_contribution": risk_score,
                    "relief_contribution": relief_score,
                    "relief_contributions": relief_contributions,
                    "relationship_contribution": relation_score,
                    "relationship_contributions": relation_contributions,
                    "obligation_contribution": obligation_score,
                    "obligation_contributions": obligation_contributions,
                    "navigation_contribution": navigation_score,
                    "navigation_contributions": navigation_contributions,
                    "action_failure_contribution": action_failure_score,
                    "action_failure_contributions": (
                        action_failure_contributions
                    ),
                    "goal_contribution": goal_score,
                    "goal_contributions": goal_contributions,
                    "sentiment_contribution": sentiment_score,
                    "sentiment_contributions": sentiment_contributions,
                    "modifier_contribution": modifier_score,
                    "modifier_contributions": modifier_contributions,
                    "knowledge_contribution": knowledge_score,
                    "knowledge_contributions": knowledge_contributions,
                    "agreement_contribution": agreement_score,
                    "agreement_contributions": agreement_contributions,
                    "world_event_contribution": world_event_score,
                    "world_event_contributions": world_event_contributions,
                    "event_response_contribution": event_response_score,
                    "event_response_contributions": event_response_contributions,
                    "continuity_contribution": continuity_score,
                    "continuity_contributions": continuity_contributions,
                    "repetition_contribution": repetition_score,
                    "schedule_contributions": schedule_contributions,
                }
            )

        selected, roll = random_streams.weighted_choice(
            [item["candidate"] for item in scored],
            [item["weight"] for item in scored],
            stream="policy",
            key_parts=(perception.actor_name, perception.step, world_version),
        )
        weight_total = sum(item["weight"] for item in scored) or 1.0
        trace_candidates = []
        for item in scored:
            candidate = item["candidate"]
            trace_candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "source": candidate.source,
                    "action": candidate.action.to_dict(),
                    "tags": list(candidate.tags),
                    "base_utility": round(candidate.base_utility, 6),
                    "utility": round(item["utility"], 6),
                    "probability": round(item["weight"] / weight_total, 8),
                    "trait_contributions": item["trait_contributions"],
                    "risk_contribution": round(item["risk_contribution"], 6),
                    "relief_contribution": round(item["relief_contribution"], 6),
                    "relief_contributions": item["relief_contributions"],
                    "relationship_contribution": round(
                        item["relationship_contribution"], 6
                    ),
                    "relationship_contributions": item[
                        "relationship_contributions"
                    ],
                    "obligation_contribution": round(
                        item["obligation_contribution"], 6
                    ),
                    "obligation_contributions": item[
                        "obligation_contributions"
                    ],
                    "navigation_contribution": round(
                        item["navigation_contribution"], 6
                    ),
                    "navigation_contributions": item[
                        "navigation_contributions"
                    ],
                    "action_failure_contribution": round(
                        item["action_failure_contribution"], 6
                    ),
                    "action_failure_contributions": item[
                        "action_failure_contributions"
                    ],
                    "goal_contribution": round(item["goal_contribution"], 6),
                    "goal_contributions": item["goal_contributions"],
                    "sentiment_contribution": round(
                        item["sentiment_contribution"], 6
                    ),
                    "sentiment_contributions": item[
                        "sentiment_contributions"
                    ],
                    "modifier_contribution": round(
                        item["modifier_contribution"], 6
                    ),
                    "modifier_contributions": item["modifier_contributions"],
                    "knowledge_contribution": round(
                        item["knowledge_contribution"], 6
                    ),
                    "knowledge_contributions": item["knowledge_contributions"],
                    "agreement_contribution": round(
                        item["agreement_contribution"], 6
                    ),
                    "agreement_contributions": item["agreement_contributions"],
                    "world_event_contribution": round(
                        item["world_event_contribution"], 6
                    ),
                    "world_event_contributions": item[
                        "world_event_contributions"
                    ],
                    "event_response_contribution": round(
                        item["event_response_contribution"], 6
                    ),
                    "event_response_contributions": item[
                        "event_response_contributions"
                    ],
                    "continuity_contribution": round(
                        item["continuity_contribution"], 6
                    ),
                    "continuity_contributions": item[
                        "continuity_contributions"
                    ],
                    "repetition_contribution": round(
                        item["repetition_contribution"], 6
                    ),
                    "schedule_contributions": item["schedule_contributions"],
                    "validated_motive_refs": list(
                        candidate.metadata.get("validated_motive_refs", [])
                    ),
                    "rejected_motive_refs": list(
                        candidate.metadata.get("rejected_motive_refs", [])
                    ),
                }
            )
        return PolicySelection(
            action=selected.action,
            trace={
                "mode": "host_sampled",
                "stream": roll.stream,
                "roll_key": roll.key,
                "roll": round(roll.value, 12),
                "temperature": temperature,
                "selected_candidate_id": selected.candidate_id,
                "selected_action": selected.action.to_dict(),
                "attention_motive_available_count": attention_opportunities[
                    "available"
                ],
                "urgent_attention_motive_available_count": (
                    attention_opportunities["urgent"]
                ),
                "candidates": trace_candidates,
            },
        )

    @staticmethod
    def _attention_motive_opportunities(
        entity: Any,
        perception: AgentPerception,
    ) -> Dict[str, int]:
        cognition = entity.get_component("Cognition")
        if cognition is None:
            return {"available": 0, "urgent": 0}
        available = 0
        urgent = 0
        for snapshot_key, book_name in (
            ("pending_world_events", "world_event_attention"),
            ("pending_event_responses", "event_response_attention"),
        ):
            book = getattr(cognition, book_name, {}) or {}
            for raw_ref in (
                perception.private_cognition.get(snapshot_key, []) or []
            ):
                ref = str(raw_ref).strip()
                record = book.get(ref) if ref else None
                if record is None:
                    continue
                available += 1
                if int(getattr(record, "priority", 0) or 0) >= 75:
                    urgent += 1
        return {"available": available, "urgent": urgent}

    def _collect_candidates(
        self,
        perception: AgentPerception,
        decision: AgentDecision,
        host_action_features: Dict[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> List[ActionCandidate]:
        candidates: List[ActionCandidate] = []
        for index, action in enumerate(decision.candidates):
            normalized = AgentAction.from_value(action)
            declared_motive_refs = (
                decision.candidate_motive_refs[index]
                if index < len(decision.candidate_motive_refs)
                else ()
            )
            candidates.append(
                self._candidate(
                    f"runtime:{index}",
                    normalized,
                    "runtime",
                    base_utility=0.35,
                    metadata={
                        "declared_motive_refs": [
                            {"kind": item.kind, "ref": item.ref}
                            for item in declared_motive_refs
                        ]
                    },
                )
            )
        candidates.extend(self._environment_candidates(perception))
        feature_map = host_action_features or {}
        candidates = [
            replace(
                candidate,
                tags=tuple(sorted(set(candidate.tags).union(
                    feature_map.get(
                        (candidate.action.target, candidate.action.affordance_id),
                        (),
                    )
                ))),
            )
            for candidate in candidates
        ]
        deduped: Dict[tuple[str, ...], ActionCandidate] = {}
        for candidate in candidates:
            key = (
                candidate.action.kind,
                candidate.action.detail.casefold(),
                candidate.action.target.casefold(),
                candidate.action.affordance_id.casefold(),
                candidate.action.claim_id.casefold(),
                candidate.action.claim_stance.casefold(),
                tuple(item.casefold() for item in candidate.action.evidence_refs),
                candidate.action.delivery_recipient.casefold(),
                candidate.action.agreement_operation.casefold(),
                candidate.action.agreement_id.casefold(),
                candidate.action.agreement_template_id.casefold(),
                "\x1f".join(candidate.action.agreement_give_refs).casefold(),
                "\x1f".join(candidate.action.agreement_request_refs).casefold(),
                candidate.action.agreement_service_object.casefold(),
                candidate.action.agreement_service_destination.casefold(),
                candidate.action.agreement_payment_ref.casefold(),
                candidate.action.agreement_deadline.casefold(),
                "\x1f".join(candidate.action.route_path).casefold(),
            )
            previous = deduped.get(key)
            if previous is None or candidate.base_utility > previous.base_utility:
                deduped[key] = candidate
        semantic_candidates: List[ActionCandidate] = []
        for candidate in deduped.values():
            duplicate_index = next(
                (
                    index
                    for index, previous in enumerate(semantic_candidates)
                    if not self.materially_distinct(
                        candidate.action, previous.action
                    )
                ),
                None,
            )
            if duplicate_index is None:
                semantic_candidates.append(candidate)
            elif (
                candidate.base_utility
                > semantic_candidates[duplicate_index].base_utility
            ):
                semantic_candidates[duplicate_index] = candidate
        return semantic_candidates[:24]

    @staticmethod
    def _validate_motive_refs(
        entity: Any,
        perception: AgentPerception,
        candidate: ActionCandidate,
    ) -> ActionCandidate:
        declared = candidate.metadata.get("declared_motive_refs", [])
        if not isinstance(declared, list) or not declared:
            return candidate

        goal_state = entity.get_component("GoalState")
        active_goals = {
            str(record.goal_id)
            for record in (
                goal_state.active_records()
                if goal_state is not None and hasattr(goal_state, "active_records")
                else []
            )
        }
        active_obligations = {
            str(item.get("obligation_id", "")).strip()
            for item in perception.private_obligations.get("active", [])
            if isinstance(item, dict)
            and str(item.get("obligation_id", "")).strip()
        }
        pending_world_events = {
            str(item).strip()
            for item in perception.private_cognition.get(
                "pending_world_events", []
            ) or []
            if str(item).strip()
        }
        pending_event_responses = {
            str(item).strip()
            for item in perception.private_cognition.get(
                "pending_event_responses", []
            ) or []
            if str(item).strip()
        }
        active_navigation_problems = {
            str(item.get("problem_id", "")).strip()
            for item in perception.private_navigation.get("active", [])
            if isinstance(item, dict)
            and str(item.get("problem_id", "")).strip()
        }
        recent_action_failures = {}
        for experience in perception.private_cognition.get(
            "recent_experiences", []
        ) or []:
            if not isinstance(experience, dict):
                continue
            try:
                experience_step = int(experience.get("step", 0) or 0)
            except (TypeError, ValueError):
                experience_step = 0
            age = max(0, int(perception.step) - experience_step)
            if age > 6:
                continue
            for event in experience.get("events", []) or []:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("event_id", "")).strip()
                outcome = str(event.get("outcome", "")).strip().lower()
                if event_id and outcome in {"fail", "blocked"}:
                    recent_action_failures[event_id] = age
        catalogs = {
            "goal": active_goals,
            "obligation": active_obligations,
            "navigation_problem": active_navigation_problems,
            "action_failure": set(recent_action_failures),
            "world_event": pending_world_events,
            "event_response": pending_event_responses,
        }
        cognition = entity.get_component("Cognition")
        attention_books = {
            "world_event": getattr(
                cognition, "world_event_attention", {}
            ) if cognition is not None else {},
            "event_response": getattr(
                cognition, "event_response_attention", {}
            ) if cognition is not None else {},
        }
        validated = []
        rejected = []
        attention_priorities: Dict[str, int] = {}
        action_failure_ages: Dict[str, int] = {}
        for item in declared[:8]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).strip()
            ref = str(item.get("ref", "")).strip()
            normalized = {"kind": kind, "ref": ref}
            is_valid = bool(
                kind in catalogs and ref and ref in catalogs[kind]
            )
            if kind in attention_books:
                is_valid = is_valid and ref in attention_books[kind]
            if is_valid:
                if normalized not in validated:
                    validated.append(normalized)
                record = attention_books.get(kind, {}).get(ref)
                if record is not None:
                    attention_priorities[f"{kind}:{ref}"] = max(
                        0,
                        min(100, int(getattr(record, "priority", 0) or 0)),
                    )
                if kind == "action_failure":
                    action_failure_ages[ref] = recent_action_failures[ref]
            elif normalized not in rejected:
                rejected.append(normalized)
        metadata = dict(candidate.metadata)
        metadata["validated_motive_refs"] = validated
        metadata["rejected_motive_refs"] = rejected
        metadata["attention_motive_priorities"] = attention_priorities
        metadata["action_failure_ages"] = action_failure_ages
        return replace(candidate, metadata=metadata)

    def materially_distinct(
        self,
        left: AgentAction,
        right: AgentAction,
    ) -> bool:
        left = AgentAction.from_value(left)
        right = AgentAction.from_value(right)
        if left.kind != right.kind:
            return True
        if self._structured_action_signature(left) != self._structured_action_signature(
            right
        ):
            return True
        left_target = " ".join(left.target.casefold().split())
        right_target = " ".join(right.target.casefold().split())
        if left_target and right_target and left_target != right_target:
            return True
        return self._infer_tags(left) != self._infer_tags(right)

    @classmethod
    def repetition_signature(cls, action: AgentAction) -> str:
        action = AgentAction.from_value(action)
        tags = ",".join(sorted(cls._infer_tags(action)))
        structured = repr(cls._structured_action_signature(action))
        return "\x1f".join((action.kind, tags, structured))[:1000]

    @staticmethod
    def repetition_target(action: AgentAction) -> str:
        action = AgentAction.from_value(action)
        return " ".join(action.target.casefold().split())

    @classmethod
    def _repetition_score(
        cls,
        controller: Any,
        candidate: ActionCandidate,
    ) -> float:
        if controller is None:
            return 0.0
        signature = cls.repetition_signature(candidate.action)
        if signature != str(
            getattr(controller, "last_policy_action_signature", "") or ""
        ):
            return 0.0
        previous_target = str(
            getattr(controller, "last_policy_action_target", "") or ""
        )
        candidate_target = cls.repetition_target(candidate.action)
        if (
            previous_target
            and candidate_target
            and previous_target != candidate_target
        ):
            return 0.0
        repeated = max(
            0,
            int(getattr(controller, "repeated_policy_action_count", 0) or 0),
        )
        if candidate.action.kind == "wait":
            return -min(0.2, repeated * 0.05)
        return -min(0.9, repeated * 0.15)

    @staticmethod
    def _structured_action_signature(action: AgentAction) -> tuple[Any, ...]:
        return (
            action.affordance_id.casefold(),
            action.claim_id.casefold(),
            action.claim_stance.casefold(),
            tuple(item.casefold() for item in action.evidence_refs),
            action.delivery_recipient.casefold(),
            action.agreement_operation.casefold(),
            action.agreement_id.casefold(),
            action.agreement_template_id.casefold(),
            tuple(item.casefold() for item in action.agreement_give_refs),
            tuple(item.casefold() for item in action.agreement_request_refs),
            action.agreement_service_object.casefold(),
            action.agreement_service_destination.casefold(),
            action.agreement_payment_ref.casefold(),
            action.agreement_deadline.casefold(),
            action.route_source.casefold(),
            action.route_target.casefold(),
            tuple(item.casefold() for item in action.route_path),
        )

    @staticmethod
    def _agreement_score(candidate: ActionCandidate) -> float:
        """Prefer a runtime's explicit formal act over generic conversation."""
        return CharacterPolicy._agreement_score_with_contributions(candidate)[0]

    @staticmethod
    def _agreement_score_with_contributions(
        candidate: ActionCandidate,
    ) -> tuple[float, Dict[str, float]]:
        operation = candidate.action.agreement_operation
        if not operation:
            return 0.0, {}
        score = 1.5 if operation == "accept" else 0.8
        agreement_id = str(candidate.action.agreement_id or "").strip()
        return score, ({agreement_id: score} if agreement_id else {})

    @staticmethod
    def _attention_motive_score_with_contributions(
        candidate: ActionCandidate,
        kind: str,
    ) -> tuple[float, Dict[str, float]]:
        priorities = candidate.metadata.get("attention_motive_priorities", {})
        if not isinstance(priorities, dict):
            return 0.0, {}
        contributions: Dict[str, float] = {}
        for item in candidate.metadata.get("validated_motive_refs", []):
            if not isinstance(item, dict) or item.get("kind") != kind:
                continue
            ref = str(item.get("ref", "")).strip()
            if not ref:
                continue
            try:
                priority = max(
                    0.0,
                    min(100.0, float(priorities.get(f"{kind}:{ref}", 0))),
                )
            except (TypeError, ValueError):
                priority = 0.0
            if priority <= 0:
                continue
            contributions[ref] = round(0.15 + priority * 0.007, 6)
        return (
            max(contributions.values(), default=0.0),
            contributions,
        )

    def _environment_candidates(self, perception: AgentPerception) -> List[ActionCandidate]:
        candidates = [
            self._candidate(
                "environment:observe",
                AgentAction("observe", "仔细观察当前环境中最值得注意的变化。"),
                "environment",
                base_utility=-0.05,
            ),
            self._candidate(
                "environment:wait",
                AgentAction("wait", "暂时保持现状，等待局势出现新的信息。"),
                "environment",
                base_utility=-0.4,
            ),
        ]
        current_location = str(perception.world_view.get("location") or "").strip()
        for item in perception.private_schedule.get("active", [])[:6]:
            if not isinstance(item, dict):
                continue
            target = str(item.get("location") or "").strip()
            if not target or target == current_location:
                continue
            try:
                steps_until_due = int(item.get("steps_until_due", 10**9))
            except (TypeError, ValueError):
                steps_until_due = 10**9
            base_utility = (
                0.7
                if steps_until_due <= 0
                else 0.4
                if steps_until_due == 1
                else 0.15
            )
            commitment_id = str(item.get("commitment_id", "")).strip()
            title = str(item.get("title") or commitment_id).strip()
            candidates.append(
                self._candidate(
                    f"schedule:move:{commitment_id}:{target}",
                    AgentAction(
                        "move",
                        f"为日程“{title}”前往{target}。",
                        target,
                    ),
                    "schedule",
                    base_utility=base_utility,
                    metadata={
                        "commitment_id": commitment_id,
                        "steps_until_due": steps_until_due,
                        "schedule_contribution": max(0.0, base_utility),
                    },
                )
            )
        visible_actors = [
            str(name).strip()
            for name in perception.world_view.get("visible_actors", [])
            if str(name).strip() and str(name).strip() != perception.actor_name
        ]
        for actor in visible_actors[:4]:
            candidates.append(
                self._candidate(
                    f"environment:communicate:{actor}",
                    AgentAction("communicate", f"主动与{actor}交谈，了解对方当前的态度或意图。", actor),
                    "environment",
                    base_utility=-0.1,
                )
            )
        location = str(perception.world_view.get("location") or "").strip()
        location_state = perception.world_view.get("visible_world", {}).get(location, {})
        for target in list(location_state.get("connected_to", []) or [])[:6]:
            target_name = str(target).strip()
            if target_name:
                candidates.append(
                    self._candidate(
                        f"environment:move:{target_name}",
                        AgentAction("move", f"从当前位置前往{target_name}。", target_name),
                        "environment",
                        base_utility=-0.15,
                    )
                )
        for opportunity in perception.affordance_opportunities[:8]:
            if not isinstance(opportunity, dict) or not opportunity.get("available", True):
                continue
            # Universal physical possibilities belong to observation/action
            # grounding, not motivation. A runtime may choose them explicitly;
            # only authored need affordances enter the automatic utility pool.
            if str(opportunity.get("source", "")).strip() == "engine_physics":
                continue
            if float(opportunity.get("relief_score", 0.0) or 0.0) <= 0:
                continue
            object_id = str(opportunity.get("object_id", "")).strip()
            affordance_id = str(opportunity.get("affordance_id", "")).strip()
            label = str(opportunity.get("label") or affordance_id).strip()
            if not object_id or not affordance_id:
                continue
            candidates.append(
                self._candidate(
                    f"environment:affordance:{object_id}:{affordance_id}",
                    AgentAction(
                        "interact",
                        f"对{object_id}执行“{label}”。",
                        object_id,
                        affordance_id,
                    ),
                    "environment",
                    base_utility=0.0,
                    metadata={
                        "affordance_id": affordance_id,
                        "relief_score": opportunity.get("relief_score", 0.0),
                        "relief_contributions": dict(
                            opportunity.get("relief_contributions", {}) or {}
                        ),
                    },
                )
            )
        claim_statements = {
            str(item.get("claim_id", "")): str(item.get("statement", ""))
            for item in perception.private_knowledge.get("claims", [])
            if isinstance(item, dict) and str(item.get("claim_id", "")).strip()
        }
        for leverage in perception.private_knowledge.get("potential_leverage", [])[:4]:
            if not isinstance(leverage, dict):
                continue
            claim_id = str(leverage.get("claim_id", "")).strip()
            statement = claim_statements.get(claim_id, "").strip()
            targets = [
                str(item).strip()
                for item in leverage.get("targets", [])
                if str(item).strip() in visible_actors
            ]
            if not claim_id or not statement or not targets:
                continue
            target = targets[0]
            candidates.append(
                self._candidate(
                    f"environment:leverage:{claim_id}:{target}",
                    AgentAction(
                        "communicate",
                        f"围绕“{statement}”试探{target}，必要时据此施压。",
                        target,
                        claim_id=claim_id,
                        claim_stance=str(
                            next(
                                (
                                    item.get("stance", "")
                                    for item in perception.private_knowledge.get(
                                        "claims", []
                                    )
                                    if isinstance(item, dict)
                                    and str(item.get("claim_id", "")) == claim_id
                                ),
                                "",
                            )
                        ),
                        evidence_refs=tuple(
                            str(item).strip()
                            for item in leverage.get(
                                "owned_supporting_evidence", []
                            )
                            if str(item).strip()
                        ),
                    ),
                    "environment",
                    base_utility=-0.05,
                    metadata={
                        "claim_id": claim_id,
                        "claim_confidence": leverage.get("confidence", 0.0),
                        "evidence_backed": bool(leverage.get("evidence_backed")),
                    },
                )
            )
        return candidates

    def _candidate(
        self,
        candidate_id: str,
        action: AgentAction,
        source: str,
        *,
        base_utility: float,
        metadata: Dict[str, Any] | None = None,
    ) -> ActionCandidate:
        return ActionCandidate(
            candidate_id=candidate_id,
            action=action,
            source=source,
            tags=tuple(sorted(self._infer_tags(action))),
            base_utility=base_utility,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def _infer_tags(cls, action: AgentAction) -> set[str]:
        tags = {action.kind}
        text = f"{action.detail} {action.target}".casefold()
        for tag, patterns in cls._TAG_PATTERNS.items():
            if any(pattern.casefold() in text for pattern in patterns):
                tags.add(tag)
        if action.kind == "communicate":
            tags.update(infer_social_response_kinds(text))
        if action.kind == "observe":
            tags.update({"information", "cautious"})
        elif action.kind == "communicate":
            tags.add("social")
        elif action.kind == "wait":
            tags.update({"rest", "patient", "cautious"})
        return tags

    def _relationship_score(
        self,
        perception: AgentPerception,
        candidate: ActionCandidate,
        relationship_book: Any,
    ) -> tuple[float, Dict[str, float]]:
        target = candidate.action.target.strip()
        if not target or relationship_book is None:
            return 0.0, {}
        visible_targets = {
            str(item.get("actor", ""))
            for item in perception.relationship_context.get("visible_relations", [])
            if isinstance(item, dict)
        }
        if target not in visible_targets:
            return 0.0, {}
        tracks = relationship_book.get_track_records(perception.actor_name, target)
        contributions: Dict[str, float] = {}
        total = 0.0
        for track_id, track in tracks.items():
            value = float(track.value)
            lower = float(track.minimum)
            upper = float(track.maximum)
            scale = max(abs(lower), abs(upper), 1.0)
            normalized = value / scale
            weights = track.policy_weights
            contribution = normalized * sum(
                float(weights.get(tag, 0.0) or 0.0) for tag in candidate.tags
            )
            if contribution:
                contributions[str(track_id)] = round(contribution, 6)
                total += contribution
        return total, contributions

    def _obligation_score(
        self,
        perception: AgentPerception,
        candidate: ActionCandidate,
    ) -> float:
        return self._obligation_score_with_contributions(
            perception, candidate
        )[0]

    def _obligation_score_with_contributions(
        self,
        perception: AgentPerception,
        candidate: ActionCandidate,
    ) -> tuple[float, Dict[str, float]]:
        text = f"{candidate.action.detail} {candidate.action.target}".casefold()
        score = 0.0
        contributions: Dict[str, float] = {}
        declared_refs = {
            str(item.get("ref", ""))
            for item in candidate.metadata.get("validated_motive_refs", [])
            if isinstance(item, dict) and item.get("kind") == "obligation"
        }
        for obligation in perception.private_obligations.get("active", []):
            if not isinstance(obligation, dict):
                continue
            needles = [
                str(obligation.get("creditor") or "").strip(),
                str(obligation.get("title") or "").strip(),
                str(obligation.get("obligation_id") or "").strip(),
            ]
            obligation_id = str(obligation.get("obligation_id", "")).strip()
            if obligation_id not in declared_refs and not any(
                needle and needle.casefold() in text for needle in needles
            ):
                continue
            remaining = int(obligation.get("steps_remaining", 99) or 0)
            contribution = (
                1.2 if remaining <= 0 else 0.8 if remaining <= 2 else 0.35
            )
            score += contribution
            if obligation_id:
                contributions[obligation_id] = round(contribution, 6)
        return score, contributions

    @staticmethod
    def _navigation_score_with_contributions(
        perception: AgentPerception,
        candidate: ActionCandidate,
    ) -> tuple[float, Dict[str, float]]:
        declared_refs = {
            str(item.get("ref", ""))
            for item in candidate.metadata.get("validated_motive_refs", [])
            if isinstance(item, dict)
            and item.get("kind") == "navigation_problem"
        }
        if not declared_refs:
            return 0.0, {}
        contributions: Dict[str, float] = {}
        for problem in perception.private_navigation.get("active", []):
            if not isinstance(problem, dict):
                continue
            problem_id = str(problem.get("problem_id", "")).strip()
            if not problem_id or problem_id not in declared_refs:
                continue
            remaining = problem.get("steps_remaining")
            if remaining is None:
                contribution = 0.45
            else:
                try:
                    remaining_steps = int(remaining)
                except (TypeError, ValueError):
                    remaining_steps = 99
                contribution = (
                    0.9
                    if remaining_steps <= 0
                    else 0.7
                    if remaining_steps <= 2
                    else 0.5
                )
            contributions[problem_id] = contribution
        return max(contributions.values(), default=0.0), contributions

    @staticmethod
    def _action_failure_score_with_contributions(
        candidate: ActionCandidate,
    ) -> tuple[float, Dict[str, float]]:
        ages = candidate.metadata.get("action_failure_ages", {})
        if not isinstance(ages, dict):
            return 0.0, {}
        contributions = {
            str(event_id): round(max(0.25, 0.65 - int(age) * 0.08), 6)
            for event_id, age in ages.items()
            if str(event_id).strip()
        }
        return max(contributions.values(), default=0.0), contributions

    def _goal_score(self, entity: Any, candidate: ActionCandidate) -> float:
        """Compatibility scalar used by focused policy callers and tests."""
        return self._goal_score_with_contributions(entity, candidate)[0]

    def _goal_score_with_contributions(
        self, entity: Any, candidate: ActionCandidate
    ) -> tuple[float, Dict[str, float]]:
        goal_state = entity.get_component("GoalState")
        if goal_state is not None and hasattr(goal_state, "active_records"):
            goals = [
                (str(record.goal_id), str(record.title))
                for record in goal_state.active_records()
            ]
        else:
            identity = entity.get_component("Identity")
            goals = [
                (f"initial:{index + 1}", str(title))
                for index, title in enumerate(
                    list(getattr(identity, "goals", []) or []) if identity else []
                )
            ]
        if not goals:
            return 0.0, {}
        declared_refs = {
            str(item.get("ref", ""))
            for item in candidate.metadata.get("validated_motive_refs", [])
            if isinstance(item, dict) and item.get("kind") == "goal"
        }
        explicit_contributions = {
            goal_id: 0.8
            for goal_id, _ in goals
            if goal_id in declared_refs
        }
        if explicit_contributions:
            return 0.8, explicit_contributions
        action_tokens = self._semantic_tokens(
            f"{candidate.action.detail} {candidate.action.target}"
        )
        if not action_tokens:
            return 0.0, {}
        best = 0.0
        contributions: Dict[str, float] = {}
        for goal_id, goal_title in goals:
            goal_tokens = self._semantic_tokens(goal_title)
            if not goal_tokens:
                continue
            overlap = len(action_tokens.intersection(goal_tokens)) / max(
                1, len(goal_tokens)
            )
            contribution = min(0.8, overlap * 0.8)
            if contribution:
                contributions[goal_id] = round(contribution, 6)
                best = max(best, contribution)
        return best, contributions

    def _sentiment_score(
        self, entity: Any, candidate: ActionCandidate
    ) -> tuple[float, Dict[str, float]]:
        target = candidate.action.target.strip()
        state = entity.get_component("SentimentState")
        if not target or state is None:
            return 0.0, {}
        return state.score_tags(target, set(candidate.tags))

    def _modifier_score(
        self, entity: Any, candidate: ActionCandidate
    ) -> tuple[float, Dict[str, float]]:
        state = entity.get_component("ModifierState")
        if state is None or not hasattr(state, "score_tags"):
            return 0.0, {}
        return state.score_tags(set(candidate.tags))

    @staticmethod
    def _knowledge_score(
        perception: AgentPerception, candidate: ActionCandidate
    ) -> float:
        return CharacterPolicy._knowledge_score_with_contributions(
            perception, candidate
        )[0]

    @staticmethod
    def _knowledge_score_with_contributions(
        perception: AgentPerception, candidate: ActionCandidate
    ) -> tuple[float, Dict[str, float]]:
        if not candidate.metadata.get("claim_id"):
            return 0.0, {}
        try:
            confidence = min(
                1.0,
                max(0.0, float(candidate.metadata.get("claim_confidence", 0.0))),
            )
        except (TypeError, ValueError):
            confidence = 0.0
        score = confidence * 0.4 + (
            0.3 if candidate.metadata.get("evidence_backed") else 0.0
        )
        claim_id = str(candidate.metadata.get("claim_id", "")).strip()
        return score, ({claim_id: round(score, 6)} if claim_id and score > 0 else {})

    @classmethod
    def _continuity_score_with_contributions(
        cls,
        perception: AgentPerception,
        candidate: ActionCandidate,
    ) -> tuple[float, Dict[str, float]]:
        action_tokens = cls._semantic_tokens(
            f"{candidate.action.detail} {candidate.action.target}"
        )
        if not action_tokens:
            return 0.0, {}

        contributions: Dict[str, float] = {}

        def contribution(key: str, text: Any, ceiling: float) -> None:
            source_tokens = cls._semantic_tokens(str(text or ""))
            if not source_tokens:
                return
            overlap = len(action_tokens.intersection(source_tokens)) / len(
                source_tokens
            )
            value = min(ceiling, overlap * ceiling)
            if value > 0:
                contributions[key] = round(value, 6)

        contribution("plan", perception.current_plan, 0.25)
        contribution(
            "focus",
            perception.private_cognition.get("current_focus", ""),
            0.15,
        )
        commitments = perception.private_cognition.get("commitments", []) or []
        if isinstance(commitments, list):
            for index, commitment in enumerate(commitments[-8:]):
                contribution(f"commitment:{index}", commitment, 0.2)
        total = min(0.6, sum(contributions.values()))
        return total, contributions

    @staticmethod
    def _semantic_tokens(text: str) -> set[str]:
        compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text).casefold())
        if not compact:
            return set()
        tokens = {compact[index : index + 2] for index in range(len(compact) - 1)}
        tokens.update(re.findall(r"[a-z0-9_]{3,}", str(text).casefold()))
        return tokens
