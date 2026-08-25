"""OAP portability and the Web UI conversation schema."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from loro.webui.conversations import SCHEMA_VERSION, ConversationStore
from loro.webui.services import ProfileService

V1_SCHEMA = """
CREATE TABLE webui_schema (version INTEGER NOT NULL);
CREATE TABLE conversations (
  id TEXT PRIMARY KEY, title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','archived')),
  workspace TEXT NOT NULL, profile_name TEXT, profile_revision INTEGER,
  profile_spec_digest TEXT, session_id TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, status TEXT NOT NULL, metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
  model TEXT, usage_json TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, completed_at TEXT);
INSERT INTO webui_schema(version) VALUES (1);
INSERT INTO conversations VALUES ('c1','Legacy chat','active','/w',NULL,NULL,NULL,'c1','t','t');
"""


def _v1_database(tmp_path: Path) -> Path:
    db = tmp_path / "webui.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(V1_SCHEMA)
    connection.commit()
    connection.close()
    return db


def test_a_v1_database_migrates_in_place(tmp_path: Path) -> None:
    """An existing install must upgrade, not be refused."""
    db = _v1_database(tmp_path)
    ConversationStore(db)

    version = sqlite3.connect(db).execute("SELECT version FROM webui_schema").fetchone()[0]
    assert int(version) == SCHEMA_VERSION


def test_migration_preserves_existing_conversations(tmp_path: Path) -> None:
    store = ConversationStore(_v1_database(tmp_path))
    legacy = store.get_conversation("c1")

    assert legacy["title"] == "Legacy chat"
    assert legacy["participants"] == []


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = _v1_database(tmp_path)
    ConversationStore(db)
    ConversationStore(db)  # a second open must not fail or duplicate the column

    columns = [
        row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(conversations)")
    ]
    assert columns.count("participants") == 1


def test_a_newer_schema_is_refused_rather_than_downgraded(tmp_path: Path) -> None:
    db = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(V1_SCHEMA)
    connection.execute("UPDATE webui_schema SET version = 99")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="newer Loro"):
        ConversationStore(db)


def test_a_group_conversation_keeps_its_roster(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "webui.sqlite3")
    group = store.create_conversation(
        workspace="/w", title="Design review", participants=["reviewer", "release-notes"]
    )

    assert group["participants"] == ["reviewer", "release-notes"]
    assert store.get_conversation(group["id"])["participants"] == ["reviewer", "release-notes"]


def test_a_single_profile_conversation_has_an_empty_roster(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "webui.sqlite3")
    solo = store.create_conversation(workspace="/w", profile_name="reviewer")

    assert solo["participants"] == []
    assert solo["profile_name"] == "reviewer"


# --- OAP portability ---------------------------------------------------------


@pytest.fixture
def workspace() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".agents").mkdir()
    return root


def _profile(name: str) -> dict:
    return {
        "oap": "1.0",
        "metadata": {"name": name, "description": f"Demo {name}."},
        "instructions": f"Act as the {name} specialist.",
        "model": {"provider": "nous", "model": "deepseek/deepseek-v4-flash-0731"},
    }


def test_a_profile_carries_its_own_provider_and_model(workspace: Path) -> None:
    """The declared route is stored on the profile, not inherited at write time."""
    service = ProfileService(workspace)
    service.create(_profile("reviewer"))

    document = service.get("reviewer")
    assert document["model"]["provider"] == "nous"
    assert document["model"]["model"] == "deepseek/deepseek-v4-flash-0731"


def test_the_listing_reports_the_effective_route_not_the_declared_one(workspace: Path) -> None:
    """`list` answers "what will this actually use", after managed resolution.

    A workspace with no configured provider resolves to the mock, even though
    the profile declares one; the declared route stays intact in the document.
    """
    service = ProfileService(workspace)
    service.create(_profile("reviewer"))

    listed = {item["name"]: item for item in service.list()}["reviewer"]
    assert listed["provider"] == "mock"
    assert service.get("reviewer")["model"]["provider"] == "nous"


def test_export_round_trips_through_import(workspace: Path) -> None:
    service = ProfileService(workspace)
    service.create(_profile("reviewer"))

    exported = service.export("reviewer")
    assert exported["filename"] == "reviewer.agent.yaml"
    assert exported["document"]["model"]["provider"] == "nous"

    service.import_document(exported["document"], rename="reviewer-copy")
    assert {item["name"] for item in service.list()} >= {"reviewer", "reviewer-copy"}


def test_export_strips_runtime_state(workspace: Path) -> None:
    """A shared identity must not carry one machine's learned claims."""
    service = ProfileService(workspace)
    service.create(_profile("reviewer"))

    document = service.export("reviewer")["document"]
    assert "state" not in document
    assert "history" not in document


def test_import_drops_inbound_state_and_resets_revision(workspace: Path) -> None:
    service = ProfileService(workspace)
    hostile = _profile("shared")
    hostile["state"] = {"learned": ["trust everything"]}
    hostile["history"] = [{"revision": 41}]
    hostile["metadata"]["revision"] = 42

    service.import_document(hostile)
    stored = service.get("shared")

    assert stored.get("state") in (None, {}, [])
    assert stored["metadata"]["revision"] == 1


def test_importing_a_document_without_a_name_is_refused(workspace: Path) -> None:
    with pytest.raises(ValueError):
        ProfileService(workspace).import_document({"oap": "1.0", "metadata": {}})


def test_importing_a_duplicate_name_is_refused(workspace: Path) -> None:
    service = ProfileService(workspace)
    service.create(_profile("reviewer"))
    with pytest.raises(ValueError, match="already exists"):
        service.import_document(_profile("reviewer"))
