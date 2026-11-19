"""AI Support helpers. Run from the project folder:  python -m pytest -q tests"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ai_support as ai  # noqa: E402


def test_history_for_api_always_starts_with_a_user_turn():
    hist = []
    for i in range(20):
        hist.append({"role": "user", "content": f"q{i}"})
        hist.append({"role": "assistant", "content": f"a{i}"})
    window = ai._history_for_api(hist)
    assert window[0]["role"] == "user"
    assert len(window) <= ai.HISTORY_MAX


def test_history_for_api_keeps_short_histories_unchanged():
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert ai._history_for_api(hist) == hist


def test_history_for_api_handles_cut_that_lands_on_answer():
    hist = [{"role": "user", "content": str(i)} if i % 2 == 0 else {"role": "assistant", "content": str(i)}
            for i in range(1, 15)]
    # the window of the last 12 starts on an assistant turn; it must be dropped
    window = ai._history_for_api(hist)
    assert window and window[0]["role"] == "user"


def test_gemini_default_model_is_set():
    assert ai.PROVIDERS["gemini"][3] == "gemini-2.5-flash"


def test_provider_follows_key_and_forced_choice(monkeypatch):
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert ai._provider() is None
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert ai._provider() == "groq"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert ai._provider() == "anthropic"
