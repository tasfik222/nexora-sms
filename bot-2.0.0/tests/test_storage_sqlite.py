import asyncio
import json

from storage import UserStore


def run(coro):
    return asyncio.run(coro)


def test_records_persist_in_sqlite_across_restarts(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("storage.STORAGE_BACKEND", "sqlite")
    st = UserStore()
    run(st.start())
    run(st.get(42, "alice"))
    st.credit(42, 500, "017", "123456", "voltx:a1")
    st.save(42)
    run(st.close())
    assert (tmp_path / "nexora.db").exists()

    st2 = UserStore()
    run(st2.start())
    rec = run(st2.get(42))
    assert rec["balance"] == 500
    assert rec["username"] == "alice"


def test_ban_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("storage.STORAGE_BACKEND", "sqlite")
    st = UserStore()
    run(st.start())
    run(st.set_banned(7, True))
    st2 = UserStore()
    run(st2.start())
    assert st2.is_banned(7)


def test_old_json_files_are_imported_once(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("storage.STORAGE_BACKEND", "sqlite")
    users = tmp_path / "users"
    users.mkdir(parents=True)
    (users / "9.json").write_text(json.dumps({"user_id": 9, "username": "old", "balance": 1200,
                                              "total_codes": 3, "history": [], "credited_otp_ids": [],
                                              "rev": 4}), encoding="utf-8")
    (users / "bad.json").write_text("{not json", encoding="utf-8")

    st = UserStore()
    run(st.start())
    assert run(st.get(9))["balance"] == 1200
    assert 9 in st.known_ids()
    assert (users / "9.json").exists()          # the JSON backup is kept

    # a second start must not import again over newer database data
    st.credit(9, 100, "017", "1", "voltx:x")
    st.save(9)
    run(st.close())
    st2 = UserStore()
    run(st2.start())
    assert run(st2.get(9))["balance"] == 1300


def test_json_backend_still_works(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("storage.STORAGE_BACKEND", "json")
    st = UserStore()
    run(st.start())
    run(st.get(5, "bob"))
    assert (tmp_path / "users" / "5.json").exists()
    assert not (tmp_path / "nexora.db").exists()
