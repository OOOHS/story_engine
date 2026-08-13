from typing import Literal


ObservationMode = Literal["passive", "active"]


def observation_mode_for_action(*, personal: bool, action_kind: str) -> ObservationMode:
    """Classify experience using the active-perception distinction.

    A character's own completed ``observe`` action is active sensing. Everything
    arriving because the world changed or another actor acted is passive
    observation. This mirrors the action-dependent observation function used by
    POMDP/POSG models without requiring an explicit solver.
    """

    return "active" if personal and str(action_kind) == "observe" else "passive"
