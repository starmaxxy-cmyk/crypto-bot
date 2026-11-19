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
    count = len(orders)
    text = (
        f"Оброблено заявок за сьогодні: {count}.\n"
        f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
        f"Оберіть валюту / напрямок кнопкою нижче або заберіть звіт:"
    )
    await message.answer(text, reply_markup=build_main_keyboard())


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
            f"Оброблено заявок за сьогодні: {len(orders)}.\n"
            f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
            f"Оберіть валюту / напрямок кнопкою нижче або заберіть звіт:",
            reply_markup=build_main_keyboard(),
        )


# ==========================================
# 1. ОБРОБКА КВИТАНЦІЙ (СКРІНШОТИ ТА PDF)
# ==========================================
def extract_full_text(image_path: str) -> str:
    try:
        img = Image.open(image_path)
        img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
        img = img.convert('L')
        img = ImageOps.autocontrast(img, cutoff=2)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.5)
        text = pytesseract.image_to_string(img, lang='eng+ukr')
        return text
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
        file_name = (message.document.file_name or "receipt.pdf").lower()

    if not file_id:
        await status_msg.edit_text("❌ Не вдалося отримати файл.")
        return

    file = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file.file_path)

    local_filename = f"temp_{message.from_user.id}_{file_name}"
    with open(local_filename, "wb") as f:
        f.write(downloaded_file.read())

    extracted_text = ""
    if local_filename.endswith('.pdf'):
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
    found_amounts = re.findall(r'\b([0-9]{1,3}(?:\s?[0-9]{3})*[\.,]\d{2})\b', extracted_text)

    valid_amounts = []
    for a in found_amounts:
        clean_a = a.replace(' ', '').replace(',', '.')
        try:
            val = float(clean_a)
            if val > 1.0:
                valid_amounts.append(val)
        except Exception:
            pass

    if valid_amounts:
        amount = f"{max(valid_amounts):.2f}"

    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})', extracted_text)
    receipt_date = date_match.group(1) if date_match else "Не вказано"

    check_url, bank_name = build_check_link(receipt_code, extracted_text)

    response_text = (
        f"✅ Номер квитанції: `{receipt_code}`\n\n"
        f"💵 Сума: {amount} грн\n"
        f"📅 Дата: {receipt_date}\n\n"
        f"⚠️ Квитанція не перевірена, будь ласка, перевірте її самі, бо я тупий!!!\n\n"
        f"Натисніть кнопку нижче для переходу до офіційного джерела:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏛 Відкрити в {bank_name}", url=check_url)]
    ])

    await status_msg.edit_text(response_text, reply_markup=keyboard, parse_mode="Markdown")


# ==========================================
# 2. ТИХИЙ ЗБІР ЗАЯВОК ТА ЕКСПОРТ
# ==========================================
@dp.channel_post(F.text)
async def catch_channel_payout_orders(message: types.Message):
    if not is_today_message(message):
        return

    entry = parse_order_entry(message.text or "")
    if entry:
        get_today_orders().append(entry)


@dp.message(F.text & ~F.text.startswith('/'))
async def catch_orders_quietly(message: types.Message):
    if not is_today_message(message):
        return

    entry = parse_order_entry(message.text or "")
    if entry:
        get_today_orders().append(entry)


async def send_filtered_report(target_message: types.Message, tag: str | None, clear_after: bool = False):
    orders = get_today_orders()
    today_str = daily_orders_state["date"]
    filtered = [item for item in orders if matches_filter(item, tag)]
    label = format_filter_label(tag)

    if not filtered:
        await target_message.answer(
            f"Оброблено заявок за сьогодні ({today_str}) [{label}]: 0.\n"
            f"Заповніть таблицю до 00:00, бо дані будуть очищені.",
            reply_markup=build_main_keyboard(),
        )
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
        f"Оброблено заявок за сьогодні ({today_str}) [{label}]: {len(filtered)}.\n"
        f"Заповніть таблицю до 00:00, бо дані будуть очищені.\n\n"
    )

    if clear_after:
        daily_orders_state["orders"] = []

    await target_message.answer(
        f"{header}{lines_block}\n\n{amounts_block}",
        parse_mode="Markdown",
        reply_markup=build_main_keyboard(),
    )


@dp.message(Command("export"))
async def export_channel_batch(message: types.Message):
    await send_filtered_report(message, tag="ALL", clear_after=True)


@dp.callback_query(F.data.startswith("filter:"))
async def filter_by_currency_callback(callback: types.CallbackQuery):
    await callback.answer()
    tag = callback.data.split(":", 1)[1] if callback.data else "ALL"
    if callback.message:
        await send_filtered_report(callback.message, tag=tag, clear_after=False)


@dp.callback_query(F.data == "export_today")
async def export_today_callback(callback: types.CallbackQuery):
    await callback.answer()
    if callback.message:
        await send_filtered_report(callback.message, tag="ALL", clear_after=True)


async def daily_midnight_cleaner():
    """Щохвилини перевіряє настання 00:00 за Києвом і очищує вчорашні заявки."""
    while True:
        await asyncio.sleep(60)
        today_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
        if daily_orders_state["date"] != today_str:
            cleared_count = len(daily_orders_state["orders"])
            reset_orders_for_today()
            logging.info(
                "О 00:00 (Europe/Kyiv) виконано автоочистку заявок. Видалено записів: %s, нова дата: %s",
                cleared_count,
                today_str,
            )


async def main():
    cleaner_task = asyncio.create_task(daily_midnight_cleaner())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        cleaner_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
