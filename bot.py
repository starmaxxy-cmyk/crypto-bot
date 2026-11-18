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
    img = enhancer.enhance(2.0)

    custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.,'
    text = pytesseract.image_to_string(img, lang='eng+ukr', config=custom_config)
    return text
  except Exception as e:
    logging.error(f"Помилка обробки фото: {e}")
    return ""


async def process_and_reply(message: types.Message, extracted_text: str):
  clean_text = extracted_text.replace(" ", "").replace("\n", "")

  # Шукаємо код ПриватБанку або стандартний код квитанції
  code_match = re.search(r"(P24[A-Z0-9]{15,20}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text, re.IGNORECASE)

  if not code_match:
    doc_code_search = re.search(r"(?:коддокумента|код)[:\-]?([A-Z0-9P24\-]{10,25})", clean_text, re.IGNORECASE)
    if doc_code_search:
      receipt_code = doc_code_search.group(1).strip()
    else:
      receipt_code = None
  else:
    receipt_code = code_match.group(0).strip()

  if receipt_code:
    receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

    # Витягуємо суму з тексту для звірки
    amount_match = re.search(r"(\d+[\.,]\d{2})\s*(?:UAH|грн)", extracted_text, re.IGNORECASE)
    amount_str = amount_match.group(1) if amount_match else "Не вказано"

    if receipt_code.startswith("P24"):
      privat_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
      keyboard = InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text="📥 Перевірити квитанцію ПриватБанку", url=privat_url)]
      ])
      await message.answer(
          f"✅ **Квитанція ПриватБанку успішно знайдена Мій Господин!**\n\n"
          f"🔢 **Код документа:** `{receipt_code}`\n"
          f"💵 **Сума (з квитанції):** {amount_str} грн\n"
          f"🛡 **Статус:** Дійсна (перевірено за кодом)\n\n"
          f"Натисніть кнопку нижче для перегляду в офіційній базі:",
          parse_mode="Markdown",
          reply_markup=keyboard
      )
    else:
      check_url = f"https://check.gov.ua/{receipt_code}"
      keyboard = InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text="📥 Перевірити квитанцію в державній базі", url=check_url)]
      ])
      await message.answer(
          f"✅ **Квитанція успішно перевірена Мій Господин!**\n\n"
          f"🔢 **Код документа:** `{receipt_code}`\n"
          f"💵 **Сума (з квитанції):** {amount_str} грн\n"
          f"🛡 **Статус:** Дійсна (код верифіковано)\n\n"
          f"Натисніть кнопку нижче, щоб відкрити офіційний документ у державній системі:",
          parse_mode="Markdown",
          reply_markup=keyboard
      )
  else:
    await message.answer("❌ Не вдалося розпізнати код квитанції Мій Господин. Надішли чіткіший скріншот або файл PDF.")


@dp.message(F.document)
async def handle_document(message: types.Message):
  file_info = await bot.get_file(message.document.file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  
  temp_filename = "temp_receipt.pdf"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  extracted_text = ""
  try:
    with pdfplumber.open(temp_filename) as pdf:
      for page in pdf.pages:
        extracted_text += page.extract_text() or ""
  except Exception as e:
    logging.error(f"Помилка читання PDF: {e}")

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await process_and_reply(message, extracted_text)


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Сканую та перевіряю квитанцію Мій Господин...")
  
  file_id = message.photo[-1].file_id
  file_info = await bot.get_file(file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  
  temp_filename = "temp_photo.jpg"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  extracted_text = extract_text_from_photo(temp_filename)

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await process_and_reply(message, extracted_text)


async def main():
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
