"""Конвейер ответа: правила → (опционально) LLM → проверка ответа LLM по базе.

1. Опасные случаи (заказ, срок возврата, брак, скидки, манипуляции) решает rules.py.
2. Остальное отправляется в LLM, если задан LLM_API_KEY (любой OpenAI-совместимый API).
3. Ответ LLM проверяется: JSON, ссылки только на F1–F6, каждое число — из
   процитированных фактов или из вопроса, без «обычно/скорее всего».
   Не прошёл проверку — отвечают правила. Ключа нет — тоже правила.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import aiohttp

import rules
from kb import FACTS, facts_block

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8").format(
    facts=facts_block()
)

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

HEDGES = ("обычно", "как правило", "скорее всего", "наверное", "попробу", "постара", "в порядке исключения")
NUMBER = re.compile(r"\d[\d\s ]*\d|\d")
PRODUCT_NAME = re.compile(r"\b[XХ]1\b", re.IGNORECASE)  # «1» в названии товара — не число


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", n) for n in NUMBER.findall(PRODUCT_NAME.sub("", text))}


def validate(result: dict, question: str) -> Optional[str]:
    """Возвращает причину отказа или None, если ответ можно отправлять."""
    if not isinstance(result.get("answer"), str) or not result["answer"].strip():
        return "пустой answer"
    if not isinstance(result.get("facts"), list) or any(f not in FACTS for f in result["facts"]):
        return f"неизвестные факты: {result.get('facts')}"
    for key in ("found", "handoff"):
        if not isinstance(result.get(key), bool):
            return f"{key} не bool"
    answer = result["answer"]
    allowed = _numbers(question)
    for fid in result["facts"]:
        allowed |= _numbers(FACTS[fid])
    extra = _numbers(answer) - allowed
    if extra:
        return f"числа не из базы: {sorted(extra)}"
    low = answer.lower()
    hedge = next((h for h in HEDGES if h in low), None)
    if hedge:
        return f"неуверенная формулировка: «{hedge}»"
    return None


async def _ask_llm(question: str) -> dict:
    payload = {
        "model": LLM_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    return json.loads(content)


async def answer(question: str) -> dict:
    intents = rules.detect(question)
    if rules.is_hard_case(intents) or not LLM_API_KEY:
        result = rules.answer(question, intents)
        result["source"] = "rules"
        return result

    try:
        result = await _ask_llm(question)
        reason = validate(result, question)
    except Exception as e:  # сеть, лимиты, битый JSON
        reason = f"ошибка LLM: {e!r}"
    if reason is None:
        result["source"] = "llm"
        return result

    result = rules.answer(question, intents)
    result["source"] = f"rules (ответ LLM отклонён: {reason})"
    return result
