import asyncio
import logging
import os
import re
import pdfplumber
import requests
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = "9198206541:AAFFn3yUbAkKE6J311_8Iw1sI-4FbuSFN0g"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


# Покращена функція розпізнавання тексту зі скріншотів з підвищенням чіткості
def extract_text_from_image(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    # Збільшуємо розмір зображення в 2 рази для кращого розпізнавання дрібного тексту
    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
    # Переводимо в чорно-біле для кращого контрасту
    img = img.convert('L')
    # Збільшуємо контраст
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # Зчитуємо текст
    text = pytesseract.image_to_string(img, lang='ukr+eng')
    return text
  except Exception as e:
    logging.error(f"Помилка OCR обробки: {e}")
    return ""


async def process_and_reply(message: types.Message, extracted_text: str):
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
          
          # ВИПРАВЛЕННЯ 404: Офіційне робоче посилання на перевірку/документ в системі check.gov.ua
          correct_receipt_url = f"https://check.gov.ua/{receipt_code}"
          
          keyboard = InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📥 Відкрити квитанцію в базі", url=correct_receipt_url)]
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
            f"📥 Офіційне посилання: https://check.gov.ua/{receipt_code}",
            parse_mode="Markdown"
        )
    except Exception as e:
      logging.error(f"Помилка API: {e}")
      await message.answer(
          f"✅ Код знайдено: `{receipt_code}`\n\n"
          f"📥 Офіційне посилання: https://check.gov.ua/{receipt_code}",
            parse_mode="Markdown"
      )
  else:
    await message.answer("Не вдалося розпізнати код квитанції Мій Господин. Надішли чіткіший скріншот або файл PDF.")


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

  await process_and_reply(message, extracted_text)


# Обробка скріншотів з покращеним зчитуванням дрібного тексту
@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Зчитую дрібний текст зі скріншота Мій Господин...")
  
  file_id = message.photo[-1].file_id
  file_info = await bot.get_file(file_id)
  downloaded_file = await bot.download_file(file_info.file_path)
  
  temp_filename = "temp_photo.jpg"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  # Використовуємо функцію з підвищенням контрасту та масштабуванням для дрібного шрифту
  extracted_text = extract_text_from_image(temp_filename)

  if os.path.exists(temp_filename):
    os.remove(temp_filename)

  await process_and_reply(message, extracted_text)


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
