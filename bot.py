import asyncio
from datetime import datetime
import logging
import os
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import pdfplumber
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не задано змінну середовища BOT_TOKEN")

KYIV_TZ = ZoneInfo("Europe/Kyiv")
CURRENCIES = ["GBP", "KZT", "UAH", "USD", "EUR", "THB", "GEL"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
bot = Bot(token=TOKEN)
dp = Dispatcher()

daily_orders_state = {
    "date": datetime.now(KYIV_TZ).strftime("%Y-%m-%d"),
    "orders": [],
    "seen_keys": set(),
}


def reset_orders_for_today() -> str:
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    daily_orders_state["date"] = today_str
    daily_orders_state["orders"] = []
    daily_orders_state["seen_keys"] = set()
    return today_str


def get_today_orders() -> list[dict]:
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    if daily_orders_state["date"] != today_str:
        reset_orders_for_today()
    return daily_orders_state["orders"]


def order_dedup_key(entry: dict) -> str:
    return f"{entry['line']}|{entry['amount']}"


def add_order_if_new(entry: dict) -> bool:
    orders = get_today_orders()
    key = order_dedup_key(entry)
    if key in daily_orders_state["seen_keys"]:
        return False
    daily_orders_state["seen_keys"].add(key)
    orders.append(entry)
    return True


def is_today_message(message: types.Message) -> bool:
    msg_date = (
        message.date.astimezone(KYIV_TZ).strftime("%Y-%m-%d")
        if message.date
        else datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    )
    today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
    return msg_date == today_str


def is_cash_direction(in_text: str, out_text: str) -> bool:
    directions_text = f"{in_text} {out_text}"
    return bool(re.search(r"\b(cash|кеш|готівк)\b", directions_text, flags=re.IGNORECASE))


def extract_target_amount(in_text: str, out_text: str) -> str:
    for side_text in (out_text, in_text):
        if "USDT" in side_text.upper():
            match = re.search(
                r"([0-9]+(?:[.,][0-9]+)?)\s*[^\n]*?USDT",
                side_text,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group(1).replace(".", ",")

    for side_text in (out_text, in_text):
        fallback = re.search(r"([0-9]+(?:[.,][0-9]+)?)", side_text)
        if fallback:
            return fallback.group(1).replace(".", ",")

    return ""


def is_payment_error(text: str) -> bool:
    return bool(
        re.search(
            r"(error\s*payment|payment\s*error|ерор\s*пеймент|помилк[аи]\s*оплат[ии]|incorrect\s*amount)",
            text,
            flags=re.IGNORECASE,
        )
    )


def build_incorrect_amount_note(text: str, in_text: str, out_text: str) -> str:
    explicit_inc = re.search(r"INCORRECT\s*AMOUNT\s*:\s*([^\n]+)", text, flags=re.IGNORECASE)
    if explicit_inc:
        return f"INCORRECT AMOUNT: {explicit_inc.group(1).strip()}"

    amount = extract_target_amount(in_text, out_text)
    if not amount:
        num_match = re.search(
            r"(?:amount|сума|error\s*payment|payment\s*error|ерор\s*пеймент)[^\n0-9]*([0-9]+(?:[.,][0-9]+)?(?:\s*[A-Z]{2,5})?)",
            text,
            flags=re.IGNORECASE,
        )
        if num_match:
            amount = num_match.group(1).strip().replace(".", ",")

    if amount:
        return f"INCORRECT AMOUNT: {amount}"

    err_line = ""
    for line in text.splitlines():
        if re.search(
            r"(error\s*payment|payment\s*error|ерор\s*пеймент|помилк[аи]\s*оплат[ии]|incorrect\s*amount)",
            line,
            flags=re.IGNORECASE,
        ):
            err_line = line.strip()
            break

    return f"INCORRECT AMOUNT: {err_line}" if err_line else "INCORRECT AMOUNT: не вказано"


def parse_order_entry(text: str) -> dict | None:
    if not text:
        return None

    has_payout = "In payout processing" in text
    has_error = is_payment_error(text)

    if not has_payout and not has_error:
        return None
    if not has_error and ("#" not in text or "Exchange:" not in text):
        return None

    order_id_match = re.search(r"#(\d+)", text)
    order_id_num = int(order_id_match.group(1)) if order_id_match else None
    order_id = order_id_match.group(1) if order_id_match else "00000"

    in_match = re.search(r"IN:\s*([^\n]+)", text)
    in_text = in_match.group(1).strip() if in_match else ""

    out_match = re.search(r"OUT:\s*([^\n]+)", text)
    out_text = out_match.group(1).strip() if out_match else ""

    if
