"""Public projection of Runner step outcomes for human-facing adapters."""

from __future__ import annotations

from typing import Any, Dict


def public_step_status(context: Dict[str, Any] | None) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    phase_errors = context.get("phase_errors", []) or []
    first_error = phase_errors[0] if phase_errors and isinstance(phase_errors[0], dict) else {}
    if context.get("step_aborted"):
        status = "aborted"
        committed = False
    elif context.get("authoritative_step_failed"):
        status = "rolled_back"
        committed = False
    elif context.get("step_failed") and context.get("step_committed"):
        status = "delivery_failed"
        committed = True
    elif context.get("step_committed"):
        status = "committed"
        committed = True
    else:
        status = "unknown"
        committed = False
    return {
        "status": status,
        "committed": committed,
        "failure_phase": str(first_error.get("phase", ""))[:120],
        "failure_type": str(first_error.get("error_type", ""))[:120],
    }
