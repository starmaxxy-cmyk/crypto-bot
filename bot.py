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
      "Надішли мені квитанцію Мій Господин"
  )


# Універсальна перевірка та відправка файлу назад
async def verify_and_return_file(message: types.Message, file_bytes: bytes, file_name: str, extracted_text: str):
  # Шукаємо 16-значний код квитанції у форматі check.gov.ua
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
          
          input_file = BufferedInputFile(file_bytes, filename=file_name)
          await message.answer_document(
              input_file,
              caption=(
                  f"✅ **Квитанція успішно перевірена!**\n"
                  f"💵 **Сума:** {sum_uah} грн\n"
                  f"👤 **Відправник:** {payer}\n"
                  f"🔢 **Код:** `{receipt_code}`\n\n"
                  f"📄 Ось твоя квитанція Мій Господин ⬆️"
              ),
              parse_mode="Markdown"
          )
        else:
          input_file = BufferedInputFile(file_bytes, filename=file_name)
          await message.answer_document(
              input_file,
              caption=f"⚠️ Код `{receipt_code}` знайдено, але в державній базі запис відсутній.",
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
    # Якщо код не витягнувся автоматично (наприклад, із картинки), 
    # все одно повертаємо файл/фото назад Мій Господин
    input_file = BufferedInputFile(file_bytes, filename=file_name)
    await message.answer_document(
        input_file,
        caption="Надішли мені квитанцію Мій Господин",
        parse_mode="Markdown"
    )


# Обробка PDF-документів
@dp.message(F.document)
async def handle_document(message: types.Message):
  file_info = await bot.get_file(message.document.file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  file_bytes = downloaded_file.read()

  temp_filename = "temp_receipt.pdf"
  with open(temp
