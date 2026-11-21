import arena_content as ac


def test_repeated_letter_words_are_not_added(monkeypatch):
    monkeypatch.setattr(ac, "WORDS", [])
    ac.merge("en", [], ["aaaaaa", "bbbbbbb", "moon", "garden"])
    assert "aaaaaa" not in ac.WORDS and "bbbbbbb" not in ac.WORDS
    assert "garden" in ac.WORDS
