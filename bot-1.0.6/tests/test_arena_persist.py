import asyncio
import json
import os
import tempfile
import types

import arena
import arena_games as ag


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self._mid = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self._mid += 1
        self.sent.append((chat_id, self._mid))
        return types.SimpleNamespace(message_id=self._mid)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, parse_mode=None):
        self.edited.append((chat_id, message_id))


def app_with(bot):
    return types.SimpleNamespace(bot=bot, bot_data={})


def test_every_game_state_is_json_safe(monkeypatch):
    for game_id, rules in arena.GAMES.items():
        players = [1] if game_id == "tictactoe" else [1, 2]
        if game_id == "quiz":
            import arena_content
            state = rules.start(players, arena_content.quiz_pick("en", 3))
        elif game_id == "tictactoe":
            state = rules.start(players, difficulty="easy")
        else:
            state = rules.start(players)
        json.dumps(state)  # must not raise


def test_match_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(arena, "PERSIST_PATH", tmp_path / "arena_matches.json")
    monkeypatch.setattr(arena, "MATCHES", {})
    monkeypatch.setattr(arena, "USER_MATCH", {})
    rules = arena.GAMES["tictactoe"]
    state = rules.start([1, 2], difficulty="medium")
    m = arena.Match(mid=7, game="tictactoe", mode="duo", players=[1, 2], state=state,
                    names={1: "A", 2: "B"}, langs={1: "en", 2: "en"}, msgs={1: (1, 101), 2: (2, 102)})
    arena.MATCHES[7] = m
    arena.USER_MATCH[1] = arena.USER_MATCH[2] = 7
    bot = FakeBot()
    app = app_with(bot)
    asyncio.run(arena._show(app, m))           # saves the match
    assert (tmp_path / "arena_matches.json").exists()

    # simulate a restart: memory is empty, the file is still there
    arena.MATCHES.clear()
    arena.USER_MATCH.clear()
    bot2 = FakeBot()
    count = asyncio.run(arena.restore(app_with(bot2)))
    assert count == 1
    back = arena.MATCHES[7]
    assert back.players == [1, 2] and back.names == {1: "A", 2: "B"}
    assert arena.USER_MATCH == {1: 7, 2: 7}
    assert back.state == json.loads(json.dumps(state))
    assert len(bot2.edited) == 2               # both players' boards were shown again


def test_finished_matches_are_not_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(arena, "PERSIST_PATH", tmp_path / "arena_matches.json")
    monkeypatch.setattr(arena, "MATCHES", {})
    m = arena.Match(mid=3, game="memory", mode="single", players=[5], state={}, names={5: "x"},
                    langs={5: "en"}, finished=True)
    arena.MATCHES[3] = m
    arena._persist()
    data = json.loads((tmp_path / "arena_matches.json").read_text(encoding="utf-8"))
    assert data["matches"] == []


def test_restore_without_file_is_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(arena, "PERSIST_PATH", tmp_path / "missing.json")
    assert asyncio.run(arena.restore(app_with(FakeBot()))) == 0
