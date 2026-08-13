from typing import Any, Dict, Iterable, List
import hashlib


class MemoryConsolidator:
    """Deterministically compact old routine logs without touching key events."""

    CADENCE_STEPS = 12
    MIN_AGE_STEPS = 24
    MIN_SOURCE_COUNT = 6
    MAX_SOURCE_COUNT = 12
    MAX_SCAN_COUNT = 100
    MAX_SUMMARY_CHARS = 4000
    LOW_SALIENCE_CEILING = 3.0

    def maybe_consolidate(
        self,
        memory: Any,
        *,
        current_step: int,
    ) -> Dict[str, Any]:
        step = int(current_step)
        if step <= 0 or step % self.CADENCE_STEPS != 0:
            return {"status": "not_due", "source_count": 0}
        if not all(
            hasattr(memory, method)
            for method in ("list_memories", "add_memory", "delete_memories")
        ):
            return {"status": "unsupported", "source_count": 0}
        cutoff = step - self.MIN_AGE_STEPS
        where = {
            "$and": [
                {"type": {"$eq": "episodic_log"}},
                {"salience": {"$lt": self.LOW_SALIENCE_CEILING}},
                {"step": {"$lte": cutoff}},
            ]
        }
        try:
            records = memory.list_memories(
                where=where,
                limit=self.MAX_SCAN_COUNT,
            )
        except Exception as exc:
            return {
                "status": "scan_failed",
                "source_count": 0,
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
        eligible = [
            item
            for item in records or []
            if self._eligible(item, cutoff=cutoff)
        ]
        eligible.sort(
            key=lambda item: (
                int(item.get("metadata", {}).get("step", 0) or 0),
                str(item.get("id", "")),
            )
        )
        if len(eligible) < self.MIN_SOURCE_COUNT:
            return {"status": "insufficient", "source_count": len(eligible)}
        selected = eligible[: self.MAX_SOURCE_COUNT]
        summary = self._summarize(selected)
        if not summary:
            return {"status": "empty", "source_count": len(selected)}
        steps = [
            int(item.get("metadata", {}).get("step", 0) or 0)
            for item in selected
        ]
        salience = min(
            self.LOW_SALIENCE_CEILING - 0.1,
            max(
                float(item.get("metadata", {}).get("salience", 1.0) or 1.0)
                for item in selected
            ),
        )
        metadata = {
            "step": max(steps),
            "start_step": min(steps),
            "end_step": max(steps),
            "type": "consolidated_summary",
            "phase_model": "host_deterministic_memory_consolidation",
            "salience": salience,
            "event_kinds": "routine_summary",
            "source_count": len(selected),
            "consolidation_step": step,
        }
        summary_id = "consolidated-" + hashlib.sha256(
            (
                f"{getattr(memory, 'namespace', '')}|"
                f"{getattr(memory, 'agent_name', '')}|{step}|"
                + "|".join(str(item.get("id", "")) for item in selected)
            ).encode("utf-8")
        ).hexdigest()
        try:
            try:
                memory.add_memory(
                    summary,
                    metadata=metadata,
                    memory_id=summary_id,
                )
            except TypeError:
                # Small test/custom memory adapters may implement the legacy
                # two-argument boundary; the host Memory component supports
                # stable upsert IDs used by real delivery retry.
                memory.add_memory(summary, metadata=metadata)
        except Exception as exc:
            return {
                "status": "write_failed",
                "source_count": len(selected),
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
        source_ids = [str(item.get("id", "")) for item in selected if item.get("id")]
        try:
            memory.delete_memories(source_ids)
        except Exception as exc:
            return {
                "status": "delete_failed",
                "source_count": len(selected),
                "summary_written": True,
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
        return {
            "status": "consolidated",
            "source_count": len(selected),
            "start_step": min(steps),
            "end_step": max(steps),
        }

    def _eligible(self, item: Dict[str, Any], *, cutoff: int) -> bool:
        if not isinstance(item, dict) or not item.get("id"):
            return False
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            return False
        try:
            salience = float(metadata.get("salience", 999.0))
            step = int(metadata.get("step", cutoff + 1))
        except (TypeError, ValueError):
            return False
        return (
            metadata.get("type") == "episodic_log"
            and salience < self.LOW_SALIENCE_CEILING
            and step <= cutoff
        )

    def _summarize(self, records: Iterable[Dict[str, Any]]) -> str:
        rows: List[str] = []
        for item in records:
            metadata = item.get("metadata", {})
            step = int(metadata.get("step", 0) or 0)
            snippets = self._snippets(str(item.get("content", "")))
            if snippets:
                rows.append(f"- Step {step}: {'；'.join(snippets)}")
        if not rows:
            return ""
        start = min(
            int(item.get("metadata", {}).get("step", 0) or 0)
            for item in records
        )
        end = max(
            int(item.get("metadata", {}).get("step", 0) or 0)
            for item in records
        )
        text = (
            f"Routine memory summary for steps {start}-{end}. "
            "These were low-salience repeated experiences:\n"
            + "\n".join(rows)
        )
        return text[: self.MAX_SUMMARY_CHARS]

    @staticmethod
    def _snippets(content: str) -> List[str]:
        ignored_headers = {
            "Timeline:",
            "Visible Intents:",
            "Visible Resolved:",
            "Rendered:",
            "Personal Outcome:",
        }
        snippets = []
        for raw in content.splitlines():
            line = " ".join(raw.split()).strip()
            if (
                not line
                or line.startswith("Step ")
                or line in ignored_headers
                or line in {"- None", "None", "{}", "[]"}
            ):
                continue
            snippets.append(line[:220])
            if len(snippets) >= 2:
                break
        return snippets
