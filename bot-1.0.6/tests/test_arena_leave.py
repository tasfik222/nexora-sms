import asyncio
import types

import arena
from arena_content import quiz_pick


class FakeBot:
    def __init__(self):
        self.edits = []
        self._mid = 500

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self._mid += 1
        return types.SimpleNamespace(message_id=self._mid)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, parse_mode=None):
        self.edits.append((chat_id, message_id, text))


def make_match(monkeypatch, game="memory", players=(1, 2)):
    monkeypatch.setattr(arena, "MATCHES", {})
    monkeypatch.setattr(arena, "USER_MATCH", {})
    rules = arena.GAMES[game]
    state = rules.start(list(players))
    m = arena.Match(mid=11, game=game, mode="duo", players=list(players), state=state,
                    names={p: f"P{p}" for p in players}, langs={p: "en" for p in players},
                    msgs={p: (p, 900 + p) for p in players})
    arena.MATCHES[11] = m
    for p in players:
        arena.USER_MATCH[p] = 11
    return m


def test_running_match_always_has_leave_button(monkeypatch):
    m = make_match(monkeypatch)
    bot = FakeBot()
    app = types.SimpleNamespace(bot=bot, bot_data={})
    asyncio.run(arena._show(app, m))
    # the markup used for the edit must contain the leave callback
    markup = arena._with_leave(None, 11, "en")
    assert markup.inline_keyboard[0][0].callback_data == "ar:leave:11"


def test_leave_ends_match_for_everyone_without_rewards(monkeypatch):
    m = make_match(monkeypatch)
    bot = FakeBot()
    app = types.SimpleNamespace(bot=bot, bot_data={"store": None})
    asyncio.run(arena._finish(app, m, {}, abandoned=True))
    assert m.finished
    assert arena.USER_MATCH == {}           # both players are free to start another game
    assert m.state.get("_rewards") in (None, {})
    assert arena._active_match(1) is None


def test_match_can_be_left_by_a_player_only(monkeypatch):
    m = make_match(monkeypatch)
    assert arena._active_match(3) is None   # someone outside the match is not affected
    assert arena._active_match(1) is m
