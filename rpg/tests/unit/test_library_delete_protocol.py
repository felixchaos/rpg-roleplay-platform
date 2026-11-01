"""Exercise the delete route and real library/registry/storage code on synthetic SQLite data."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_app import assets_registry, db, storage
from platform_app.api import library as library_api


@pytest.fixture
def sandbox(tmp_path):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        create table users (id integer primary key, username text, avatar_url text);
        create table scripts (id integer primary key, owner_id integer, title text, cover_image_url text);
        create table character_cards (id integer primary key, user_id integer, script_id integer, name text, avatar_path text);
        create table card_persona_images (id integer primary key, card_id integer, image_url text);
        create table user_assets (
            id integer primary key, user_id integer, kind text, storage_key text, url text,
            source text, ref_kind text, ref_id integer, mime text, size integer, meta text, created_at text
        );
    """)
    key = "library/synthetic.png"
    url = storage.public_url(key)
    path = tmp_path / key
    path.parent.mkdir()
    path.write_bytes(b"synthetic asset only")
    connection.execute("insert into user_assets values (123, 1, 'avatar', ?, ?, '', null, null, '', 20, '{}', '')", (key, url))
    connection.execute("insert into users values (1, 'synthetic-owner', null)")
    connection.execute("insert into users values (2, 'synthetic-other', '/unrelated.png')")

    class Adapter:
        def execute(self, sql, params=()):
            return connection.execute(sql.replace("%s", "?"), params)

    @contextmanager
    def connect():
        yield Adapter()
        connection.commit()

    app = FastAPI()
    app.include_router(library_api.router)
    app.dependency_overrides[library_api.require_user] = lambda: {"id": 1}
    # No app lifespan, production configuration, database pool, or external storage is used.
    with patch.object(db, "connect", connect), patch.object(db, "init_db"), \
         patch.object(assets_registry, "connect", connect), patch.object(assets_registry, "init_db"), \
         patch.object(storage, "PLATFORM_DATA_ROOT", tmp_path), TestClient(app) as client:
        yield client, connection, path, url
    connection.close()


def add_references(connection, url):
    connection.execute("update users set avatar_url = ? where id = 1", (url,))
    connection.execute("insert into scripts values (10, 1, 'synthetic script', ?)", (url,))
    connection.execute("insert into character_cards values (20, 1, null, 'user card', ?)", (url,))
    connection.execute("insert into character_cards values (21, null, 10, 'script card', ?)", (url,))
    connection.execute("insert into card_persona_images values (30, 20, ?)", (url,))
    connection.execute("insert into card_persona_images values (31, 21, ?)", (url,))
    connection.commit()


def assert_present(connection, path):
    assert connection.execute("select count(*) from user_assets").fetchone()[0] == 1
    assert path.exists()


def test_unreferenced_deletes_without_forcing(sandbox):
    client, connection, path, _url = sandbox
    response = client.post("/api/library/asset/123/delete", json={"confirm": False})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["deleted"] is True
    assert connection.execute("select count(*) from user_assets").fetchone()[0] == 0
    assert not path.exists()


@pytest.mark.parametrize("body", [None, {}, {"confirm": False}, {"confirm": "false"}, {"confirm": "true"}, {"confirm": 1}])
def test_references_require_literal_true_and_cancellation_preserves_data(sandbox, body):
    client, connection, path, url = sandbox
    add_references(connection, url)
    response = client.post("/api/library/asset/123/delete", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["needs_confirm"] is True
    assert {ref["kind"] for ref in payload["references"]} == {"avatar", "cover", "card_avatar", "persona_image"}
    assert_present(connection, path)
    # Cancellation means no follow-up request: all reference fields remain intact.
    assert connection.execute("select avatar_url from users where id = 1").fetchone()[0] == url
    assert connection.execute("select count(*) from card_persona_images").fetchone()[0] == 2


def test_explicit_confirmation_clears_owned_references_and_deletes_asset_and_file(sandbox):
    client, connection, path, url = sandbox
    add_references(connection, url)
    assert client.post("/api/library/asset/123/delete", json={}).json()["needs_confirm"]
    response = client.post("/api/library/asset/123/delete", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not path.exists()
    assert connection.execute("select count(*) from user_assets").fetchone()[0] == 0
    assert connection.execute("select avatar_url from users where id = 1").fetchone()[0] is None
    assert connection.execute("select cover_image_url from scripts").fetchone()[0] == ""
    assert [row[0] for row in connection.execute("select avatar_path from character_cards")] == ["", ""]
    assert connection.execute("select count(*) from card_persona_images").fetchone()[0] == 0
    assert connection.execute("select avatar_url from users where id = 2").fetchone()[0] == "/unrelated.png"


@pytest.mark.parametrize("asset_id", [123, 999])
def test_missing_or_other_users_asset_is_not_deleted(sandbox, asset_id):
    client, connection, path, _url = sandbox
    connection.execute("update user_assets set user_id = 2")
    response = client.post(f"/api/library/asset/{asset_id}/delete", json={"confirm": True})
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert_present(connection, path)


@pytest.mark.parametrize("body", [[], None, "yes", True])
def test_non_object_json_is_rejected_without_deleting(sandbox, body):
    client, connection, path, _url = sandbox
    response = client.post("/api/library/asset/123/delete", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert_present(connection, path)


def test_malformed_json_is_rejected_without_deleting(sandbox):
    client, connection, path, _url = sandbox
    response = client.post("/api/library/asset/123/delete", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert_present(connection, path)
