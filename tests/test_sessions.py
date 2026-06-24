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
        )
    )
    assert store.get(record.session_id)["prompt"] == "Draft a plan"
    assert store.list()[0]["session_id"] == record.session_id
