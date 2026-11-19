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


def parse_order_entry(text: str) -> dict | None:
    if not text:
        return None
    if "In payout processing" not in text:
        return None
    if "#" not in text or "Exchange:" not in text:
        return None

    order_id_match = re.search(r"#(\d+)", text)
    order_id_num = int(order_id_match.group(1)) if order_id_match else None
    order_id = order_id_match.group(1) if order_id_match else "00000"

    in_match = re.search(r"IN:\s*([^\n]+)", text)
    in_text = in_match.group(1).strip() if in_match else ""

    out_match = re.search(r"OUT:\s*([^\n]+)", text)
    out_text = out_match.group(1).strip() if out_match else ""

    if not in_text and not out_text:
        return None

    cash_flag = is_cash_direction(in_text, out_text)
    line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
    amount = extract_target_amount(in_text, out_text)

    return {
        "order_id": order_id_num,
        "line": line,
        "amount": amount,
        "in_text": in_text,
        "out_text": out_text,
        "is_cash": cash_flag,
    }


def sort_orders_ascending(orders_list: list[dict]) -> list[dict]:
    """Сортує заявки від найменшого номера до найбільшого (від раніших до новіших)."""
    return sorted(
        orders_list,
        key=lambda item: (
            0 if item.get("order_id") is not None else 1,
            item.get("order_id") if item.get("order_id") is not None else 0,
        ),
    )


def matches_filter(order: dict, tag: str | None) -> bool:
    if not tag or tag == "ALL":
        return True

    is_cash = bool(order.get("is_cash", False))
    directions_text = f"{order.get('in_text', '')} {order.get('out_text', '')}"

    if tag == "Cash":
        return is_cash

    if tag.startswith("CASH_"):
        currency = tag.split("_", 1)[1]
        return is_cash and bool(
            re.search(rf"\b{re.escape(currency)}\b", directions_text, flags=re.IGNORECASE)
        )

    if is_cash:
        return False

    return bool(re.search(rf"\b{re.escape(tag)}\b", directions_text, flags=re.IGNORECASE))


def format_filter_label(tag: str | None) -> str:
    if not tag or tag == "ALL":
        return "Усі напрямки"
    if tag.startswith("CASH_"):
        currency = tag.split("_", 1)[1]
        return f"Cash {currency}"
    return tag


def clear_orders_by_tag(tag: str | None) -> int:
    """Видаляє заявки поточного дня, які підходять під вказаний фільтр."""
    orders = get_today_orders()
    if not tag or tag == "ALL":
        removed_count = len(orders)
        reset_orders_for_today()
        return removed_count

    remaining = []
    removed_count = 0
    for item in orders:
        if matches_filter(item, tag):
            removed_count += 1
            daily_orders_state["seen_keys"].discard(order_dedup_key(item))
        else:
            remaining.append(item)

    daily_orders_state["orders"] = remaining
    return removed_count


def build_main_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons_with_cash = CURRENCIES + ["Cash"]
    for i in range(0, len(buttons_with_cash), 4):
        chunk = buttons_with_cash[i : i + 4]
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


def build_report_keyboard(tag: str | None) -> InlineKeyboardMarkup:
    clean_tag = tag or "ALL"
    label = format_filter_label(clean_tag)
    rows = build_main_keyboard().inline_keyboard
    rows.append([
        InlineKeyboardButton(
            text=f"🗑 Очистити {label}",
            callback_data=f"clear_filter:{clean_tag}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cash_keyboard() -> InlineKeyboardMarkup:
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
    hyphen_match = re.search(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){2,4}\b", text)
    if hyphen_match:
        return hyphen_match.group(0).strip()

    code_match = re.search(r"\b(?:P24[A-Z0-9]{12,35}|[A-Z0-9]{16,40})\b", text)
    if code_match:
        return code_match.group(0).strip()

    return "Не знайдено"


def build_check_link(receipt_code: str, full_text: str) -> tuple[str, str]:
    clean_code = (
        receipt_code.strip() if receipt_code and receipt_code != "Не знайдено" else ""
    )
    upper_text = full_text.upper()

    if clean_code.startswith("P24") or "ПРИВАТ" in upper_text or "PRIVAT" in upper_text:
        url = (
            f"https://www.privatbank.ua/sendmoney/check?reference={quote(clean_code)}"
            if clean_code
            else "https://www.privatbank.ua/sendmoney/check"
        )
        return url, "ПриватБанку"

    url = f"https://check.gov.ua/{quote(clean_code)}" if clean_code else "https://check.gov.ua/"
    return url, "check.gov.ua"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    orders = get_today_orders()
    text = (
        f"Оброблено заявок за сьогодні: {len(orders)}. "
        f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
        f"Оберіть валюту або напрямок кнопкою нижче:"
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    orders = get_today_orders()
    await message.answer(
        f"Бот активний для команди. Дата (Київ): {daily_orders_state['date']}. "
        f"Заявок у пам'яті за сьогодні: {len(orders)}.",
        reply_markup=build_main_keyboard(),
    )


@dp.callback_query(F.data == "open_cash_menu")
async def open_cash_menu_callback(callback: types.CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Оберіть валюту для напрямку Cash:",
            reply_markup=build_cash_keyboard(),
        )


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: types.CallbackQuery):
    await callback.answer()
    orders = get_today_orders()
    if callback.message:
        await callback.message.edit_text(
            f"Оброблено заявок за сьогодні: {len(orders)}. "
            f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
            f"Оберіть валюту або напрямок кнопкою нижче:",
            reply_markup=build_main_keyboard(),
        )


def extract_full_text(image_path: str) -> str:
    try:
        img = Image.open(image_path)
        img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
        img = img.convert("L")
        img = ImageOps.autocontrast(img, cutoff=2)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.5)
        return pytesseract.image_to_string(img, lang="eng+ukr")
    except Exception:
        return ""


@dp.message(F.chat.type == "private", F.photo | F.document)
async def handle_receipt(message: types.Message):
    status_msg = await message.answer("⏳ Зчитую реквізити квитанції...")

    file_id = None
    file_name = ""
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = "receipt.jpg"
    elif message.document:
        file_id = message.document.file_id
        file_name = (message.document.file_name or "receipt.pdf").
