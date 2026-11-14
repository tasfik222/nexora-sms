"""Rules tests for the Arena games. Run from the project folder:  python -m pytest -q tests"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena_games import CodeBreaker, Memory, Quiz, Scramble, TicTacToe, outcome  # noqa: E402

Q = [{'prompt': 'p', 'choices': ['A', 'B', 'C'], 'answer': 2}]


def play(game, state, moves):
    for player, spot in moves:
        state = game.handle_action(state, player, spot)
    return state


def test_tictactoe_win():
    g = TicTacToe()
    s = play(g, g.start([1, 2]), [(1, '0'), (2, '3'), (1, '1'), (2, '4'), (1, '2')])
    assert g.get_result(s) == {'winner': 1}


def test_tictactoe_invalid_move_does_not_change_board():
    g = TicTacToe()
    s = play(g, g.start([1, 2]), [(2, '0')])
    assert s['board'] == [' '] * 9


def test_tictactoe_full_board_is_draw():
    g = TicTacToe()
    moves = [(1, '0'), (2, '1'), (1, '2'), (2, '4'), (1, '3'), (2, '5'), (1, '7'), (2, '6'), (1, '8')]
    s = play(g, g.start([1, 2]), moves)
    assert g.get_result(s) == {'winner': 'draw'}


def test_tictactoe_four_player_relay_alternates_teams():
    g = TicTacToe()
    s = play(g, g.start([1, 2, 3, 4]), [(1, '0'), (2, '3'), (3, '1'), (4, '4'), (1, '2')])
    assert s['winner'] == 1 and s['board'][:3] == ['X', 'X', 'X']
    out = outcome('tictactoe', s, {'winner': 1})
    assert out == {1: 'win', 2: 'loss', 3: 'win', 4: 'loss'}


def test_tictactoe_vs_bot_outcomes():
    g = TicTacToe()
    assert outcome('tictactoe', {'players': [5], 'vs_bot': True}, {'winner': 5}) == {5: 'win'}
    assert outcome('tictactoe', {'players': [5], 'vs_bot': True}, {'winner': 'bot'}) == {5: 'loss'}
    assert outcome('tictactoe', {'players': [5], 'vs_bot': True}, {'winner': 'draw'}) == {5: 'draw'}


def test_codebreaker_feedback():
    g = CodeBreaker()
    s = g.start([1, 2])
    s['secret'] = '1234'
    s = g.handle_action(s, 1, '1243')
    assert s['attempts'][0]['exact'] == 2 and s['attempts'][0]['misplaced'] == 2


def test_codebreaker_rejects_guess_out_of_turn():
    g = CodeBreaker()
    s = g.start([1, 2])
    s['secret'] = '1234'
    s = g.handle_action(s, 2, '1111')
    assert s['attempts'] == [] and s['turn'] == 0


def test_quiz_scores_correct_answer_and_counts_it():
    g = Quiz()
    s = g.handle_action(g.start([1], Q), 1, '2')
    assert s['scores']['1'] > 0 and s['correct']['1'] == 1
    assert g.get_result(s)['correct'] == {'1': 1}


def test_quiz_rejects_choice_outside_question_options():
    g = Quiz()
    s = g.handle_action(g.start([1], [{'prompt': 'p', 'choices': ['A', 'B', 'C'], 'answer': 2}]), 1, '4')
    assert s['scores']['1'] == 0 and s['round'] == 0


def test_quiz_timeout_skips_idle_player_so_match_moves_on():
    # Fix: player 2 never answers. Before, the round stalled forever.
    g = Quiz()
    s = g.start([1, 2], Q + Q)
    s = g.handle_action(s, 1, '2')
    assert s['round'] == 0            # waiting for player 2
    s = g.skip_round(s)
    assert s['round'] == 1 and s['answers'] == {}


def test_quiz_single_player_win_needs_three_correct():
    g = Quiz()
    qs = [{'prompt': 'p', 'choices': ['A', 'B'], 'answer': 0}] * 5
    s = g.start([1], qs)
    for _ in range(3):
        s = g.handle_action(s, 1, '0')
    for _ in range(2):
        s = g.skip_round(s)
    assert outcome('quiz', s, g.get_result(s)) == {1: 'win'}


def test_scramble_awards_first_correct_player():
    g = Scramble()
    s = g.start([1, 2], words=['cat'])
    s = g.handle_action(s, 2, 'CAT')
    assert s['winner'] == 2
    assert outcome('scramble', s, g.get_result(s)) == {1: 'loss', 2: 'win'}


def test_scramble_handles_words_without_a_distinct_permutation():
    s = Scramble().start([1], words=['aa'])
    assert sorted(s['scrambled']) == sorted(s['target'])


def test_memory_mismatch_keeps_cards_visible_until_next_turn():
    g = Memory()
    s = g.start([1, 2])
    s['cards'] = ['🍎', '🚀'] + s['cards'][2:]
    s = play(g, s, [(1, '0'), (1, '1')])
    assert s['revealed'] == [0, 1] and s['turn'] == 1
    s = g.handle_action(s, 2, '2')
    assert s['revealed'] == [2]


def test_memory_multi_outcome_by_score_and_draw_on_tie():
    assert outcome('memory', {'players': [1, 2]}, {'scores': {'1': 3, '2': 1}}) == {1: 'win', 2: 'loss'}
    assert outcome('memory', {'players': [1, 2]}, {'scores': {'1': 2, '2': 2}}) == {1: 'draw', 2: 'draw'}
