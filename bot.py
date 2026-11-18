import asyncio
import logging
import os
import re
import pdfplumber
import requests
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


# Основна логіка перевірки та виведення посилання на файл квитанції
async def verify_and_send_result(message: types.Message, text: str):
  # Шукаємо 16-значний код квитанції
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", text)

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
          
          # Офіційні посилання на перевірку та скачування файлу в системі check.gov.ua
          pdf_download_url = f"https://check.gov.ua/receipts/{receipt_code}"
          
          # Створюємо зручну кнопку для скачування самого файлу квитанції
          keyboard = InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📥 Завантажити файл квитанції (PDF)", url=pdf_download_url)]
          ])

          await message.answer(
              f"✅ **Квитанція успішно перевірена та дійсна!**\n\n"
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
              f"❌ **Увага Мій Господин!** Квитанцію з кодом `{receipt_code}` не знайдено в державній базі.",
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

  await verify_and_send_result(message, extracted_text)


# Обробка звичайних фото (якщо текст вдається витягнути або як підказка)
@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
