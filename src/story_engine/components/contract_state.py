"""Legacy import shim for the former GM-owned ContractState component.

Core runtime state now lives on Agreement Entities. ``ContractState`` remains a
plain transaction-book adapter so older content/tests can be migrated without
reintroducing it as an ECS Component.
"""

from typing import Any

from pydantic import ConfigDict

from src.story_engine.social.agreement_registry import AgreementBook, AgreementRecord


ContractRecord = AgreementRecord


class ContractState(AgreementBook):
    model_config = ConfigDict(extra="allow")

    def __init__(self, **data: Any) -> None:
        normalized = dict(data)
        if "contracts" in normalized and "agreements" not in normalized:
            normalized["agreements"] = normalized.pop("contracts")
        if "max_contracts" in normalized and "max_agreements" not in normalized:
            normalized["max_agreements"] = normalized.pop("max_contracts")
        super().__init__(**normalized)
