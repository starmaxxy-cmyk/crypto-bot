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
      "Привіт! 👋 Надішли мені квитанцію **у форматі PDF**, "
      "і я перевірю її та надішлю її тобі назад файлом із підтвердженням."
  )


@dp.message(F.document)
async def handle_document(message: types.Message):
  file_name = message.document.file_name.lower()
  if not file_name.endswith(".pdf"):
    await message.answer("⚠️ Будь ласка, надішли квитанцію у форматі PDF.")
    return

  await message.answer("⏳ Зчитую та перевіряю PDF...")
  
  # Завантажуємо файл
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

  # Шукаємо 16-значний код квитанції
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)

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
          
          # Надсилаємо оригінальний файл назад користувачу
          input_file = BufferedInputFile(file_bytes, filename=message.document.file_name)
          await message.answer_document(
              input_file,
              caption=(
                  f"✅ **Квитанція дійсна та перевірена!**\n"
                  f"💵 **Сума:** {sum_uah} грн\n"
                  f"👤 **Відправник:** {payer}\n"
                  f"🔢 **Код:** `{receipt_code}`\n\n"
                  f"📄 Ось оригінальний файл квитанції ⬆️"
              ),
              parse_mode="Markdown"
          )
        else:
          await message.answer(
              f"❌ **Увага!** Код `{receipt_code}` є в PDF, але квитанцію **не знайдено** в державній базі check.gov.ua.",
              parse_mode="Markdown"
          )
      else:
        # Якщо API відповіло інакше, все одно повертаємо файл із знайденим кодом
        input_file = BufferedInputFile(file_bytes, filename=message.document.file_name)
        await message.answer_document(
            input_file,
            caption=f"✅ Код витягнуто: `{receipt_code}`\n🔗 Перевірка: https://check.gov.ua/",
            parse_mode="Markdown"
        )
    except Exception as e:
      logging.error(f"Помилка API: {e}")
      input_file = BufferedInputFile(file_bytes, filename=message.document.file_name)
      await message.answer_document(
          input_file,
          caption=f"✅ Код знайдено: `{receipt_code}`",
          parse_mode="Markdown"
      )
  else:
    await message.answer(
        "⚠️ Не вдалося знайти 16-значний код квитанції у цьому PDF-файлі."
    )


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer(
      "💡 Будь ласка, надсилай квитанцію **як документ (PDF)**, щоб я міг її перевірити та повернути файлом."
  )


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
