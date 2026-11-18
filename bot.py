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

TOKEN = "9100600793:AAEiyt2CLU7KBEQap0nhfBwvtVKK7tv2Dms"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


def extract_text_from_photo(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    # Збільшуємо у 3 рази для максимальної деталізації дрібних цифр
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=5)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)

    # Забороняємо сканеру плутати символи (тільки латиниця, цифри та дефіс)
    custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
    text = pytesseract.image_to_string(img, lang='eng', config=custom_config)
    return text
  except Exception as e:
    logging.error(f"Помилка обробки складного фото: {e}")
    return ""


async def process_and_reply(message: types.Message, extracted_text: str):
  clean_text = extracted_text.replace(" ", "").replace("\n", "")

  # Шукаємо код ПриватБанку (формат P24...) або стандартний код
  code_match = re.search(r"(P24[A-Z0-9]{15,20}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text)

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

    # Визначаємо, чи це ПриватБанк (починається з P24)
    if receipt_code.startswith("P24"):
      # Для ПриватБанку робимо пряму перевірку через їхнє посилання на документ
      privat_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
      keyboard = InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text="📥 Відкрити квитанцію ПриватБанку", url=privat_url)]
      ])
      await message.answer(
          f"✅ **Розпізнано квитанцію ПриватБанку Мій Господин!**\n\n"
          f"🔢 **Код документа:** `{receipt_code}`\n\n"
          f"Натисніть кнопку нижче для перевірки на офіційному сайті ПриватБанку:",
          parse_mode="Markdown",
          reply_markup=keyboard
      )
    else:
      # Для Mono, А-Банку та інших — використовуємо державний API check.gov.ua
      await message.answer(
          f"🔍 Знайдено код: `{receipt_code}`\n"
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
          keyboard = InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📥 Відкрити посилання", url=f"https://check.gov.ua/{receipt_code}")]
          ])
          await message.answer(
              f"✅ Код витягнуто: `{receipt_code}`\n\n"
              f"📥 Офіційне посилання:",
              parse_mode="Markdown",
              reply_markup=keyboard
          )
      except Exception as e:
        logging.error(f"Помилка API: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Офіційне посилання", url=f"https://check.gov.ua/{receipt_code}")]
        ])
        await message.answer(
            f"✅ Код знайдено: `{receipt_code}`\n\n"
            f"📥 Перевірте документ за посиланням нижче:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
  else:
    await message.answer("Не вдалося розпізнати код квитанції Мій Господин. Надішли чіткіший скріншот або файл PDF.")


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
  await message.answer("⏳ Сканую та обробляю квитанцію Мій Господин...")
  
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
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
