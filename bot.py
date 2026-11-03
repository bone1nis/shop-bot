from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.filters import Command, CommandStart  # noqa: E402
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message  # noqa: E402

import assistant  # noqa: E402

BOT_TOKEN = os.environ["BOT_TOKEN"]
MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID")
SHOW_FACTS = os.getenv("SHOW_FACTS") == "1"
LOG_DIR = Path(__file__).parent / "logs"

dp = Dispatcher()
# id сообщения бота → исходный вопрос, чтобы кнопка «Передать менеджеру» знала, что передавать
pending_handoffs: dict[int, str] = {}

# По этой строке бот находит покупателя, когда менеджер отвечает реплаем — без БД и без состояния.
CUSTOMER_ID = re.compile(r"\(id (\d+)\)")

HANDOFF_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Передать менеджеру", callback_data="handoff")]]
)


def log(name: str, record: dict) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_DIR / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Здравствуйте. Я помощник магазина X1 — отвечаю про наушники, доставку и возврат. "
        "Если ответа нет в правилах магазина, так и скажу и позову менеджера."
    )


def is_manager_chat(message: Message) -> bool:
    return bool(MANAGER_CHAT_ID) and str(message.chat.id) == MANAGER_CHAT_ID


def read_log(name: str) -> list[dict]:
    path = LOG_DIR / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@dp.message(Command("stats"))
async def stats(message: Message) -> None:
    """Сводка для владельца: сколько вопросов бот закрыл сам и что спрашивают чаще всего без ответа."""
    if MANAGER_CHAT_ID and not is_manager_chat(message):
        return
    dialogs = read_log("dialogs.jsonl")
    if not dialogs:
        await message.answer("Пока нет ни одного диалога.")
        return
    total = len(dialogs)
    auto = sum(1 for d in dialogs if d["found"] and not d["handoff"])
    handed = sum(1 for h in read_log("handoffs.jsonl") if "question" in h)
    facts = Counter(f for d in dialogs for f in d["facts"])
    gaps = Counter(d["question"].strip().lower() for d in dialogs if not d["found"])
    lines = [
        f"Вопросов: {total}",
        f"Закрыто ботом без менеджера: {auto} ({auto * 100 // total}%)",
        f"Передано менеджеру по кнопке: {handed}",
        "Частые темы: " + (", ".join(f"{f} ×{n}" for f, n in facts.most_common()) or "—"),
        "",
        "Чего нет в базе (кандидаты в регламент):",
        *(f"• {q} ×{n}" for q, n in gaps.most_common(5)),
    ]
    await message.answer("\n".join(lines))


@dp.message(F.reply_to_message, F.text, is_manager_chat)
async def on_manager_reply(message: Message) -> None:
    """Менеджер отвечает реплаем на переданный вопрос — бот пересылает ответ покупателю."""
    m = CUSTOMER_ID.search(message.reply_to_message.text or "")
    if not m:
        await message.answer("Ответьте реплаем на сообщение с вопросом покупателя.")
        return
    await message.bot.send_message(int(m.group(1)), f"Ответ менеджера:\n{message.text}")
    log("handoffs.jsonl", {"user_id": int(m.group(1)), "manager_answer": message.text})
    await message.answer("Отправлено покупателю.")


@dp.message(F.text)
async def on_text(message: Message) -> None:
    result = await assistant.answer(message.text)
    log("dialogs.jsonl", {"user_id": message.from_user.id, "question": message.text, **result})

    text = result["answer"]
    if SHOW_FACTS:
        text += f"\n\n— факты: {', '.join(result['facts']) or 'нет'} · источник: {result['source']}"
    sent = await message.answer(text, reply_markup=HANDOFF_KB if result["handoff"] else None)
    if result["handoff"]:
        pending_handoffs[sent.message_id] = message.text


@dp.message()
async def on_other(message: Message) -> None:
    await message.answer("Я понимаю только текст. Напишите вопрос о наушниках X1, доставке или возврате.")


@dp.callback_query(F.data == "handoff")
async def on_handoff(callback: CallbackQuery) -> None:
    question = pending_handoffs.pop(callback.message.message_id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    if question is None:
        await callback.answer("Этот вопрос уже передан.")
        return

    user = callback.from_user
    who = f"@{user.username}" if user.username else user.full_name
    if MANAGER_CHAT_ID:
        await callback.bot.send_message(
            MANAGER_CHAT_ID,
            f"Вопрос от {who} (id {user.id}):\n{question}\n\nОтветьте реплаем — ответ уйдёт покупателю.",
        )
    log("handoffs.jsonl", {"user_id": user.id, "user": who, "question": question})
    await callback.message.answer("Передал ваш вопрос менеджеру.")
    await callback.answer()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mode = f"LLM {assistant.LLM_MODEL} + правила" if assistant.LLM_API_KEY else "только правила (LLM_API_KEY не задан)"
    logging.info("Режим: %s", mode)
    await dp.start_polling(Bot(BOT_TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
