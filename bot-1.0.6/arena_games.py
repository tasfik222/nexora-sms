"""
arena_games.py - the rules of the Arena mini-games (no Telegram, no storage, pure Python).

Ported from the separate aiogram game bot, with these fixes:
  * quiz: a round that one player never answers is ended by a timer (`skip_round`) - before, one idle
    player could stall the whole match forever.
  * quiz: counts correct answers, so a single-player quiz has a real win condition.
  * winners are always a user id, 'bot' or 'draw' - the UI turns ids into names (before: raw numbers).
  * `outcome()` works out win / loss / draw for every player, so the rewards can be paid
    (the old bot never called its award code at all).
"""
from __future__ import annotations

import random
import string
import time

WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
ROUND_SECONDS = 20          # quiz: time allowed per question before the round is skipped
SCRAMBLE_WORDS = ['planet', 'garden', 'python', 'river', 'friend']
MEMORY_SYMBOLS = ['🍎', '🚀', '🐼', '🌙', '🎸', '🌻']

QUIZ_QUESTIONS = {'en': [{'prompt': 'Which planet is known as the Red Planet?',
         'choices': ['Venus', 'Mars', 'Jupiter', 'Mercury'],
         'answer': 1},
        {'prompt': 'What is the chemical symbol for water?',
         'choices': ['CO2', 'H2O', 'O2', 'NaCl'],
         'answer': 1},
        {'prompt': 'How many players are on a football team on the field?',
         'choices': ['9', '10', '11', '12'],
         'answer': 2},
        {'prompt': 'How many pillars of Islam are there?', 'choices': ['3', '4', '5', '6'], 'answer': 2},
        {'prompt': 'What is the capital of Bangladesh?',
         'choices': ['Dhaka', 'Chattogram', 'Khulna', 'Sylhet'],
         'answer': 0},
        {'prompt': 'What does CPU stand for?',
         'choices': ['Central Processing Unit',
                     'Computer Power Utility',
                     'Core Program User',
                     'Central Program Unit'],
         'answer': 0}],
 'bn': [{'prompt': 'লাল গ্রহ নামে পরিচিত কোনটি?',
         'choices': ['শুক্র', 'মঙ্গল', 'বৃহস্পতি', 'বুধ'],
         'answer': 1},
        {'prompt': 'পানির রাসায়নিক সংকেত কী?', 'choices': ['CO2', 'H2O', 'O2', 'NaCl'], 'answer': 1},
        {'prompt': 'ফুটবল মাঠে এক দলে কতজন খেলোয়াড় থাকে?', 'choices': ['৯', '১০', '১১', '১২'], 'answer': 2},
        {'prompt': 'ইসলামের স্তম্ভ কয়টি?', 'choices': ['৩', '৪', '৫', '৬'], 'answer': 2},
        {'prompt': 'বাংলাদেশের রাজধানী কোনটি?',
         'choices': ['ঢাকা', 'চট্টগ্রাম', 'খুলনা', 'সিলেট'],
         'answer': 0},
        {'prompt': 'CPU-এর পূর্ণরূপ কী?',
         'choices': ['Central Processing Unit',
                     'Computer Power Utility',
                     'Core Program User',
                     'Central Program Unit'],
         'answer': 0}]}


def _winner_of_lines(board: list[str], mark: str) -> bool:
    return any(board[a] == board[b] == board[c] == mark for a, b, c in WIN_LINES)


class TicTacToe:
    game_id = 'tictactoe'

    def start(self, players: list[int], difficulty: str = 'medium') -> dict:
        return {'players': list(players), 'board': [' '] * 9, 'turn': 0, 'winner': None,
                'vs_bot': len(players) == 1, 'difficulty': difficulty}

    def handle_action(self, s: dict, p: int, a: str) -> dict:
        if p not in s['players'] or s['winner'] or (not s['vs_bot'] and s['players'][s['turn']] != p):
            return s
        try:
            i = int(a)
        except (TypeError, ValueError):
            return s
        if not 0 <= i < 9 or s['board'][i] != ' ':
            return s
        mark = 'X' if s['vs_bot'] else 'XO'[s['turn'] % 2]     # 4-player relay: players alternate X / O teams
        s['board'][i] = mark
        if _winner_of_lines(s['board'], mark):
            s['winner'] = p
            return s
        if ' ' not in s['board']:
            s['winner'] = 'draw'
            return s
        if s['vs_bot']:
            available = [k for k, v in enumerate(s['board']) if v == ' ']
            if s['difficulty'] == 'hard':
                bot_move = self.best_move(s['board'], 'O')
            elif s['difficulty'] == 'easy':
                bot_move = random.choice(available)
            else:
                bot_move = next((k for k in available if self._would_win(s['board'], k, 'O')), None)
                if bot_move is None:
                    bot_move = next((k for k in available if self._would_win(s['board'], k, 'X')), None)
                if bot_move is None:
                    bot_move = random.choice(available)
            s['board'][bot_move] = 'O'
            if _winner_of_lines(s['board'], 'O'):
                s['winner'] = 'bot'
            elif ' ' not in s['board']:
                s['winner'] = 'draw'
        else:
            s['turn'] = (s['turn'] + 1) % len(s['players'])
        return s

    @staticmethod
    def _would_win(board: list[str], index: int, mark: str) -> bool:
        copy = board.copy()
        copy[index] = mark
        return _winner_of_lines(copy, mark)

    def best_move(self, board: list[str], mark: str = 'O') -> int | None:
        def solve(b: list[str], turn: str) -> int:
            for line in WIN_LINES:
                vals = [b[k] for k in line]
                if vals[0] != ' ' and vals.count(vals[0]) == 3:
                    return 1 if vals[0] == mark else -1
            if ' ' not in b:
                return 0
            scores = []
            for k, v in enumerate(b):
                if v == ' ':
                    c = b.copy()
                    c[k] = turn
                    scores.append(solve(c, 'X' if turn == 'O' else 'O'))
            return max(scores) if turn == mark else min(scores)

        options = []
        for k, v in enumerate(board):
            if v == ' ':
                c = board.copy()
                c[k] = mark
                options.append((solve(c, 'X' if mark == 'O' else 'O'), k))
        return max(options)[1] if options else None

    def get_result(self, s: dict) -> dict | None:
        return {'winner': s['winner']} if s['winner'] is not None else None


class Quiz:
    game_id = 'quiz'

    def start(self, players: list[int], questions: list[dict]) -> dict:
        return {'players': list(players), 'questions': questions, 'round': 0,
                'scores': {str(p): 0 for p in players}, 'correct': {str(p): 0 for p in players},
                'answers': {}, 'started': time.time()}

    def handle_action(self, s: dict, p: int, a: str) -> dict:
        if s['round'] >= len(s['questions']) or p not in s['players'] or str(p) in s['answers']:
            return s
        q = s['questions'][s['round']]
        try:
            index = int(a)
        except (TypeError, ValueError):
            return s
        if not 0 <= index < len(q['choices']):
            return s
        if index == q['answer']:
            elapsed = time.time() - s['started']
            s['scores'][str(p)] += max(10, 100 - int(elapsed * 8))
            s['correct'][str(p)] += 1
        s['answers'][str(p)] = index
        if len(s['answers']) >= len(s['players']):
            self._advance(s)
        return s

    def skip_round(self, s: dict) -> dict:
        """Timer ran out: nobody else gets points for this question, move on."""
        if s['round'] < len(s['questions']):
            self._advance(s)
        return s

    @staticmethod
    def _advance(s: dict) -> None:
        s['round'] += 1
        s['answers'] = {}
        s['started'] = time.time()

    def get_result(self, s: dict) -> dict | None:
        if s['round'] >= len(s['questions']):
            return {'scores': s['scores'], 'correct': s['correct']}
        return None


class Scramble:
    game_id = 'scramble'

    def start(self, players: list[int], words: list[str] | None = None) -> dict:
        target = random.choice(words or SCRAMBLE_WORDS)
        scrambled = ''.join(random.sample(target, len(target)))
        if len(set(target)) > 1 and scrambled == target:
            scrambled = target[1:] + target[:1]
        return {'players': list(players), 'target': target, 'scrambled': scrambled, 'winner': None,
                'scores': {str(p): 0 for p in players}}

    def handle_action(self, s: dict, p: int, a: str) -> dict:
        if p in s['players'] and s['winner'] is None and a.strip().casefold() == s['target'].casefold():
            s['winner'] = p
            s['scores'][str(p)] = 100
        return s

    def get_result(self, s: dict) -> dict | None:
        return {'winner': s['winner'], 'scores': s['scores']} if s['winner'] is not None else None


class CodeBreaker:
    game_id = 'codebreaker'

    def start(self, players: list[int], **_) -> dict:
        return {'players': list(players), 'secret': ''.join(random.choices(string.digits, k=4)),
                'attempts': [], 'winner': None, 'turn': 0}

    def handle_action(self, s: dict, p: int, a: str) -> dict:
        if p not in s['players'] or s['winner'] is not None or not isinstance(a, str) or len(a) != 4 or not a.isdigit():
            return s
        if len(s['players']) > 1 and s['players'][s['turn']] != p:
            return s
        exact = sum(x == y for x, y in zip(a, s['secret']))
        misplaced = sum(min(a.count(d), s['secret'].count(d)) for d in set(a)) - exact
        s['attempts'].append({'player': p, 'guess': a, 'exact': exact, 'misplaced': misplaced})
        if exact == 4:
            s['winner'] = p
        else:
            s['turn'] = (s['turn'] + 1) % len(s['players'])
        return s

    def get_result(self, s: dict) -> dict | None:
        return {'winner': s['winner']} if s['winner'] is not None else None


class Memory:
    game_id = 'memory'

    def start(self, players: list[int], **_) -> dict:
        cards = MEMORY_SYMBOLS * 2
        random.shuffle(cards)
        return {'players': list(players), 'cards': cards, 'revealed': [], 'matched': [], 'turn': 0,
                'scores': {str(p): 0 for p in players}}

    def handle_action(self, s: dict, p: int, a: str) -> dict:
        if not s['players'] or p not in s['players'] or s['players'][s['turn']] != p or not isinstance(a, str) or not a.isdigit():
            return s
        i = int(a)
        if i < 0 or i >= len(s['cards']) or i in s['matched']:
            return s
        if len(s['revealed']) == 2:
            s['revealed'] = []
        if i in s['revealed']:
            return s
        s['revealed'].append(i)
        if len(s['revealed']) == 2:
            x, y = s['revealed']
            if s['cards'][x] == s['cards'][y]:
                s['matched'] += [x, y]
                s['scores'][str(p)] += 1
                s['revealed'] = []
            else:
                s['turn'] = (s['turn'] + 1) % len(s['players'])
        return s

    def get_result(self, s: dict) -> dict | None:
        return {'scores': s['scores']} if len(s['matched']) == len(s['cards']) else None


GAMES = {g.game_id: g for g in (TicTacToe(), Quiz(), Scramble(), CodeBreaker(), Memory())}
GAME_ORDER = ['quiz', 'scramble', 'codebreaker', 'tictactoe', 'memory']


def outcome(game_id: str, state: dict, result: dict) -> dict[int, str]:
    """Each real player -> 'win' / 'loss' / 'draw' for this finished game (used to pay rewards)."""
    players = state['players']
    if game_id == 'tictactoe':
        w = result['winner']
        if state['vs_bot']:
            return {players[0]: {'draw': 'draw', 'bot': 'loss'}.get(w, 'win')}
        if w == 'draw':
            return {p: 'draw' for p in players}
        # relay teams: players at the same turn parity share the winner's side
        win_side = players.index(w) % 2
        return {p: 'win' if i % 2 == win_side else 'loss' for i, p in enumerate(players)}
    if game_id in ('scramble', 'codebreaker'):
        w = result['winner']
        return {p: 'win' if p == w else 'loss' for p in players}
    if game_id == 'quiz':
        if len(players) == 1:
            return {players[0]: 'win' if result['correct'][str(players[0])] >= 3 else 'loss'}
        return _by_score(players, result['scores'])
    if game_id == 'memory':
        if len(players) == 1:
            return {players[0]: 'win'}
        return _by_score(players, result['scores'])
    return {p: 'draw' for p in players}


def _by_score(players: list[int], scores: dict) -> dict[int, str]:
    best = max(scores[str(p)] for p in players)
    tops = [p for p in players if scores[str(p)] == best]
    if len(tops) > 1:
        return {p: 'draw' for p in players}
    return {p: 'win' if p in tops else 'loss' for p in players}
