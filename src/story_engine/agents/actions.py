from dataclasses import dataclass
from typing import Any, Dict, Literal, Tuple


ActionKind = Literal["observe", "move", "interact", "communicate", "wait"]
ACTION_KINDS = {"observe", "move", "interact", "communicate", "wait"}


def require_natural_language(value: Any, *, field: str = "action") -> str:
    """Validate an external action boundary.

    Structured action objects are an internal Host representation only. Player
    and Hermes proposals must cross the boundary as explicit natural language.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a natural-language string")
    text = " ".join(value.split()).strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty natural-language string")
    return text


def parse_natural_language_action(value: Any, *, field: str = "action") -> "AgentAction":
    return AgentAction.from_value(require_natural_language(value, field=field))


@dataclass(frozen=True)
class AgentAction:
    """One coarse external action with natural-language parameters.

    The kind is deliberately small and stable. ``detail`` and ``target`` keep
    the open-ended semantics that the environment rules and semantic GM resolve
    together. An action is still only a proposal; it never mutates the world.
    """

    kind: ActionKind
    detail: str
    target: str = ""
    affordance_id: str = ""
    claim_id: str = ""
    claim_stance: str = ""
    evidence_refs: Tuple[str, ...] = ()
    delivery_recipient: str = ""
    agreement_operation: str = ""
    agreement_id: str = ""
    agreement_template_id: str = ""
    agreement_give_refs: Tuple[str, ...] = ()
    agreement_request_refs: Tuple[str, ...] = ()
    agreement_service_object: str = ""
    agreement_service_destination: str = ""
    agreement_payment_ref: str = ""
    agreement_deadline: str = ""
    route_source: str = ""
    route_target: str = ""
    route_path: Tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any, *, strict: bool = False) -> "AgentAction":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            raw_kind = str(
                value.get("kind") or value.get("type") or value.get("action_type") or ""
            ).strip().lower()
            if strict and raw_kind not in ACTION_KINDS:
                raise ValueError(
                    "structured agent action requires a valid kind"
                )
            detail = cls._text(
                value.get("detail")
                or value.get("description")
                or value.get("intent")
                or value.get("action"),
                800,
            )
            target = cls._text(value.get("target"), 160)
            affordance_id = cls._text(value.get("affordance_id"), 120)
            claim_id = cls._text(value.get("claim_id"), 120)
            claim_stance = cls._text(value.get("claim_stance"), 20)
            raw_evidence = value.get("evidence_refs", [])
            if not isinstance(raw_evidence, (list, tuple)):
                raw_evidence = []
            evidence_refs = tuple(
                dict.fromkeys(
                    text
                    for item in raw_evidence[:8]
                    if (text := cls._text(item, 120))
                )
            )
            delivery_recipient = cls._text(
                value.get("delivery_recipient"), 120
            )
            agreement_operation = cls._text(
                value.get("agreement_operation"), 20
            )
            agreement_id = cls._text(value.get("agreement_id"), 120)
            agreement_template_id = cls._text(
                value.get("agreement_template_id"), 120
            )
            agreement_give_refs = cls._reference_list(
                value.get("agreement_give_refs", [])
            )
            agreement_request_refs = cls._reference_list(
                value.get("agreement_request_refs", [])
            )
            agreement_service_object = cls._text(
                value.get("agreement_service_object"), 120
            )
            agreement_service_destination = cls._text(
                value.get("agreement_service_destination"), 120
            )
            agreement_payment_ref = cls._text(
                value.get("agreement_payment_ref"), 120
            )
            agreement_deadline = cls._text(
                value.get("agreement_deadline"), 20
            )
            route_source = cls._text(value.get("route_source"), 120)
            route_target = cls._text(value.get("route_target"), 120)
            route_path = cls._reference_list(value.get("route_path", []))
            kind = raw_kind if raw_kind in ACTION_KINDS else cls.infer_kind(detail)
            return cls(
                kind=kind,
                detail=detail,
                target=target,
                affordance_id=affordance_id if kind == "interact" else "",
                claim_id=claim_id if kind == "communicate" else "",
                claim_stance=(
                    claim_stance
                    if kind == "communicate"
                    and claim_stance in {"supports", "rejects", "uncertain"}
                    else ""
                ),
                evidence_refs=evidence_refs if kind == "communicate" else (),
                delivery_recipient=(
                    delivery_recipient if kind == "interact" else ""
                ),
                agreement_operation=(
                    agreement_operation
                    if kind == "communicate"
                    and agreement_operation
                    in {"propose", "accept", "reject", "withdraw"}
                    else ""
                ),
                agreement_id=agreement_id if kind == "communicate" else "",
                agreement_template_id=(
                    agreement_template_id if kind == "communicate" else ""
                ),
                agreement_give_refs=(
                    agreement_give_refs if kind == "communicate" else ()
                ),
                agreement_request_refs=(
                    agreement_request_refs if kind == "communicate" else ()
                ),
                agreement_service_object=(
                    agreement_service_object if kind == "communicate" else ""
                ),
                agreement_service_destination=(
                    agreement_service_destination if kind == "communicate" else ""
                ),
                agreement_payment_ref=(
                    agreement_payment_ref if kind == "communicate" else ""
                ),
                agreement_deadline=(
                    agreement_deadline
                    if kind == "communicate"
                    and agreement_deadline in {"urgent", "soon", "flexible"}
                    else ""
                ),
                route_source=route_source if kind == "communicate" else "",
                route_target=route_target if kind == "communicate" else "",
                route_path=route_path if kind == "communicate" else (),
            )
        if strict:
            raise ValueError("structured agent action must be an object with kind")
        detail = cls._text(value, 800)
        return cls(kind=cls.infer_kind(detail), detail=detail)

    @staticmethod
    def infer_kind(detail: str) -> ActionKind:
        text = str(detail or "").lower()
        if any(token in text for token in ("观察", "查看", "检查", "搜索", "环顾", "inspect", "observe", "search")):
            return "observe"
        if any(token in text for token in ("前往", "走到", "进入", "离开", "移动", "move", "go to", "leave")):
            return "move"
        if any(token in text for token in ("说", "问", "告诉", "回答", "喊", "communicate", "speak", "ask", "tell")):
            return "communicate"
        if any(token in text for token in ("等待", "停留", "休息", "wait", "rest")):
            return "wait"
        return "interact"

    def to_dict(self) -> Dict[str, str]:
        payload = {
            "kind": self.kind,
            "detail": self.detail,
            "target": self.target,
        }
        if self.kind == "interact" and self.affordance_id:
            payload["affordance_id"] = self.affordance_id
        if self.kind == "interact" and self.delivery_recipient:
            payload["delivery_recipient"] = self.delivery_recipient
        if self.kind == "communicate" and self.claim_id:
            payload["claim_id"] = self.claim_id
            if self.claim_stance:
                payload["claim_stance"] = self.claim_stance
            if self.evidence_refs:
                payload["evidence_refs"] = list(self.evidence_refs)
        if self.kind == "communicate" and self.agreement_operation:
            payload["agreement_operation"] = self.agreement_operation
            if self.agreement_id:
                payload["agreement_id"] = self.agreement_id
            if self.agreement_template_id:
                payload["agreement_template_id"] = self.agreement_template_id
            if self.agreement_give_refs:
                payload["agreement_give_refs"] = list(self.agreement_give_refs)
            if self.agreement_request_refs:
                payload["agreement_request_refs"] = list(
                    self.agreement_request_refs
                )
            if self.agreement_service_object:
                payload["agreement_service_object"] = self.agreement_service_object
            if self.agreement_service_destination:
                payload["agreement_service_destination"] = (
                    self.agreement_service_destination
                )
            if self.agreement_payment_ref:
                payload["agreement_payment_ref"] = self.agreement_payment_ref
            if self.agreement_deadline:
                payload["agreement_deadline"] = self.agreement_deadline
            if self.route_source and self.route_target:
                payload["route_source"] = self.route_source
                payload["route_target"] = self.route_target
            if self.route_path:
                payload["route_path"] = list(self.route_path)
        return payload

    @classmethod
    def _reference_list(cls, value: Any) -> Tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            dict.fromkeys(
                text
                for item in value[:4]
                if (text := cls._text(item, 120))
            )
        )

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
