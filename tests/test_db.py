import sqlite3

from malt import db


def test_project_crud_logs_and_cascade(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "malt.db")
    db.init_db()
    project = db.create_project("demo", str(tmp_path), "write")
    assert db.get_project(project["id"])["name"] == "demo"
    assert db.list_projects()[0]["id"] == project["id"]
    updated = db.update_project(project["id"], name="renamed", ignored="nope")
    assert updated["name"] == "renamed"
    old_token = project["token"]
    new_token = db.regenerate_token(project["id"])
    assert new_token and new_token != old_token
    db.add_log(project["id"], "read_file", response="one")
    db.add_log(project["id"], "read_file", response="two", success=False)
    logs = db.get_logs(project["id"], limit=2)
    assert {row["response"] for row in logs} == {"one", "two"}
    assert len(db.get_logs(project["id"], limit=1)) == 1
    assert db.delete_project(project["id"])
    conn = sqlite3.connect(db.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
    conn.close()


def test_legacy_schema_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, root_path TEXT, "
        "permission TEXT, token TEXT)"
    )
    conn.commit()
    conn.close()
    db.init_db()
    columns = {row[1] for row in sqlite3.connect(db.DB_PATH).execute("PRAGMA table_info(projects)")}
    assert {"file_permissions", "terminal_permission", "domain_id", "icon_mode", "icon_path"} <= columns
