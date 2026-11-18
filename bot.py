import asyncio
import logging
import os
import re
import pdfplumber
import requests
from PIL import Image
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = "9015943013:AAFOf-TUX4AeklQ_JWqsCs34Wju_cEHrRbE"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


# Універсальна перевірка та надсилання посилання на файл квитанції
async def process_text_and_reply(message: types.Message, extracted_text: str):
  # Шукаємо 16-значний код квитанції (наприклад, T20K-357H-T17C-H7PH)
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)

  if code_match:
    receipt_code = code_match.group(0)
    await message.answer(
        f"🔍 Знайдено код квитанції: `{receipt_code}`\n"
        f"🌐 Перевіряю через державну базу...",
        parse_mode="Markdown"
    )

    try:
      check_url = f"https://check.gov.ua/api/receipts/{receipt_code}"
      response = requests.get(check_url, timeout=10)

      if response.status_code == 200:
        data = response.json()
        if data and "id" in data:
          sum_uah = data.get("amount", "Не вказано")
          payer = data.get("payerName", "Не вказано")
          payee = data.get("payeeName", "Не вказано")
          payment_date = data.get("paymentDate", "Не вказано")
          
          # Офіційне посилання на скачування самого PDF-файлу квитанції
          pdf_download_url = f"https://check.gov.ua/receipts/{receipt_code}"
          
          keyboard = InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📥 Завантажити файл квитанції (PDF)", url=pdf_download_url)]
          ])

          await message.answer(
              f"✅ **Квитанція успішно перевірена та дійсна Мій Господин!**\n\n"
              f"💵 **Сума:** {sum_uah} грн\n"
              f"👤 **Відправник:** {payer}\n"
              f"🏢 **Отримувач:** {payee}\n"
              f"📅 **Дата:** {payment_date}\n"
              f"🔢 **Код:** `{receipt_code}`",
              parse_mode="Markdown",
              reply_markup=keyboard
          )
        else:
          await message.answer(
              f"❌ Квитанцію з кодом `{receipt_code}` не знайдено в державній базі, Мій Господин.",
              parse_mode="Markdown"
          )
      else:
        await message.answer(
            f"✅ Код витягнуто: `{receipt_code}`\n\n"
            f"📥 Офіційний файл квитанції: https://check.gov.ua/{receipt_code}",
            parse_mode="Markdown"
        )
    except Exception as e:
      logging.error(f"Помилка API: {e}")
      await message.answer(
          f"✅ Код знайдено: `{receipt_code}`\n\n"
          f"📥 Офіційний файл квитанції: https://check.gov.ua/{receipt_code}",
          parse_mode="Markdown"
      )
  else:
    await message.answer("Надішли мені квитанцію Мій Господин")


# Обробка PDF-документів
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

  await process_text_and_reply(message, extracted_text)


# Обробка скріншотів та фото з галереї
@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Зчитую дані зі скріншота Мій Господин...")
  
  file_id = message.photo[-1].file_id
  file_info = await bot.get_file(file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  
  temp_filename = "temp_photo.jpg"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  extracted_text = ""
  try:
    # Пробуємо зчитати текст з картинки
    extracted_text = pytesseract.image_to_string(Image.open(temp_filename), lang='ukr+eng')
  except Exception as e:
    logging.error(f"Помилка OCR з фото: {e}")

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await process_text_and_reply(message, extracted_text)


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
