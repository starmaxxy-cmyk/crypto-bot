import asyncio
from datetime import datetime
import logging
import os
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo
import pdfplumber
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = os.environ.get("BOT_TOKEN")
KYIV_TZ = ZoneInfo("Europe/Kyiv")

CURRENCIES_AND_TAGS = ["GBP", "KZT", "UAH", "USD", "EUR", "THB", "GEL", "Cash"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Сховище заявок за поточний день:
# {"date": "2026-11-19", "orders": [{"line": "...", "amount": "...", "in_text": "...", "out_text": "...", "is_cash": bool}]}
daily_orders_state = {
    "date": datetime.now(KYIV_TZ).strftime("%Y-%m-%d"),
    "orders": [],
}


def reset_orders_for_today() -> str:
    """Оновлює дату на поточний день за Києвом і очищує список заявок."""
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    daily_orders_state["date"] = today_str
    daily_orders_state["orders"] = []
    return today_str


def get_today_orders() -> list[dict]:
    """Повертає список заявок за сьогодні. Якщо настав новий день — очищує вчорашні."""
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    if daily_orders_state["date"] != today_str:
        reset_orders_for_today()
    return daily_orders_state["orders"]


def is_today_message(message: types.Message) -> bool:
    """Перевіряє, чи повідомлення прийшло за поточний день за київським часом."""
    msg_date = (
        message.date.astimezone(KYIV_TZ).strftime("%Y-%m-%d")
        if message.date
        else datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    )
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    return msg_date == today_str


def is_cash_direction(in_text: str, out_text: str) -> bool:
    """Перевіряє, чи є кеш саме в напрямках обміну IN / OUT."""
    directions_text = f"{in_text} {out_text}"
    return bool(re.search(r'\b(cash|кеш|готівк)\b', directions_text, flags=re.IGNORECASE))


def extract_target_amount(in_text: str, out_text: str) -> str:
    """
    Витягує суму в USDT з IN або OUT.
    Якщо USDT у заявці немає — бере числове значення з OUT або IN.
    Повертає суму з комою для зручної вставки в таблицю.
    """
    for side_text in (out_text, in_text):
        if "USDT" in side_text.upper():
            match = re.search(
                r'([0-9]+(?:[.,][0-9]+)?)\s*[^\n]*?USDT',
                side_text,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group(1).replace('.', ',')

    for side_text in (out_text, in_text):
        fallback = re.search(r'([0-9]+(?:[.,][0-9]+)?)', side_text)
        if fallback:
            return fallback.group(1).replace('.', ',')

    return ""


def parse_order_entry(text: str) -> dict | None:
    """
    Повертає словник з очищеним рядком операції, сумою та ознакою кешу.
    Бере заявки зі статусом 'In payout processing' по всіх напрямках/валютах.
    """
    if not text:
        return None
    if "In payout processing" not in text:
        return None
    if "#" not in text or "Exchange:" not in text:
        return None

    order_id_match = re.search(r'#(\d+)', text)
    order_id = order_id_match.group(1) if order_id_match else "00000"

    in_match = re.search(r'IN:\s*([^\n]+)', text)
    in_text = in_match.group(1).strip() if in_match else ""

    out_match = re.search(r'OUT:\s*([^\n]+)', text)
    out_text = out_match.group(1).split('\n')[0].strip() if out_match else ""

    if not in_text and not out_text:
        return None

    cash_flag = is_cash_direction(in_text, out_text)
    line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
    amount = extract_target_amount(in_text, out_text)

    return {
        "line": line,
        "amount": amount,
        "in_text": in_text,
        "out_text": out_text,
        "is_cash": cash_flag,
    }


def matches_filter(order: dict, tag: str | None) -> bool:
    """
    Фільтрує заявки за валютою, звичайним Cash або конкретним Cash EUR / Cash UAH / Cash USD:
    - 'ALL' -> усі заявки за день
    - 'Cash' -> усі заявки з кешем у напрямках
    - 'CASH_EUR' / 'CASH_UAH' / 'CASH_USD' -> заявки з кешем + відповідною валютою
    - Валюта (USD, EUR, UAH тощо) -> лише заявки з цією валютою БЕЗ кешу в напрямках
    """
    if not tag or tag == "ALL":
        return True

    is_cash = bool(order.get("is_cash", False))
    directions_text = f"{order.get('in_text', '')} {order.get('out_text', '')}"

    if tag == "Cash":
        return is_cash

    if tag.startswith("CASH_"):
        currency = tag.split("_", 1)[1]
        return is_cash and bool(re.search(rf'\b{re.escape(currency)}\b', directions_text, flags=re.IGNORECASE))

    if is_cash:
        return False

    return bool(re.search(rf'\b{re.escape(tag)}\b', directions_text, flags=re.IGNORECASE))


def format_filter_label(tag: str | None) -> str:
    if not tag or tag == "ALL":
        return "Усі напрямки"
    if tag.startswith("CASH_"):
        currency = tag.split("_", 1)[1]
        return f"Cash {currency}"
    return tag


def build_main_keyboard() -> InlineKeyboardMarkup:
    """Формує клавіатуру з кнопками валют, Cash та звіту."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(CURRENCIES_AND_TAGS), 4):
        chunk = CURRENCIES_AND_TAGS[i:i + 4]
        row = []
        for item in chunk:
            cb_data = "open_cash_menu" if item == "Cash" else f"filter:{item}"
            row.append(InlineKeyboardButton(text=item, callback_data=cb_data))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(text="🌐 Усі напрямки", callback_data="filter:ALL"),
        InlineKeyboardButton(text="📊 Забрати звіт за день", callback_data="export_today"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cash_keyboard() -> InlineKeyboardMarkup:
    """Підменю вибору валюти для Cash: EUR, UAH, USD."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💶 Cash EUR", callback_data="filter:CASH_EUR"),
                InlineKeyboardButton(text="🇺🇦 Cash UAH", callback_data="filter:CASH_UAH"),
                InlineKeyboardButton(text="💵 Cash USD", callback_data="filter:CASH_USD"),
            ],
            [
                InlineKeyboardButton(text="💰 Увесь Cash", callback_data="filter:Cash"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"),
            ],
        ]
    )


def extract_receipt_code(text: str) -> str:
    """Акуратно витягує номер квитанції."""
    hyphen_match = re.search(r'\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){2,4}\b', text)
    if hyphen_match:
        return hyphen_match.group(0).strip()

    code_match = re.search(r'\b(?:P24[A-Z0-9]{12,35}|[A-Z0-9]{16,40})\b', text)
    if code_match:
        return code_match.group(0).strip()

    return "Не знайдено"


def build_check_link(receipt_code: str, full_text: str) -> tuple[str, str]:
    """Формує коректне посилання та підпис кнопки для перевірки квитанції."""
    clean_code = receipt_code.strip() if receipt_code and receipt_code != "Не знайдено" else ""
    upper_text = full_text.upper()

    if clean_code.startswith("P24") or "ПРИВАТ" in upper_text or "PRIVAT" in upper_text:
        if clean_code:
            url = f"https://www.privatbank.ua/sendmoney/check?reference={quote(clean_code)}"
        else:
            url = "https://www.privatbank.ua/sendmoney/check"
        return url, "ПриватБанку"

    if clean_code:
        url = f"https://check.gov.ua/{quote(clean_code)}"
    else:
        url = "https://check.gov.ua/"
    return url, "check.gov.ua"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    orders = get_today_orders()
    count = len(orders
