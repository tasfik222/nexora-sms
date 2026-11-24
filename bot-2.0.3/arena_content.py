"""Fresh content for the arena games (quiz questions and scramble words).

The built-in lists are small, so players see the same questions and words often.
When an AI provider key is set (the same one used by AI Support), this module
asks the AI for new items in the background and adds the valid ones to the pools.
Without a key, or when the AI fails, the games keep using the built-in lists.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
from collections import deque
from typing import Optional

import httpx

from arena_games import SCRAMBLE_WORDS
from arena_quiz_data import QUIZ_QUESTIONS as STATIC_QUIZ

log = logging.getLogger(__name__)

REFILL_SECONDS = 1800          # how often the pools are topped up
BATCH = 10                     # items asked for per language per refill
POOL_MAX = 300                 # cap per pool so memory stays small
LOW_WATER = 60                 # refill only when a pool is below this size

WORD_RE = re.compile(r"[a-z]{5,8}")

# Pools shared by the game code. Start with the built-in lists.
QUIZ: dict[str, list[dict]] = {lang: copy.deepcopy(items) for lang, items in STATIC_QUIZ.items()}
WORDS: list[str] = list(SCRAMBLE_WORDS)
_recent_quiz: deque = deque(maxlen=40)     # prompts served lately, to avoid back-to-back repeats


def quiz_pool(lang: str) -> list[dict]:
    return QUIZ.get(lang) or QUIZ.get("en") or []


def quiz_pick(lang: str, n: int) -> list[dict]:
    """n questions for one match, avoiding recently served prompts when possible."""
    pool = quiz_pool(lang)
    fresh = [q for q in pool if q["prompt"] not in _recent_quiz]
    stale = [q for q in pool if q["prompt"] in _recent_quiz]
    chosen = random.sample(fresh, min(n, len(fresh)))
    if len(chosen) < n:                       # not enough new questions: fill with the others
        chosen += random.sample(stale, min(n - len(chosen), len(stale)))
    for q in chosen:
        _recent_quiz.append(q["prompt"])
    return copy.deepcopy(chosen)


def word() -> str:
    return random.choice(WORDS)


def _valid_question(q) -> Optional[dict]:
    if not isinstance(q, dict):
        return None
    prompt = str(q.get("prompt") or "").strip()
    choices = q.get("choices")
    answer = q.get("answer")
    if not (5 <= len(prompt) <= 200) or not isinstance(choices, list) or len(choices) != 4:
        return None
    choices = [str(c).strip() for c in choices]
    if any(not c or len(c) > 60 for c in choices) or len(set(c.lower() for c in choices)) != 4:
        return None
    if not isinstance(answer, int) or not 0 <= answer < 4:
        return None
    return {"prompt": prompt, "choices": choices, "answer": answer}


def parse_reply(text: str) -> dict:
    """Pull the JSON object out of the AI reply. Returns {} when it is not valid."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def merge(lang: str, questions: list, words: list) -> tuple[int, int]:
    """Add valid, new items to the pools. Returns how many were added (questions, words)."""
    pool = QUIZ.setdefault(lang, [])
    known = {q["prompt"].lower() for q in pool}
    added_q = 0
    for raw in questions or []:
        q = _valid_question(raw)
        if q and q["prompt"].lower() not in known and len(pool) < POOL_MAX:
            pool.append(q)
            known.add(q["prompt"].lower())
            added_q += 1
    added_w = 0
    known_w = set(WORDS)
    for w in words or []:
        w = str(w).strip().lower() if isinstance(w, str) else ""
        if WORD_RE.fullmatch(w) and len(set(w)) >= 4 and w not in known_w and len(WORDS) < POOL_MAX:
            WORDS.append(w)
            known_w.add(w)
            added_w += 1
    return added_q, added_w


def _prompt(lang: str) -> str:
    language = "Bangla (Bengali script)" if lang == "bn" else "English"
    return (
        f"Make {BATCH} new multiple-choice general-knowledge quiz questions in {language}. "
        "Each has exactly 4 different choices and one correct answer (answer = index 0 to 3). "
        f"Also make {BATCH} new English words of 5 to 8 lowercase letters only for a word game. "
        "Reply with ONLY one JSON object, no other text, in this form: "
        '{"questions":[{"prompt":"...","choices":["a","b","c","d"],"answer":0}],"words":["word"]}'
    )


class ArenaContent:
    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client
        self._task: Optional[asyncio.Task] = None

    def _pick_provider(self):
        from ai_support import PROVIDERS, _provider
        name = _provider()
        return name, (PROVIDERS[name] if name else None)

    def enabled(self) -> bool:
        name, _ = self._pick_provider()
        return name is not None

    async def _ask(self, prompt: str) -> str:
        from ai_support import API_VERSION, _model, _provider, PROVIDERS
        name = _provider()
        if not name:
            return ""
        key_env, style, base, _default, _max = PROVIDERS[name]
        import os
        key = (os.getenv(key_env) or "").strip()
        model = _model(name)
        if style == "anthropic":
            url = f"{base}/messages"
            headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
            payload = {"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
        else:
            url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
            payload = {"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
        r = await self._client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            log.warning("Arena content: %s HTTP %s", name, r.status_code)
            return ""
        data = r.json()
        if style == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    async def refill_once(self) -> None:
        if not self.enabled():
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        need_quiz = {lang: len(QUIZ.get(lang, [])) < LOW_WATER for lang in ("en", "bn")}
        need_words = len(WORDS) < LOW_WATER
        if not (any(need_quiz.values()) or need_words):
            return
        for lang in ("en", "bn"):
            if not need_quiz[lang] and not (lang == "en" and need_words):
                continue
            try:
                reply = await self._ask(_prompt(lang))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Arena content request failed: %s", e)
                continue
            data = parse_reply(reply)
            aq, aw = merge(lang, data.get("questions"), data.get("words") if lang == "en" else [])
            log.info("Arena content (%s): +%d questions, +%d words", lang, aq, aw)

    async def _loop(self) -> None:
        while True:
            try:
                await self.refill_once()
            except Exception:
                log.exception("Arena content refill failed")
            await asyncio.sleep(REFILL_SECONDS)

    async def start(self) -> None:
        if self._task is None and self.enabled():
            self._task = asyncio.create_task(self._loop())
            log.info("Arena content: AI refill on (every %d min)", REFILL_SECONDS // 60)
        elif not self.enabled():
            log.info("Arena content: no AI key - using built-in quiz questions and words")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
