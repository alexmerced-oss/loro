import pytest

from loro.config import SessionConfig
from loro.sessions import SessionRecord, SessionStore


def test_session_store_roundtrip(tmp_path) -> None:
    store = SessionStore(SessionConfig(path=str(tmp_path)))
    record = store.save(
        SessionRecord(
            prompt="Draft a plan",
            mode="plan",
            summary="A plan",
            recalled_memories=["Use concise bullets"],
            identity={"subject": "user-123", "tenant": "acme"},
        )
    )
    assert store.get(record.session_id)["prompt"] == "Draft a plan"
    assert store.get(record.session_id)["identity"]["subject"] == "user-123"
    assert store.list()[0]["session_id"] == record.session_id


def test_session_store_rejects_path_traversal_and_oversized_records(tmp_path) -> None:
    config = SessionConfig(path=str(tmp_path / "sessions"), max_record_bytes=1024)
    store = SessionStore(config)

    with pytest.raises(ValueError, match="Session ids"):
        store.get("../../outside")
    with pytest.raises(ValueError, match="managed limit"):
        store.save(
            SessionRecord(
                session_id="bounded",
                prompt="x" * 2000,
                mode="run",
                summary="summary",
            )
        )
    assert not (tmp_path / "sessions" / "bounded.json").exists()
