import asyncio
import logging
import os
import re
import pdfplumber
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

TOKEN = "9015943013:AAFOf-TUX4AeklQ_JWqsCs34Wju_cEHrRbE"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer(
      "Привіт! 👋 Надішли мені квитанцію **як документ (PDF) або скріншот (фото)**, "
      "і я перевірю її та надішлю тобі назад із підтвердженням."
  )


# Універсальна перевірка та відправка назад
async def process_and_reply(message: types.Message, file_bytes: bytes, file_name: str, extracted_text: str):
  # Шукаємо 16-значний код квитанції (наприклад, 20K-357H-T17C-H7PH або схожий за шаблоном)
  # Або шукаємо у будь-якому форматі код квитанції
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)

  # Якщо раптом у Monobank інший формат коду, можемо пошукати ключові слова або числа, 
  # але поки шукаємо стандартний паттерн check.gov.ua
  if code_match:
    receipt_code = code_match.group(0)
    
    try:
      check_url = f"https://check.gov.ua/api/receipts/{receipt_code}"
      response = requests.get(check_url, timeout=10)

      if response.status_code == 200:
        data = response.json()
        if data and "id" in data:
          sum_uah = data.get("amount", "Не вказано")
          payer = data.get("payerName", "Не вказано")
          
          input_file = BufferedInputFile(file_bytes, filename=file_name)
          await message.answer_document(
              input_file,
              caption=(
                  f"✅ **Квитанція дійсна та перевірена!**\n"
                  f"💵 **Сума:** {sum_uah} грн\n"
                  f"👤 **Відправник:** {payer}\n"
                  f"🔢 **Код:** `{receipt_code}`\n\n"
                  f"📄 Ось твоя квитанція ⬆️"
              ),
              parse_mode="Markdown"
          )
        else:
          input_file = BufferedInputFile(file_bytes, filename=file_name)
          await message.answer_document(
              input_file,
              caption=f"⚠️ Код `{receipt_code}` знайдено, але в базі check.gov.ua запис відсутній.",
              parse_mode="Markdown"
          )
      else:
        input_file = BufferedInputFile(file_bytes, filename=file_name)
        await message.answer_document(
            input_file,
            caption=f"✅ Код витягнуто: `{receipt_code}`\n🔗 https://check.gov.ua/",
            parse_mode="Markdown"
        )
    except Exception as e:
      logging.error(f"Помилка API: {e}")
      input_file = BufferedInputFile(file_bytes, filename=file_name)
      await message.answer_document(
          input_file,
          caption=f"✅ Код знайдено: `{receipt_code}`",
          parse_mode="Markdown"
      )
  else:
    # Якщо код автоматично не знайшовся на фото (оскільки на скріншотах текст картинкою), 
    # просто повертаємо файл/фото назад і пишемо підказку
    input_file = BufferedInputFile(file_bytes, filename=file_name)
    await message.answer_document(
        input_file,
        caption="⚠️ Не вдалося автоматично витягнути 16-значний код із цього зображення. Але саму квитанцію повертаю тобі назад ⬆️",
        parse_mode="Markdown"
    )


# Обробка PDF-файлів
@dp.message(F.document)
async def handle_document(message: types.Message):
  file_info = await bot.get_file(message.document.file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  file_bytes = downloaded_file.read()

  temp_filename = "temp_receipt.pdf"
  with open(temp_filename, "wb") as f:
    f.write(file_bytes)

  extracted_text = ""
  try:
    with pdfplumber.open(temp_filename) as pdf:
      for page in pdf.pages:
        extracted_text += page.extract_text() or ""
  except Exception as e:
    logging.error(f"Помилка читання PDF: {e}")

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await process_and_reply(message, file_bytes, message.document.file_name, extracted_text)


# Обробка звичайних фото/скріншотів
@dp.message(F.photo)
async def handle_photo(message: types.Message):
  file_id = message.photo[-1].file_id
  file_info = await bot.get_file(file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  file_bytes = downloaded_file.read()

  # Оскільки на скріншоті з Monobank (як на твоєму) текст зображений графічно, 
  # для прямого парсингу коду краще працює PDF, але давай приймати фото і відправляти його назад
  await process_and_reply(message, file_bytes, "receipt_screenshot.jpg", extracted_text="")


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
