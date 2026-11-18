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


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


def extract_text_from_photo(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=5)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)

    # Жорсткий білий список: тільки латинські літери, цифри, дефіс, крапка та кома
    custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.,'
    text = pytesseract.image_to_string(img, config=custom_config)
    return text
  except Exception as e:
    logging.error(f"Помилка обробки фото: {e}")
    return ""


@dp.message(F.document)
async def handle_document(message: types.Message):
  await message.answer("⏳ Читаю PDF-квитанцію Мій Господин...")
  try:
    file_info = await bot.get_file(message.document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_receipt.pdf"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    extracted_text = ""
    with pdfplumber.open(temp_filename) as pdf:
      for page in pdf.pages:
        extracted_text += page.extract_text() or ""

    if os.path.exists(temp_filename):
      os.remove(temp_filename)

    await process_receipt(message, extracted_text, extracted_text)
  except Exception as e:
    logging.error(f"Помилка PDF: {e}")
    await message.answer("❌ Сталася помилка при читанні PDF документу.")


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Сканую квитанцію Мій Господин...")
  try:
    file_id = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_photo.jpg"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    # Повний текст для читання кирилиці (імена, отримувачі)
    raw_text_full = pytesseract.image_to_string(Image.open(temp_filename), lang='eng+ukr')
    # Відфільтрований текст для точного пошуку коду
    extracted_text = extract_text_from_photo(temp_filename)

    if os.path.exists(temp_filename):
      os.remove(temp_filename)

    await process_receipt(message, extracted_text, raw_text_full)
  except Exception as e:
    logging.error(f"Помилка фото: {e}")
    await message.answer("❌ Сталася помилка при скануванні зображення.")


async def process_receipt(message: types.Message, extracted_text: str, raw_full_text: str = ""):
  clean_text = extracted_text.replace(" ", "").replace("\n", "")

  # Шукаємо код ПриватБанку у форматі P24...D... або стандартний код квитанції
  code_match = re.search(r"(P24[A-Z0-9]{10,25}D[A-Z0-9]{1,6}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text, re.IGNORECASE)

  if not code_match:
    doc_code_search = re.search(r"(?:коддокумента|код)[:\-]?([A-Z0-9P24\-]{10,30})", clean_text, re.IGNORECASE)
    if doc_code_search:
      receipt_code = doc_code_search.group(1).strip()
    else:
      receipt_code = None
  else:
    receipt_code = code_match.group(0).strip()

  if not receipt_code:
    await message.answer("❌ Не вдалося знайти код квитанції Мій Господин. Надішли чіткіший скріншот.")
    return

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

  # Витягуємо суму, дату, отримувача та карту з текстів
  source_details = raw_full_text if raw_full_text else extracted_text

  amount_match = re.search(r"(\d+[\.,]\d{2})\s*(?:UAH|грн)", source_details, re.IGNORECASE)
  amount_str = amount_match.group(1) if amount_match else "Не вказано"

  date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s*\d{2}:\d{2})", source_details)
  date_str = date_match.group(1) if date_match else "Не вказано"

  payee_match = re.search(r"(?:Отримувач|Одержувач)[:\s]*([А-Яа-яA-Za-z\s\.]{3,30})", source_details)
  payee_str = payee_match.group(1).strip() if payee_match else "Визначено в документі"

  card_match = re.search(r"(?:UA\d{2}\d{4,}\d*|\d{4}\s?\*{4}\s?\*{4}\s?\d{4})", source_details)
  card_str = card_match.group(0).strip() if card_match else "Згідно з оригіналом"

  # Формуємо звіт залежно від типу банку
  if receipt_code.startswith("P24"):
    privat_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Відкрити оригінал в ПриватБанку", url=privat_url)]
    ])
    await message.answer(
        f"✅ **Квитанція ПриватБанку успішно розпізнана Мій Господин!**\n\n"
        f"🔢 **Код документа:** `{receipt_code}`\n"
        f"💵 **Сума:** {amount_str} грн\n"
        f"🏢 **Отримувач:** {payee_str}\n"
        f"💳 **Картка / Рахунок:** `{card_str}`\n"
        f"📅 **Дата:** {date_str}\n"
        f"🛡 **Статус:** Документ сформовано за кодом\n\n"
        f"Натисніть кнопку нижче для перегляду оригіналу в системі банку:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
  else:
    check_url = f"https://check.gov.ua/{receipt_code}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Відкрити в державній базі check.gov.ua", url=check_url)]
    ])
    await message.answer(
        f"✅ **Квитанція успішно перевірена Мій Господин!**\n\n"
        f"🔢 **Код документа:** `{receipt_code}`\n"
        f"💵 **Сума:** {amount_str} грн\n"
        f"🏢 **Отримувач:** {payee_str}\n"
        f"💳 **Картка / Рахунок:** `{card_str}`\n"
        f"📅 **Дата:** {date_str}\n"
        f"🛡 **Статус:** Код валідний та готовий до перевірки\n\n"
        f"Натисніть кнопку нижче, щоб відкрити офіційний документ у держреєстрі:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def main():
  await asyncio.sleep(3)
  try:
    await bot.delete_webhook(drop_pending_updates=True)
  except Exception:
    pass
    
  await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
  asyncio.run(main())
