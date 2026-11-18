import asyncio
import logging
import os
import re
import pdfplumber
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

TOKEN = "9033168856:AAGAP6KKPAI7Sb5Tswxmdv4dBBuNFMqISzA"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer(
      "Привіт! 👋 Надішли мені квитанцію **як файл (PDF)** з Монобанку чи Привату, "
      "і я перевірю її справжність у державних базах."
  )


@dp.message(F.document)
async def handle_document(message: types.Message):
  file_name = message.document.file_name.lower()
  if not file_name.endswith(".pdf"):
    await message.answer("⚠️ Будь ласка, надішли квитанцію у форматі **PDF**.")
    return

  await message.answer("⏳ Зчитую квитанцію та шукаю код...")

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

  # Шукаємо 16-значний код квитанції
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)

  if code_match:
    receipt_code = code_match.group(0)
    await message.answer(
        f"🔍 Знайдено код квитанції: `{receipt_code}`\n"
        f"🌐 Перевіряю через державний реєстр check.gov.ua...",
        parse_mode="Markdown"
    )

    # Робимо перевірку через публічний API check.gov.ua (або форму перевірки)
    try:
      # Запит до сервісу check.gov.ua (база для Моно, А-Банку та інших)
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
              f"❌ **Квитанцію з кодом `{receipt_code}` не знайдено в базі.**\n"
              f"Можливо, платіж ще не дійшов або квитанція недійсна.",
              parse_mode="Markdown"
          )
      else:
        # Якщо прямий API відповів інакше, даємо базову лінку для ручної перевірки
        await message.answer(
            f"✅ Код витягнуто: `{receipt_code}`\n\n"
            f"🔗 Можеш перевірити вручну на офіційних сайтах:\n"
            f"• [Check.gov.ua](https://check.gov.ua/)\n"
            f"• [ПриватБанк Перевірка](https://privatbank.ua/check)",
            parse_mode="Markdown"
        )
    except Exception as api_err:
      logging.error(f"Помилка запиту до API: {api_err}")
      await message.answer(
          f"✅ Код успішно знайдено: `{receipt_code}`\n\n"
          f"🔗 Перевір квитанцію за посиланням: https://check.gov.ua/",
          parse_mode="Markdown"
      )

  else:
    await message.answer(
        "⚠️ Не вдалося знайти код квитанції у цьому файлі. "
        "Переконайся, що це оригінальний PDF-файл квитанції з банку."
    )


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer(
      "💡 Для найшвидшої та найточнішої перевірки надсилай квитанцію **як файл (PDF)**."
  )


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
