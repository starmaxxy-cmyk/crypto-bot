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

TOKEN = "9015943013:AAFOf-TUX4AeklQ_JWqsCs34Wju_cEHrRbE"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer(
      "Привіт! 👋 Надішли мені квитанцію **як фото, скріншот або PDF**, "
      "і я перевірю її справжність у базі."
  )


# Універсальна функція перевірки коду
async def check_receipt_code(message: types.Message, extracted_text: str):
  # Шукаємо 16-значний код квитанції (наприклад, 9M4E-AC4M-M1CA-K097)
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)

  if code_match:
    receipt_code = code_match.group(0)
    await message.answer(
        f"🔍 Знайдено код квитанції: `{receipt_code}`\n"
        f"🌐 Перевіряю через державну базу check.gov.ua...",
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
          await message.answer(
              f"✅ **Квитанція успішно знайдена та дійсна!**\n\n"
              f"💵 **Сума:** {sum_uah} грн\n"
              f"👤 **Відправник:** {payer}\n"
              f"🔢 **Код:** `{receipt_code}`",
              parse_mode="Markdown"
          )
        else:
          await message.answer(
              f"❌ **Квитанцію з кодом `{receipt_code}` не знайдено в базі.**",
              parse_mode="Markdown"
          )
      else:
        await message.answer(
            f"✅ Код витягнуто: `{receipt_code}`\n\n"
            f"🔗 Перевір квитанцію на офіційному сайті: https://check.gov.ua/",
            parse_mode="Markdown"
        )
    except Exception as e:
      logging.error(f"Помилка API: {e}")
      await message.answer(
          f"✅ Код успішно знайдено: `{receipt_code}`\n\n"
          f"🔗 Посилання для перевірки: https://check.gov.ua/",
            parse_mode="Markdown"
      )
  else:
    await message.answer(
        "⚠️ Не вдалося знайти код квитанції на цьому зображенні чи у файлі. "
        "Переконайся, що скріншот чіткий і на ньому повністю видно текст чека."
    )


# Обробка PDF-файлів
@dp.message(F.document)
async def handle_document(message: types.Message):
  file_name = message.document.file_name.lower()
  if not file_name.endswith(".pdf"):
    await message.answer("⚠️ Будь ласка, надішли квитанцію у форматі PDF.")
    return

  await message.answer("⏳ Зчитую PDF...")
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

  await check_receipt_code(message, extracted_text)


# Обробка фото та скріншотів
@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Зчитую текст зі скріншота...")

  file_id = message.photo[-1].file_id
  file_info = await bot.get_file(file_id)
  downloaded_file = await bot.download_file(file_info.file_path)

  temp_filename = "temp_photo.jpg"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  extracted_text = ""
  try:
    # Розпізнаємо текст з картинки за допомогою Tesseract
    extracted_text = pytesseract.image_to_string(Image.open(temp_filename), lang='ukr+eng')
  except Exception as e:
    logging.error(f"Помилка OCR з фото: {e}")

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await check_receipt_code(message, extracted_text)


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
