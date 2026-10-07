import json
import sys
from pathlib import Path


VENDOR_ROOT = Path(__file__).resolve().parents[2] / "docker" / "hermes-story" / "hermes-agent"
sys.path.insert(0, str(VENDOR_ROOT))

from hermes_state import SessionDB  # noqa: E402
from tools.session_search_tool import session_search  # noqa: E402


def _db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


def test_current_session_compaction_archive_is_searchable(tmp_path):
    db = _db(tmp_path)
    db.create_session("current", source="cli")
    db.append_message(
        "current",
        role="user",
        content="spectral phoenix requires moonstone bait",
    )
    db.archive_and_compact(
        "current",
        [{"role": "assistant", "content": "Earlier events were summarized."}],
    )

    result = json.loads(session_search(
        query="spectral phoenix",
        db=db,
        current_session_id="current",
    ))

    assert result["count"] == 1
    assert result["results"][0]["session_id"] == "current"

    match_id = result["results"][0]["match_message_id"]
    scroll = json.loads(session_search(
        session_id="current",
        around_message_id=match_id,
        db=db,
        current_session_id="current",
    ))
    assert scroll["success"] is True
    assert any("moonstone" in message.get("content", "") for message in scroll["messages"])


def test_current_session_live_message_stays_out_of_search(tmp_path):
    db = _db(tmp_path)
    db.create_session("current", source="cli")
    db.append_message(
        "current",
        role="user",
        content="crystal golem farming route",
    )

    result = json.loads(session_search(
        query="crystal golem",
        db=db,
        current_session_id="current",
    ))

    assert result["count"] == 0


def test_legacy_compression_parent_is_searchable_from_child(tmp_path):
    db = _db(tmp_path)
    db.create_session("parent", source="cli")
    db.append_message(
        "parent",
        role="user",
        content="void crystal requires a diamond pickaxe",
    )
    db.end_session("parent", "compression")
    db.create_session("child", source="cli", parent_session_id="parent")

    result = json.loads(session_search(
        query="void crystal",
        db=db,
        current_session_id="child",
    ))

    assert result["count"] == 1
    assert result["results"][0]["session_id"] == "parent"
