import asyncio
import json

import arena_content as ac


def q(prompt, answer=1):
    return {"prompt": prompt, "choices": ["A", "B", "C", "D"], "answer": answer}


def test_parse_reply_extracts_json_from_text():
    reply = 'Here you go:\n{"questions": [], "words": ["river"]}\nthanks'
    assert ac.parse_reply(reply) == {"questions": [], "words": ["river"]}
    assert ac.parse_reply("no json here") == {}
    assert ac.parse_reply("{broken") == {}


def test_merge_keeps_only_valid_new_items(monkeypatch):
    monkeypatch.setattr(ac, "QUIZ", {"en": []})
    monkeypatch.setattr(ac, "WORDS", ["planet"])
    questions = [
        q("What is the capital of France?"),
        q("What is the capital of France?"),                     # duplicate
        {"prompt": "Bad answer index here?", "choices": ["A", "B", "C", "D"], "answer": 9},
        {"prompt": "Only three choices?", "choices": ["A", "B", "C"], "answer": 0},
        {"prompt": "Same choices twice?", "choices": ["A", "A", "C", "D"], "answer": 0},
    ]
    words = ["Garden", "planet", "tree", "python", "ab", "toolongwordhere", "sun-day"]
    aq, aw = ac.merge("en", questions, words)
    assert aq == 1
    assert ac.QUIZ["en"][0]["prompt"] == "What is the capital of France?"
    assert aw == 2                       # 'garden' and 'python' only ('planet' already known)
    assert "garden" in ac.WORDS and "python" in ac.WORDS


def test_quiz_pick_avoids_recent_prompts(monkeypatch):
    pool = [q(f"Question number {i}?") for i in range(12)]
    monkeypatch.setattr(ac, "QUIZ", {"en": pool})
    monkeypatch.setattr(ac, "_recent_quiz", ac.deque(maxlen=40))
    first = ac.quiz_pick("en", 5)
    second = ac.quiz_pick("en", 5)
    assert len(first) == 5 and len(second) == 5
    assert not {x["prompt"] for x in first} & {x["prompt"] for x in second}


def test_quiz_pick_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(ac, "QUIZ", {"en": [q("English question here?")]})
    assert ac.quiz_pick("bn", 1)[0]["prompt"] == "English question here?"


def test_refill_adds_ai_items_when_key_is_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ac, "QUIZ", {"en": [], "bn": []})
    monkeypatch.setattr(ac, "WORDS", [])

    async def fake_ask(self, prompt):
        return json.dumps({"questions": [q("Which ocean is the largest?", 2)], "words": ["ocean", "garden"]})

    monkeypatch.setattr(ac.ArenaContent, "_ask", fake_ask)
    asyncio.run(ac.ArenaContent().refill_once())
    assert ac.QUIZ["en"] and ac.QUIZ["bn"]
    assert "ocean" in ac.WORDS


def test_no_key_means_no_ai_calls(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = []

    async def fake_ask(self, prompt):
        called.append(prompt)
        return ""

    monkeypatch.setattr(ac.ArenaContent, "_ask", fake_ask)
    asyncio.run(ac.ArenaContent().refill_once())
    assert called == []
