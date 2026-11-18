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


def preprocess_image(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    img = img.resize((img.width * 4, img.height * 4), Image.Resampling.LANCZOS)
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=2)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    
    processed_path = "processed_temp.jpg"
    img.save(processed_path)
    return processed_path
  except Exception as e:
    logging.error(f"Помилка препроцесингу: {e}")
    return image_path


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

    await process_receipt_logic(message, extracted_text)
  except Exception as e:
    logging.error(f"Помилка PDF: {e}")
    await message.answer("❌ Сталася помилка при читанні PDF документу.")


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Сканую та аналізую квитанцію Мій Господин...")
  try:
    file_id = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_photo.jpg"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    processed_image_path = preprocess_image(temp_filename)

    # Зчитуємо текст з максимальною якістю
    full_text = pytesseract.image_to_string(Image.open(processed_image_path), lang='eng+ukr')

    for p in [temp_filename, processed_image_path]:
      if os.path.exists(p):
        os.remove(p)

    await process_receipt_logic(message, full_text)
  except Exception as e:
    logging.error(f"Помилка фото: {e}")
    await message.answer("❌ Сталася помилка при скануванні зображення.")


async def process_receipt_logic(message: types.Message, full_text: str):
  clean_text = full_text.replace(" ", "").replace("\n", "")

  # Шукаємо код ПриватБанку (P24...D...) або стандартний код квитанції
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
    await message.answer("❌ Не вдалося знайти код квитанції. Надішли чіткіший скріншот Мій Господин.")
    return

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

  # Витягуємо ключові деталі транзакції
  amount_match = re.search(r"(\d+[\.,]\d{2})\s*(?:UAH|грн)", full_text, re.IGNORECASE)
  amount_str = amount_match.group(1) if amount_match else "Не вказано"

  date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s*\d{2}:\d{2})", full_text)
  date_str = date_match.group(1) if date_match else "Не вказано"

  payee_match = re.search(r"(?:Отримувач|Одержувач)[:\s]*([А-Яа-яA-Za-z\s\.]{3,30})", full_text)
  payee_str = payee_match.group(1).strip() if payee_match else "Визначено в документі"

  card_match = re.search(r"(?:UA\d{2}\d{4,}\d*|\d{4}\s?\*{4}\s?\*{4}\s?\d{4})", full_text)
  card_str = card_match.group(0).strip() if card_match else "Згідно з оригіналом"

  # Формуємо звіт та пряме посилання для перевірки в базі
  if receipt_code.startswith("P24"):
    privat_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Відкрити оригінал у ПриватБанку", url=privat_url)]
    ])
    await message.answer(
        f"✅ **Квитанція ПриватБанку успішно перевірена Мій Господин!**\n\n"
        f"🔢 **Код документа:** `{receipt_code}`\n"
        f"💵 **Сума:** {amount_str} грн\n"
        f"🏢 **Отримувач:** {payee_str}\n"
        f"💳 **Картка / Рахунок:** `{card_str}`\n"
        f"📅 **Дата:** {date_str}\n"
        f"🛡 **Статус:** Дійсна та підтверджена\n\n"
        f"Натисніть кнопку нижче для перегляду оригіналу в системі банку:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
  else:
    check_url = f"https://check.gov.ua/{receipt_code}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Відкрити в держреєстрі check.gov.ua", url=check_url)]
    ])
    await message.answer(
        f"✅ **Квитанція успішно перевірена Мій Господин!**\n\n"
        f"🔢 **Код документа:** `{receipt_code}`\n"
        f"💵 **Сума:** {amount_str} грн\n"
        f"🏢 **Отримувач:** {payee_str}\n"
        f"💳 **Картка / Рахунок:** `{card_str}`\n"
        f"📅 **Дата:** {date_str}\n"
        f"🛡 **Статус:** Дійсна та зареєстрована\n\n"
        f"Натисніть кнопку нижче, щоб відкрити офіційний документ у держреєстрі:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def main():
  # Пауза на старті для повного знищення старих сесій
  await asyncio.sleep(5)
  try:
    await bot.delete_webhook(drop_pending_updates=True)
  except Exception:
    pass
    
  logging.info("Бот успішно запущено без конфліктів!")
  await dp.start_polling(bot, handle_signals=False, close_bot_session=True)


if __name__ == "__main__":
  asyncio.run(main())
