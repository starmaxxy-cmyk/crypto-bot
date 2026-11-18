import asyncio
import logging
import os
import re
import pdfplumber
import requests
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = "9100600793:AAEIyt2CLU7KBEQap0nhfBWvtVKK7tv2Dms"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


# Потужне покращення для фото з екранів (знищує бліки та витягує текст)
def extract_text_from_photo(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    # Збільшуємо роздільну здатність
    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
    # Робимо чорно-білим
    img = img.convert('L')
    # Автоконтраст для виділення тексту на фоні бліків
    img = ImageOps.autocontrast(img, cutoff=2)
    
    # Використовуємо OCR з конфігурацією для блоків тексту (--psm 6)
    custom_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(img, lang='ukr+eng', config=custom_config)
    return text
  except Exception as e:
    logging.error(f"Помилка обробки фото: {e}")
    return ""


async def process_and_reply(message: types.Message, extracted_text: str):
  # Шукаємо код квитанції: або стандартний (4 групи по 4 символи), або код ПриватБанку (формат P24...)
  code_match = re.search(r"([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}|P24[A-Z0-9]{15,20})", extracted_text)

  # Якщо регулярний вираз не зловив автоматично, пробуємо витягти по ключовому слову "Код документа"
  if not code_match:
    doc_code_search = re.search(r"(?:код\s*документа|код)\s*[:\-]?\s*([A-Z0-9P24\-]{10,25})", extracted_text, re.IGNORECASE)
    if doc_code_search:
      receipt_code = doc_code_search.group(1).replace(" ", "").strip()
    else:
      receipt_code = None
  else:
    receipt_code = code_match.group(0).replace(" ", "").strip()

  if receipt_code:
    await message.answer(
        f"🔍 Знайдено код квитанції: `{receipt_code}`\n"
        f"🌐 Перевіряю через державну базу...",
        parse_mode="Markdown"
    )

    try:
      # Деякі коди ПриватБанку пробиваються через загальний API або через пряме посилання
      check_url = f"https://check.gov.ua/api/receipts/{receipt_code}"
      response = requests.get(check_url, timeout=10)

      if response.status_code == 200:
        data = response.json()
        if data and "id" in data:
          sum_uah = data.get("amount", "Не вказано")
          payer = data.get("payerName", "Не вказано")
          payee = data.get("payeeName", "Не вказано")
          payment_date = data.get("paymentDate", "Не вказано")
          
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
          # Якщо в API немає, але код є — даємо пряме посилання на перевірку в держсистемі
          keyboard = InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📥 Перевірити за кодом", url=f"https://check.gov.ua/{receipt_code}")]
          ])
          await message.answer(
              f"✅ Код документа витягнуто: `{receipt_code}`\n"
              f"Мій Господин, перевірте документ за посиланням нижче ⬇️",
              parse_mode="Markdown",
              reply_markup=keyboard
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
    logging.error(f"По
