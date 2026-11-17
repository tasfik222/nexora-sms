"""End-to-end checks of the Arena (Telegram side) with a real UserStore in a temp folder and a fake Bot.
Run from the project folder:  python -m pytest -q tests"""
import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["LOCAL_DATA_DIR"] = tempfile.mkdtemp()

import arena  # noqa: E402
import arena_games  # noqa: E402
from storage import UserStore  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent, self.edits, self._id = [], [], 100

    async def send_message(self, chat_id, text, **kw):
        self._id += 1
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=self._id)

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        self.edits.append((chat_id, message_id, text))


async def make_app():
    store = UserStore()
    await store.start()
    app = types.SimpleNamespace(bot=FakeBot(), bot_data={"store": store})
    return app


async def load(app, uid):
    return await app.bot_data["store"].get(uid, f"user{uid}")


def test_single_quiz_pays_game_coins_not_sms_balance():
    async def run():
        app = await make_app()
        rec_before = await load(app, 1)
        m = await arena._start_match(app, "quiz", "single", [1])
        assert m.state["questions"] and m.msgs[1][0] == 1
        for _ in range(5):
            r = m.state["round"]
            correct = m.state["questions"][r]["answer"]
            await arena._apply_move(app, m, 1, f"{r}:{correct}", reply=lambda t: asyncio.sleep(0))
        assert m.finished
        assert rec_before["balance"] == 0
        rec = await load(app, 1)
        assert rec["balance"] == 0, "game must never change the SMS balance"
        prof = arena.profile_of(rec)
        assert prof["played"] == 1 and prof["wins"] == 1 and prof["xp"] == 30 and prof["coins"] == 140
        assert prof["elo"] == arena.DEFAULT_ELO, "single-player games do not change ELO"
        assert arena.USER_MATCH.get(1) is None
    asyncio.run(run())


def test_quiz_timeout_moves_on_when_one_player_is_idle(monkeypatch):
    monkeypatch.setattr(arena, "ROUND_SECONDS", 0.3)

    async def run():
        app = await make_app()
        m = await arena._start_match(app, "quiz", "duo", [1, 2])
        await arena._apply_move(app, m, 1, "0:0", reply=lambda t: asyncio.sleep(0))
        assert m.state["round"] == 0            # player 2 is still thinking
        await asyncio.sleep(0.4)                # timer ends the round (0.3s), next round not yet over
        assert m.state["round"] == 1
        assert m.state["answers"] == {}
    asyncio.run(run())


def test_duo_tictactoe_win_updates_elo_and_stats():
    async def run():
        app = await make_app()
        m = await arena._start_match(app, "tictactoe", "duo", [1, 2])
        for uid, spot in [(1, 0), (2, 3), (1, 1), (2, 4), (1, 2)]:
            await arena._apply_move(app, m, uid, str(spot), reply=lambda t: asyncio.sleep(0))
        assert m.finished
        p1 = arena.profile_of(await load(app, 1))
        p2 = arena.profile_of(await load(app, 2))
        assert p1["elo"] == arena.DEFAULT_ELO + 20 and p2["elo"] == arena.DEFAULT_ELO - 12
        assert p1["stats"]["tictactoe"] == {"played": 1, "wins": 1}
        assert p2["stats"]["tictactoe"] == {"played": 1, "wins": 0}
    asyncio.run(run())


def test_invalid_and_out_of_turn_moves_are_refused():
    async def run():
        app = await make_app()
        m = await arena._start_match(app, "tictactoe", "duo", [1, 2])
        seen = []

        async def reply(t):
            seen.append(t)
        await arena._apply_move(app, m, 2, "0", reply=reply)     # player 2 is not first
        await arena._apply_move(app, m, 1, "0", reply=reply)
        await arena._apply_move(app, m, 2, "0", reply=reply)     # cell taken
        assert len(seen) == 2 and m.state["board"][0] == "X"
    asyncio.run(run())


def test_queue_matches_two_players_with_similar_elo():
    arena.QUEUES.clear()
    assert arena._enqueue(1, 1000, "memory", "duo", 2) is None
    assert arena._enqueue(2, 1010, "memory", "duo", 2) == [2, 1]


def test_room_start_needs_enough_players_and_keeps_host():
    async def run():
        app = await make_app()
        arena.ROOMS.clear(); arena.USER_ROOM.clear()
        arena.ROOMS["ABC123"] = arena.Room("ABC123", 1, "memory", "duo", 2, [1], {1: "Host"})
        arena.USER_ROOM[1] = "ABC123"
        alerts = []

        async def alert(t):
            alerts.append(t)
        q = types.SimpleNamespace(edit_message_text=lambda *a, **k: asyncio.sleep(0))
        await arena._room_start(app, q, "ABC123", 1, "en", alert)
        assert "ABC123" in arena.ROOMS      # not enough players yet
        arena.ROOMS["ABC123"].members.append(2)
        arena.USER_ROOM[2] = "ABC123"
        await arena._room_start(app, q, "ABC123", 1, "en", alert)
        assert "ABC123" not in arena.ROOMS and arena.USER_MATCH.get(1) is not None
    asyncio.run(run())


def test_daily_reward_once_per_day_and_coins_separate():
    async def run():
        app = await make_app()
        rec = await load(app, 5)
        prof = arena.profile_of(rec)
        first = arena._claim_daily(app, rec, prof, 5, "en")
        second = arena._claim_daily(app, rec, prof, 5, "en")
        assert "+50" in first and "+20 XP" in first
        assert "Come back" in second
        assert prof["coins"] == arena.STARTING_COINS + 50 and prof["xp"] == 20
        assert rec["balance"] == 0
    asyncio.run(run())


def test_bangla_and_english_strings_have_same_keys():
    assert set(arena.S["en"]) == set(arena.S["bn"])


def test_scramble_text_only_counts_during_scramble():
    async def run():
        app = await make_app()
        m = await arena._start_match(app, "scramble", "single", [7])
        target = m.state["target"]
        msg = types.SimpleNamespace(text=target, reply_text=lambda *a, **k: asyncio.sleep(0))
        upd = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=7), effective_message=msg)
        await arena.on_arena_text(upd, types.SimpleNamespace(application=app))
        assert m.finished
    asyncio.run(run())
