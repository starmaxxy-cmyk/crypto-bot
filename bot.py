import asyncio
import logging
import os
import re
import pdfplumber
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Сховище для пакетів заявок
order_batches = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Надішли мені квитанцію Мій Господин 🫡")

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
    except Exception as e:
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
        file_name = message.document.file_name.lower()

    if not file_id:
        await status_msg.edit_text("❌ Не вдалося отримати файл.")
        return

    file = await bot.get_file(file_id)
    file_path_on_server = file.file_path
    downloaded_file = await bot.download_file(file_path_on_server)
    
    local_filename = f"temp_{message.from_user.id}_{file_name}"
    with open(local_filename, "wb") as f:
        f.write(downloaded_file.read())

    extracted_text = ""
    if local_filename.endswith('.pdf'):
        try:
            with pdfplumber.open(local_filename) as pdf:
                for page in pdf.pages:
                    extracted_text += page.extract_text() or ""
        except Exception as e:
            pass
    
    if not extracted_text.strip():
        extracted_text = extract_full_text(local_filename)

    if os.path.exists(local_filename):
        os.remove(local_filename)

    # 1. Парсинг коду квитанції
    code_match = re.search(r'(?:[A-Za-z0-9]{15,45}|[A-Z0-9-]{15,})', extracted_text)
    receipt_code = code_match.group(0) if code_match else "Не знайдено"

    # 2. Покращений парсинг суми (шукаємо чітко біля слів Сума/грн або великі числа з копійками)
    amount = "Не вказано"
    # Шукаємо шаблони типу "Сума: 1234.56" або схожі
    amount_match = re.search(r'(?:Сума|Сумма|Total|Amount|платпежу|оплати)[\s:]*([0-9\s]+[\.,]\d{2})', extracted_text, re.IGNORECASE)
    if amount_match:
        amount = amount_match.group(1).replace(' ', '').replace(',', '.')
    else:
        # Шукаємо всі грошові суми у тексті і беремо найбільш релевантну (не занадто малу комісійну)
        amounts = re.findall(r'\b([1-9]\d{1,5}[\.,]\d{2})\b', extracted_text)
        if amounts:
            # Беремо максимальну або останню знайдену суму (зазвичай загальна сума платіжки найбільша)
            amount = amounts[-1].replace(' ', '').replace(',', '.')

    # Посилання для перевірки
    if "P24" in receipt_code or "PRIVAT" in extracted_text.upper():
        check_url = f"https://www.privatbank.ua/sendmoney/check?reference={receipt_code}"
        bank_name = "ПриватБанку"
    else:
        check_url = f"https://check.gov.ua/{receipt_code}"
        bank_name = "check.gov.ua"

    response_text = (
        f"✅ Номер квитанції: `{receipt_code}`\n\n"
        f"💵 Сума: {amount} грн\n"
        f"📅 Дата: Не вказано\n\n"
        f"⚠️ Квитанція не перевірена, будь ласка, перевірте її самі, бо я тупий!!!\n\n"
        f"Натисніть кнопку нижче для переходу до офіційного джерела:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏛 Відкрити в {bank_name}", url=check_url)]
    ])

    await status_msg.edit_text(response_text, reply_markup=keyboard, parse_mode="Markdown")


# ==========================================
# 2. ПАКЕТНИЙ ЗБІР І ОЧИЩЕННЯ ЗАЯВОК
# ==========================================
@dp.message(F.text & ~F.text.startswith('/'))
async def catch_exchange_orders(message: types.Message):
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    
    if "#" not in text or "Exchange:" not in text:
        return 

    order_id_match = re.search(r'#(\d+)', text)
    order_id = order_id_match.group(1) if order_id_match else "00000"
    
    in_match = re.search(r'IN:\s*([^\n]+)', text)
    in_text = in_match.group(1).strip() if in_match else ""
    
    out_match = re.search(r'OUT:\s*([^\n]+)', text)
    out_text = out_match.group(1).split('\n')[0].strip() if out_match else ""

    cleaned_line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
    
    if user_id not in order_batches:
        order_batches[user_id] = []
        
    order_batches[user_id].append(cleaned_line)
    count = len(order_batches[user_id])
    
    await message.answer(f"✅ Заявку очищено! У стовпчику: {count}\nЯк закінчите пересилати, напишіть /export")

@dp.message(Command("export"))
async def export_batch(message: types.Message):
    user_id = message.from_user.id
    if user_id not in order_batches or not order_batches[user_id]:
        await message.answer("⚠️ Стовпчик порожній. Пересилайте заявки!")
        return
        
    output_block = "📋 **Готовий стовпчик для таблиці:**\n\n```text\n"
    for item in order_batches[user_id]:
        output_block += f"{item}\n"
    output_block += "```\n*💡 Скопіюйте текст із блоку вище, клікніть на першу клітинку в таблиці та натисніть Ctrl+V.*"
    
    order_batches[user_id] = []
    await message.answer(output_block, parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
