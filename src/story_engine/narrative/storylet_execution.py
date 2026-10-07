"""Condition-triggered World intents, independent of the story proposal agent."""

from typing import Any, Dict, List


class StoryletExecution:
    ACTIVE_FLAG = "active_storylet_triggers"
    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def prepare(self, scene: Any, active: List[Dict[str, Any]], step: int,
                location: str = "", tracking: Any = None) -> List[Dict[str, Any]]:
        previous = set(scene.get_scene_flag(self.ACTIVE_FLAG, []) or []) if scene else set()
        triggers = []
        for item in active:
            tracker = tracking.agents.get(item["storylet_id"]) if tracking else None
            if tracker is not None:
                if tracker.closed or tracker.pending_advance is None:
                    continue
                intent = tracker.pending_advance
            else:
                if not item.get("trigger") and item["storylet_id"] in previous:
                    continue
                intent = item["intent"]
            triggers.append({
            "actor": "World", "intent": intent, "source": "storylet",
            "storylet_direction": item["intent"],
            "source_storylet_id": item["storylet_id"],
            "event_id": f"storylet:{item['storylet_id']}:{step}",
            "action_kind": "interact", "action_target": "", "location": str(item.get("location", "") or ""),
            "trigger": item.get("trigger", ""),
            "already_triggered": item["storylet_id"] in previous,
            "continuation": bool(tracker is not None and item["storylet_id"] in previous),
        })
        return triggers

    def validate(self, triggers: List[Dict[str, Any]], result: Dict[str, Any], scene: Any = None) -> None:
        """Omission/duplicate citations fail the step before any world writes."""
        actions = result.get("resolved_actions", []) or []
        eligible = {item["source_storylet_id"] for item in triggers}
        for action in actions:
            if not isinstance(action, dict):
                continue
            source = action.get("source_storylet_id")
            if source and (source not in eligible or action.get("actor") != "World"):
                raise RuntimeError(f"Unauthorized storylet occurrence: {source}")
        for trigger in triggers:
            matches = [a for a in actions if isinstance(a, dict)
                       and a.get("actor") == "World"
                       and a.get("source_storylet_id") == trigger["source_storylet_id"]
                       and a.get("intent") == trigger["intent"]]
            if len(matches) != 1 or matches[0].get("outcome") not in (
                    self.POSITIVE_OUTCOMES | {"blocked", "failure", "deferred", "inactive"}):
                raise RuntimeError(f"Unresolved storylet trigger: {trigger['source_storylet_id']}")
            if (matches[0].get("outcome") in self.POSITIVE_OUTCOMES
                    and trigger.get("already_triggered") and not trigger.get("continuation")):
                raise RuntimeError(f"Storylet already occurred: {trigger['source_storylet_id']}")
            if matches[0].get("outcome") in self.POSITIVE_OUTCOMES and not str(matches[0].get("result", "")).strip():
                raise RuntimeError(f"Storylet requires an observable event: {trigger['source_storylet_id']}")

            if matches[0].get("outcome") in self.POSITIVE_OUTCOMES:
                location = str(matches[0].get("location", "") or "").strip()
                if not location:
                    raise RuntimeError(f"Storylet requires an event location: {trigger['source_storylet_id']}")
                if trigger.get("location") and not trigger.get("continuation") and location != trigger["location"]:
                    raise RuntimeError(f"Storylet event changed its authored location: {trigger['source_storylet_id']}")
                if scene is not None and location not in scene.get_known_locations():
                    raise RuntimeError(f"Storylet event location is unknown: {location}")

        suggestions = result.get("director_suggestions", [])
        if not isinstance(suggestions, list) or len(suggestions) > 16:
            raise RuntimeError("director_suggestions must be a list of at most 16 messages")
        for suggestion in suggestions:
            if (not isinstance(suggestion, dict)
                    or suggestion.get("source_storylet_id") not in eligible
                    or not isinstance(suggestion.get("recipient"), str)
                    or not isinstance(suggestion.get("text"), str)
                    or not suggestion["text"].strip()):
                raise RuntimeError("Invalid director suggestion source, recipient or text")
            source = suggestion["source_storylet_id"]
            if any(a.get("source_storylet_id") == source and a.get("outcome") == "inactive"
                   for a in actions if isinstance(a, dict)):
                raise RuntimeError("Inactive storylet cannot send director suggestions")
            if scene is not None and suggestion["recipient"] not in scene.actor_states:
                raise RuntimeError(f"Unknown suggestion recipient: {suggestion['recipient']}")

    def commit(self, scene: Any, active: List[Dict[str, Any]],
               triggers: List[Dict[str, Any]], result: Dict[str, Any],
               tracking: Any = None, step: int = 0) -> None:
        previous = set(scene.get_scene_flag(self.ACTIVE_FLAG, []) or [])
        active_ids = {item["storylet_id"] for item in active}
        if tracking:
            active_ids.update(sid for sid, tracker in tracking.agents.items() if not tracker.closed)
        trigger_ids = {item["source_storylet_id"] for item in triggers}
        hits = [a["source_storylet_id"] for a in result.get("resolved_actions", [])
                if isinstance(a, dict) and a.get("actor") == "World"
                and a.get("source_storylet_id") in trigger_ids
                and a.get("outcome") in self.POSITIVE_OUTCOMES]
        # A reusable block rearms after its condition has become false. Failed
        # triggers stay eligible; a one-shot is consumed only after commit.
        inactive = {a.get("source_storylet_id") for a in result.get("resolved_actions", [])
                    if isinstance(a, dict) and a.get("actor") == "World" and a.get("outcome") == "inactive"}
        # Starting guards apply until the first event; ongoing tracker work can
        # then continue across changes to the original triggering situation.
        continuing_ids = {sid for sid in previous if tracking and sid in tracking.agents}
        suggested = {item["source_storylet_id"] for item in result.get("director_suggestions", [])
                     if tracking and item["source_storylet_id"] in tracking.agents}
        armed = ((previous & active_ids) - (inactive - continuing_ids)) | set(hits) | suggested
        if armed != previous:
            scene.update_scene_flags({self.ACTIVE_FLAG: sorted(armed)})
        result["storylet_hits"] = list(dict.fromkeys(hits))
        one_shots = {item["storylet_id"] for item in active if item.get("one_shot")
                     and not (tracking and item["storylet_id"] in tracking.agents)}
        consumed = list(scene.get_scene_flag("consumed_storylets", []) or [])
        new_consumed = [sid for sid in hits if sid in one_shots and sid not in consumed]
        if new_consumed:
            scene.update_scene_flags({"consumed_storylets": consumed + new_consumed})
        if tracking:
            tracking.commit_execution(triggers, result, step)
