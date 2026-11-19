import asyncio
from datetime import datetime
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import pdfplumber
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не задано змінну середовища BOT_TOKEN")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
USERS_FILE = "allowed_users.json"

KYIV_TZ = ZoneInfo("Europe/Kyiv")
CURRENCIES = ["GBP", "KZT", "UAH", "USD", "EUR", "THB", "GEL"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
bot = Bot(token=TOKEN)
dp = Dispatcher()


def load_allowed_users() -> set[int]:
    users = {ADMIN_ID} if ADMIN_ID else set()
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                users.update(int(uid) for uid in data)
        except Exception:
            pass
    return users


def save_allowed_users(users: set[int]) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(users), f)


ALLOWED_USERS = load_allowed_users()

# Зберігаємо (chat_id, message_id) для очищення о 00:00 (без збереження самих даних у файл)
tracked_messages: list[tuple[int, int]] = []


def track_msg(chat_id: int | None, message_id: int | None) -> None:
    if chat_id and message_id:
        pair = (chat_id, message_id)
        if pair not in tracked_messages:
            tracked_messages.append(pair)


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if isinstance(event, types.Message):
            track_msg(event.chat.id, event.message_id)
            if event.text and event.text.strip().startswith("/id"):
                return await handler(event, data)

        if user.id not in ALLOWED_USERS:
            if (
                isinstance(event, types.Message)
                and event.text
                and event.text.strip().startswith("/start")
            ):
                await event.answer("⛔ Доступ заборонено. Зверніться до адміністратора.")
            elif isinstance(event, types.CallbackQuery):
                await event.answer("⛔ Доступ заборонено", show_alert=True)
            return

        return await handler(event, data)


dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())

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
        match = re.search(
            r"([0-9]+(?:[.,][0-9]+)?)\s*[^\n]*?\b(?:USDT|USDC)\b",
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

    if not has_error and not in_text and not out_text:
        return None

    cash_flag = is_cash_direction(in_text, out_text)
    if has_error:
        inc_note = build_incorrect_amount_note(text, in_text, out_text)
        base_part = (
            f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}".strip()
            if (in_text or out_text)
            else f"#{order_id}."
        )
        line = f"{base_part} | {inc_note}"
        amount = inc_note
    else:
        line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
        amount = extract_target_amount(in_text, out_text)

    return {
        "order_id": order_id_num,
        "line": line,
        "amount": amount,
        "in_text": in_text,
        "out_text": out_text,
        "is_cash": cash_flag,
        "raw_text": text,
    }


def sort_orders_ascending(orders_list: list[dict]) -> list[dict]:
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
    directions_text = (
        f"{order.get('in_text', '')} {order.get('out_text', '')} {order.get('raw_text', '')}"
    )

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


@dp.message(Command("id"))
async def cmd_get_id(message: types.Message):
    sent = await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )
    track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("add"))
async def cmd_add_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        sent = await message.answer("Формат: <code>/add 123456789</code>", parse_mode="HTML")
        track_msg(sent.chat.id, sent.message_id)
        return
    new_id = int(parts[1])
    ALLOWED_USERS.add(new_id)
    save_allowed_users(ALLOWED_USERS)
    sent = await message.answer(
        f"✅ Користувача <code>{new_id}</code> додано до списку доступу.",
        parse_mode="HTML",
    )
    track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("del"))
async def cmd_del_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        sent = await message.answer("Формат: <code>/del 123456789</code>", parse_mode="HTML")
        track_msg(sent.chat.id, sent.message_id)
        return
    target_id = int(parts[1])
    if target_id == ADMIN_ID:
        sent = await message.answer("❌ Не можна видалити головного адміністратора.")
        track_msg(sent.chat.id, sent.message_id)
        return
    if target_id in ALLOWED_USERS:
        ALLOWED_USERS.remove(target_id)
        save_allowed_users(ALLOWED_USERS)
        sent = await message.answer(
            f"🗑 Користувача <code>{target_id}</code> видалено.",
            parse_mode="HTML",
        )
        track_msg(sent.chat.id, sent.message_id)
    else:
        sent = await message.answer("Цього ID немає у списку.")
        track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("users"))
async def cmd_list_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    users_str = "\n".join(
        f"• <code>{uid}</code> {'👑 (Admin)' if uid == ADMIN_ID else ''}"
        for uid in sorted(ALLOWED_USERS)
    )
    sent = await message.answer(
        f"👥 <b>Доступ до бота мають:</b>\n{users_str}",
        parse_mode="HTML",
    )
    track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    orders = get_today_orders()
    text = (
        f"Оброблено заявок за сьогодні: {len(orders)}. "
        f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
        f"Оберіть валюту або напрямок кнопкою нижче:"
    )
    sent = await message.answer(text, reply_markup=build_main_keyboard())
    track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    orders = get_today_orders()
    sent = await message.answer(
        f"Бот активний для команди. Дата (Київ): {daily_orders_state['date']}. "
        f"Заявок у пам'яті за сьогодні: {len(orders)}.",
        reply_markup=build_main_keyboard(),
    )
    track_msg(sent.chat.id, sent.message_id)


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
    track_msg(status_msg.chat.id, status_msg.message_id)

    file_id = None
    file_name = ""
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = "receipt.jpg"
    elif message.document:
        file_id = message.document.file_id
        file_name = (message.document.file_name or "receipt.pdf").lower()

    if not file_id:
        await status_msg.edit_text("❌ Не вдалося отримати файл.")
        return

    file = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file.file_path)

    user_tag = message.from_user.id if message.from_user else "team"
    local_filename = f"temp_{user_tag}_{file_name}"
    with open(local_filename, "wb") as f:
        f.write(downloaded_file.read())

    extracted_text = ""
    if local_filename.endswith(".pdf"):
        try:
            with pdfplumber.open(local_filename) as pdf:
                for page in pdf.pages:
                    extracted_text += page.extract_text() or ""
        except Exception:
            pass

    if not extracted_text.strip():
        extracted_text = extract_full_text(local_filename)

    if os.path.exists(local_filename):
        os.remove(local_filename)

    receipt_code = extract_receipt_code(extracted_text)

    amount = "Не вказано"
    found_amounts = re.findall(r"\b([0-9]{1,3}(?:\s?[0-9]{3})*[\.,]\d{2})\b", extracted_text)
    valid_amounts = []
    for a in found_amounts:
        clean_a = a.replace(" ", "").replace(",", ".")
        try:
            val = float(clean_a)
            if val > 1.0:
                valid_amounts.append(val)
        except Exception:
            pass

    if valid_amounts:
        amount = f"{max(valid_amounts):.2f}"

    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", extracted_text)
    receipt_date = date_match.group(1) if date_match else "Не вказано"

    check_url, bank_name = build_check_link(receipt_code, extracted_text)

    response_text = (
        f"✅ Номер квитанції: `{receipt_code}`\n\n"
        f"💵 Сума: {amount} грн\n"
        f"📅 Дата: {receipt_date}\n\n"
        f"⚠️ Квитанція не перевірена, будь ласка, перевірте її самі, бо я тупий!!!\n\n"
        f"Натисніть кнопку нижче для переходу до офіційного джерела:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🏛 Відкрити в {bank_name}", url=check_url)]
        ]
    )

    await status_msg.edit_text(response_text, reply_markup=keyboard, parse_mode="Markdown")


@dp.channel_post(F.text)
async def catch_channel_payout_orders(message: types.Message):
    if not is_today_message(message):
        return

    entry = parse_order_entry(message.text or "")
    if entry:
        add_order_if_new(entry)


@dp.message(F.text & ~F.text.startswith("/"))
async def catch_orders_quietly(message: types.Message):
    if not is_today_message(message):
        return

    entry = parse_order_entry(message.text or "")
    if entry:
        add_order_if_new(entry)


async def send_filtered_report(target_message: types.Message, tag: str | None):
    orders = get_today_orders()
    today_str = daily_orders_state["date"]
    filtered = sort_orders_ascending([item for item in orders if matches_filter(item, tag)])
    label = format_filter_label(tag)
    markup = build_report_keyboard(tag)

    if not filtered:
        sent = await target_message.answer(
            f"Оброблено заявок за сьогодні ({today_str}) [{label}]: 0. "
            f"Заповніть таблицю до 00:00, бо дані будуть очищені.",
            reply_markup=markup,
        )
        track_msg(sent.chat.id, sent.message_id)
        return

    lines_block = "📋 1. Стовпчик «Операція»:\n\n```text\n"
    for item in filtered:
        lines_block += f"{item['line']}\n"
    lines_block += "```"

    amounts_block = "💵 2. Стовпчик «Сума заявки»:\n\n```text\n"
    for item in filtered:
        amounts_block += f"{item['amount']}\n"
    amounts_block += "```"

    header = (
        f"Оброблено заявок за сьогодні ({today_str}) [{label}]: {len(filtered)}. "
        f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
    )

    sent = await target_message.answer(
        f"{header}{lines_block}\n\n{amounts_block}",
        parse_mode="Markdown",
        reply_markup=markup,
    )
    track_msg(sent.chat.id, sent.message_id)


@dp.message(Command("export"))
async def export_channel_batch(message: types.Message):
    await send_filtered_report(message, tag="ALL")


@dp.callback_query(F.data.startswith("filter:"))
async def filter_by_currency_callback(callback: types.CallbackQuery):
    await callback.answer()
    tag = callback.data.split(":", 1)[1] if callback.data else "ALL"
    if callback.message:
        await send_filtered_report(callback.message, tag=tag)


@dp.callback_query(F.data.startswith("clear_filter:"))
async def clear_filter_callback(callback: types.CallbackQuery):
    tag = callback.data.split(":", 1)[1] if callback.data else "ALL"
    label = format_filter_label(tag)
    removed = clear_orders_by_tag(tag)
    remaining_total = len(get_today_orders())
    await callback.answer(f"Очищено [{label}]: {removed}", show_alert=False)
    if callback.message:
        sent = await callback.message.answer(
            f"🗑 Очищено заявок за фільтром [{label}]: {removed}.\n"
            f"Залишилось усього за сьогодні: {remaining_total}. "
            f"Повне очищення відбудеться о 00:00.",
            reply_markup=build_main_keyboard(),
        )
        track_msg(sent.chat.id, sent.message_id)


@dp.callback_query(F.data == "export_today")
async def export_today_callback(callback: types.CallbackQuery):
    await callback.answer()
    if callback.message:
        await send_filtered_report(callback.message, tag="ALL")


async def daily_midnight_cleaner():
    while True:
        await asyncio.sleep(60)
        today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
        if daily_orders_state["date"] != today_str:
            cleared_count = len(daily_orders_state["orders"])
            reset_orders_for_today()

            for chat_id, msg_id in list(tracked_messages):
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
            tracked_messages.clear()

            notify_text = (
                f"🕛 <b>00:00 (Київ) — чат і базу повністю очищено!</b>\n"
                f"Усі заявки вище очищено (видалено за вчора: {cleared_count}).\n"
                f"Почато новий день ({today_str}). Оберіть напрямок:"
            )
            for uid in list(ALLOWED_USERS):
                if not uid:
                    continue
                try:
                    sent = await bot.send_message(
                        chat_id=uid,
                        text=notify_text,
                        parse_mode="HTML",
                        reply_markup=build_main_keyboard(),
                    )
                    track_msg(sent.chat.id, sent.message_id)
                except Exception as e:
                    logging.warning("Не вдалося надіслати 00:00 сповіщення %s: %s", uid, e)

            logging.info(
                "О 00:00 (Europe/Kyiv) виконано автоочистку. Видалено заявок: %s, дата: %s",
                cleared_count,
                today_str,
            )


async def main():
    cleaner_task = asyncio.create_task(daily_midnight_cleaner())
    try:
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                await dp.start_polling(
                    bot,
                    drop_pending_updates=True,
                    allowed_updates=dp.resolve_used_update_types(),
                )
                break
            except TelegramConflictError:
                logging.warning("Конфлікт getUpdates — інший процес використовує токен. Чекаю 5 сек...")
                await asyncio.sleep(5)
    finally:
        cleaner_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
