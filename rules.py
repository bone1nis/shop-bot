"""Детерминированный слой: правила, которые код применяет сам, без LLM.

Опасные случаи (конкретный заказ, срок возврата, брак, скидки, попытки
манипуляции) всегда решает этот модуль: здесь модель не может «пожалеть»
покупателя или выдумать статус заказа. Без ключа LLM этот модуль отвечает
на все вопросы.
"""
from __future__ import annotations

import re
from typing import Optional

from kb import RETURN_DAYS

ORDER_REF = re.compile(r"(заказ\w*|трек\w*|посылк\w*|№|#)\s*(номер\s*)?(\d{3,})")
ORDER_STATUS = re.compile(
    r"(где|статус|когда\s+(приедет|привезут|доставят))\b.*(заказ|посылк)"
    r"|\b(мой|моего|мою|моей)\s+(заказ|посылк)"
)
RETURN = re.compile(r"верн(уть|ите|у|ем)|возврат|сдать|сдам")
OPENED = re.compile(r"вскры|распечат|открыт|открыл|распаков|пользовал|поносил")
DEFECT = re.compile(
    r"брак|слома|не\s+работа|дефект|неисправ|трещин|не\s+заряжа|не\s+включа|не\s+подключа"
    r"|не\s+слышно|шип|хрип|трещ|глюч|барахл|не\s+горит"
)
DISCOUNT = re.compile(
    r"скидк|промокод|промо-код|купон|дешевле|уступ|торг|акци|скин(ь|ете|уть)|сбав"
    r"|discount|promo|coupon|cheaper"
)
# «Сколько стоит доставка» — это не цена товара: стоимости доставки в базе нет.
DELIVERY_COST = re.compile(
    r"бесплатн|(стоит|стоимост\w*|цена|платн\w*)\s+(\w+\s+)?доставк|доставк\w*\s+(\w+\s+)?(стоит|платн|стоимост)"
)
PRICE = re.compile(r"цен[аыу]|стоит|стоят|стоимост|почем|наличи|купить")
PRODUCT = re.compile(r"наушник|\b[xх]\s?1\b|товар")
DELIVERY = re.compile(r"доставк|доставит|доставля|привез|курьер")
MANIPULATION = re.compile(
    r"забудь|игнорир|ты\s+теперь|представь|притворись|я\s+(директор|админ|разработчик|владелец|хозяин)"
    r"|это\s+тест|опечатк|промпт|prompt|(твои|свои|все)\s+инструкци"
    r"|ignore|forget|instructions|you\s+are\s+now"
)
GREETING = re.compile(r"^\s*(привет|здравствуй|добр\w+\s+(день|утро|вечер)|hi\b|hello\b)")
THANKS = re.compile(r"спасиб|благодар|пасиб|\bспс\b|понял|^\s*(ок|окей|понятно|ясно)\W*$")

# Темы, о которых в базе ничего нет: называем их честно и предлагаем менеджера.
NOT_IN_KB = [
    (DELIVERY_COST, "стоимость доставки"),
    (re.compile(r"гаранти"), "гарантию"),
    (re.compile(r"инструкци\w*\s+(к|по|для)"), "инструкцию к наушникам"),
    (re.compile(r"телефон|контакт|почт[ауы]|email"), "контакты магазина"),
    (re.compile(r"цвет"), "цвета"),
    (re.compile(r"чехол|чехл"), "чехол"),
    (re.compile(r"модел"), "другие модели"),
    (re.compile(r"самовывоз|пункт\w*\s+выдачи|адрес"), "самовывоз и адрес"),
    (re.compile(r"оплат|наличн|рассрочк"), "способы оплаты"),
    (re.compile(r"выходн|суббот|воскресен"), "доставку в выходные"),
    (re.compile(r"\bутром\b|\bднем\b|другое\s+время"), "другие слоты доставки"),
]

DAY_PATTERNS = [
    (re.compile(r"(\d{1,3})\s*-?\s*(?:й|ый|ий|ой)?\s*(?:день|дн[яеийь]|сут)"), 1),
    (re.compile(r"(\d{1,2})\s*недел"), 7),
]
# Срок словами: число в ответ не подставляем, чтобы не приписывать покупателю «30-й день».
DAY_WORDS = [
    (re.compile(r"три\s+недел"), 21, "через три недели"),
    (re.compile(r"две\s+недел"), 14, "через две недели"),
    (re.compile(r"месяц"), 30, "через месяц"),
    (re.compile(r"недел"), 7, "через неделю"),
]

HELP_TOPICS = "наушники X1, доставка и возврат"


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def extract_day(text: str) -> tuple[Optional[int], Optional[str]]:
    """День с момента покупки, если покупатель его назвал, и как сослаться на него в ответе."""
    for pattern, mult in DAY_PATTERNS:
        m = pattern.search(text)
        if m:
            day = int(m.group(1)) * mult
            return day, f"на {day}-й день"
    for pattern, days, label in DAY_WORDS:
        if pattern.search(text):
            return days, label
    return None, None


def detect(text: str) -> dict:
    t = normalize(text)
    order = ORDER_REF.search(t)
    day, day_label = extract_day(t)
    return {
        "order": bool(order or ORDER_STATUS.search(t)),
        "order_number": order.group(3) if order else None,
        "defect": bool(DEFECT.search(t)),
        "return": bool(RETURN.search(t)),
        "opened": bool(OPENED.search(t)),
        "day": day,
        "day_label": day_label,
        "discount": bool(DISCOUNT.search(t)),
        "price": bool(PRICE.search(t)) and not DELIVERY_COST.search(t),
        "product": bool(PRODUCT.search(t)),
        "delivery": bool(DELIVERY.search(t)),
        "manipulation": bool(MANIPULATION.search(t)),
        "greeting": bool(GREETING.search(t)),
        "thanks": bool(THANKS.search(t)),
        "unknown_topics": [name for pattern, name in NOT_IN_KB if pattern.search(t)],
    }


def is_hard_case(intents: dict) -> bool:
    """Случаи, которые никогда не отдаются LLM."""
    return any(intents[k] for k in ("order", "defect", "discount", "manipulation")) or (
        intents["return"] and intents["day"] is not None
    )


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def answer(text: str, intents: Optional[dict] = None) -> dict:
    i = intents or detect(text)
    parts: list[str] = []
    facts: list[str] = []
    found = False
    handoff = False
    # Следующий шаг добавляем, только когда он нужен: на полный ответ хвост не вешаем.
    tail = ""

    def use(*ids: str) -> None:
        for fid in ids:
            if fid not in facts:
                facts.append(fid)

    if i["order"]:
        # Статуса заказов в базе нет: даём только общее правило F2.
        which = f"Заказ {i['order_number']}" if i["order_number"] else "Ваш заказ"
        parts.append(
            f"{which} мне не виден — к базе заказов у меня нет доступа, поэтому про статус гадать не буду. "
            "Общее правило такое: курьер приезжает на следующий день, между 18:00 и 21:00."
        )
        use("F2")
        handoff = True
        tail = "Передать номер менеджеру? Он посмотрит, что с заказом."
    elif i["delivery"]:
        parts.append("Курьер приезжает на следующий день, между 18:00 и 21:00.")
        use("F2")
        found = True

    if i["defect"]:
        # Брак всегда идёт по F5, даже в первые 14 дней.
        parts.append("Наушники с браком мы забираем на экспертизу, поэтому вернуть деньги сразу не получится.")
        use("F5")
        found = True
        handoff = True
        tail = "Передать обращение менеджеру, чтобы он его оформил?"
    elif i["return"] or i["opened"]:
        day = i["day"]
        opened = i["opened"]
        refund = "Деньги придут на ту же карту за 3 рабочих дня."
        if day is not None and day > RETURN_DAYS:
            parts.append(
                f"{_cap(i['day_label'])} вернуть уже не получится: срок возврата — {RETURN_DAYS} дней"
                f"{', для вскрытого товара он такой же' if opened else ''}."
            )
        elif day is not None:
            parts.append(
                f"{_cap(i['day_label'])} — успеваете: срок возврата {RETURN_DAYS} дней"
                f"{', и вскрытые наушники без брака тоже можно сдать' if opened else ''}. {refund}"
            )
        elif opened:
            parts.append(f"Да, даже вскрытые наушники без брака можно вернуть в течение {RETURN_DAYS} дней. {refund}")
        else:
            parts.append(f"Вернуть товар можно в течение {RETURN_DAYS} дней. {refund}")
        use("F3", *(["F4"] if opened else []))
        found = True

    if i["discount"]:
        parts.append("Скидок у нас нет ни при каких условиях — наушники X1 стоят 2 490 ₽.")
        use("F6", "F1")
        found = True
    elif i["price"] or (i["product"] and not parts and not i["unknown_topics"]):
        parts.append("Наушники X1 есть в наличии, цена — 2 490 ₽.")
        use("F1")
        found = True

    if i["unknown_topics"]:
        parts.append(f"Про {_join(i['unknown_topics'])} в правилах магазина ничего нет, а выдумывать не буду.")
        handoff = True
        tail = "Передать вопрос менеджеру?"

    if not parts:
        if i["manipulation"]:
            parts.append(f"Я отвечаю по правилам магазина: {HELP_TOPICS}. Что вас интересует?")
        elif i["thanks"]:
            parts.append("Обращайтесь. Если появятся вопросы про наушники, доставку или возврат — пишите.")
        elif i["greeting"]:
            parts.append(f"Здравствуйте. Подскажу про {_join(['наушники X1', 'доставку', 'возврат'])} — что вас интересует?")
        else:
            parts.append("Тут не подскажу — это вне моих правил.")
            handoff = True
            tail = f"Могу ответить про наушники X1, доставку и возврат или передать вопрос менеджеру."

    return {
        "answer": " ".join(parts + ([tail] if tail else [])),
        "facts": facts,
        "found": found,
        "handoff": handoff,
    }
