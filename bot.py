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

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Сховище заявок за поточний день:
# {"date": "2026-11-19", "orders": [{"line": "...", "amount": "..."}]}
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


def get_today_orders() -> list[dict[str, str]]:
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


def extract_usdt_amount(in_text: str, out_text: str) -> str:
    """
    Витягує суму саме в USDT з IN або OUT
    (наприклад, 'OUT:724.85 Tether TRC20 USDT' -> '724,85').
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

    fallback = re.search(r'([0-9]+(?:[.,][0-9]+)?)', f"{out_text} {in_text}")
    if fallback:
        return fallback.group(1).replace('.', ',')
    return ""


def is_cash_exchange(in_text: str, out_text: str) -> bool:
    """Перевіряє, чи є кеш у напрямках IN / OUT (наприклад, Cash UAH, Cash Kyiv UAH)."""
    directions_lower = f"{in_text} {out_text}".lower()
    return any(word in directions_lower for word in ("cash", "кеш", "готівк"))


def parse_order_entry(text: str) -> dict[str, str] | None:
    """
    Повертає словник з очищеним рядком операції та сумою заявки в USDT.
    Бере лише:
    - статус 'In payout processing'
    - валюту 'UAH'
    - виключає кеш у напрямках IN/OUT ('Cash UAH', 'Cash Kyiv UAH' тощо),
      але НЕ чіпає 'crypto-cash' у полі 'Changed by'.
    """
    if not text:
        return None
    if "In payout processing" not in text:
        return None
    if "UAH" not in text:
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

    if is_cash_exchange(in_text, out_text):
        return None

    line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
    amount = extract_usdt_amount(in_text, out_text)
    return {"line": line, "amount": amount}


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
    await message.answer(
        "Надішли мені квитанцію Мій Господин 🫡\n\n"
        "Або напиши /export, щоб забрати два стовпчики (операції та суми в USDT) за сьогодні."
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


@dp.message(F.photo | F.document)
async def handle_receipt(message: types.Message):
    status_msg = await message.answer("⏳ Зчитую реквізити квитанції Мій Господин...")

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
# 2. ЗБІР ЗАЯВОК (КАНАЛ + ПЕРЕСЛАНІ) ТА ЕКСПОРТ
# ==========================================
@dp.channel_post(F.text)
async def catch_channel_payout_orders(message: types.Message):
    if not is_today_message(message):
        return

    entry = parse_order_entry(message.text or "")
    if entry:
        get_today_orders().append(entry)


@dp.message(F.text & ~F.text.startswith('/'))
async def catch_forwarded_payout_orders(message: types.Message):
    if not is_today_message(message):
        return

    text = message.text or ""
    if "In payout processing" not in text or "UAH" not in text:
        return

    entry = parse_order_entry(text)
    if entry:
        orders = get_today_orders()
        orders.append(entry)
        await message.answer(
            f"✅ Заявку додано (всього за сьогодні: {len(orders)}). Напишіть /export, коли буде готово."
        )


@dp.message(Command("export"))
async def export_channel_batch(message: types.Message):
    orders = get_today_orders()
    today_str = daily_orders_state["date"]

    if not orders:
        await message.answer(
            f"⚠️ За сьогодні ({today_str}) нових UAH заявок (без кешу) у списку поки немає."
        )
        return

    lines_block = "📋 1. Стовпчик «Операція»:\n\n```text\n"
    for item in orders:
        lines_block += f"{item['line']}\n"
    lines_block += "```"

    amounts_block = "💵 2. Стовпчик «Сума заявки» (USDT):\n\n```text\n"
    for item in orders:
        amounts_block += f"{item['amount']}\n"
    amounts_block += "```\n💡 Скопіюйте спочатку один блок і вставте (Ctrl+V), потім — другий у сусідній стовпчик."

    daily_orders_state["orders"] = []
    await message.answer(f"{lines_block}\n\n{amounts_block}", parse_mode="Markdown")


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
