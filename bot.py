import asyncio
import logging
import os
import re
import pdfplumber
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import requests

TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


def extract_text_from_photo(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=5)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)

    text = pytesseract.image_to_string(img, lang='eng+ukr')
    return text
  except Exception as e:
    logging.error(f"Помилка обробки фото: {e}")
    return ""


@dp.message(F.document)
async def handle_document(message: types.Message):
  await message.answer("⏳ Читаю PDF-квитанцію Мій Господин...")
  try:
    file_info = await bot.get_file(message.document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_receipt.pdf"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    extracted_text = ""
    with pdfplumber.open(temp_filename) as pdf:
      for page in pdf.pages:
        extracted_text += page.extract_text() or ""

    if os.path.exists(temp_filename):
      os.remove(temp_filename)

    await process_and_verify(message, extracted_text)
  except Exception as e:
    logging.error(f"Помилка PDF: {e}")
    await message.answer("❌ Сталася помилка при читанні PDF документу.")


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Сканую та роблю запит до держреєстру Мій Господин...")
  try:
    file_id = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_photo.jpg"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    extracted_text = extract_text_from_photo(temp_filename)

    if os.path.exists(temp_filename):
      os.remove(temp_filename)

    await process_and_verify(message, extracted_text)
  except Exception as e:
    logging.error(f"Помилка фото: {e}")
    await message.answer("❌ Сталася помилка при скануванні зображення.")


async def process_and_verify(message: types.Message, extracted_text: str):
  clean_text = extracted_text.replace(" ", "").replace("\n", "")

  # Шукаємо код ПриватБанку або стандартний код квитанції
  code_match = re.search(r"(P24[A-Z0-9]{15,20}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text, re.IGNORECASE)

  if not code_match:
    doc_code_search = re.search(r"(?:коддокумента|код)[:\-]?([A-Z0-9P24\-]{10,25})", clean_text, re.IGNORECASE)
    if doc_code_search:
      receipt_code = doc_code_search.group(1).strip()
    else:
      receipt_code = None
  else:
    receipt_code = code_match.group(0).strip()

  if not receipt_code:
    await message.answer("❌ Не вдалося знайти жодного коду квитанції на цьому зображенні Мій Господин.")
    return

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)
  await message.answer(f"🔍 Знайдено код: `{receipt_code}`. Роблю запит на перевірку...", parse_mode="Markdown")

  # 1. СПЕЦІАЛЬНА ПЕРЕВІРКА ДЛЯ ПРИВАТБАНКУ (P24...)
  if receipt_code.startswith("P24"):
    privat_api_url = f"https://www.privatbank.ua/api/check-receipt?paymentNumber={receipt_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
      response = requests.get(privat_url, headers=headers, timeout=5)
      # Якщо ПриватБанк повертає успішну відповідь або документ існує в їхній системі
      if response.status_code == 200:
        privat_url_page = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Відкрити оригінал в ПриватБанку", url=privat_url_page)]
        ])
        await message.answer(
            f"✅ **Квитанція успішно пройшла перевірку в банку Мій Господин!**\n\n"
            f"🔢 **Код документа:** `{receipt_code}`\n"
            f"🛡 **Статус:** Підтверджено офіційним сервером ПриватБанку.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
      else:
        await message.answer(
            f"❌ **УВАГА! Підробка або недійсний документ!**\n\n"
            f"Код `{receipt_code}` не пройшов верифікацію на сервері ПриватБанку.",
            parse_mode="Markdown"
        )
    except Exception:
      # Запасний варіант, якщо сервер банку обмежив з'єднання
      privat_url_page = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
      keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Перевірити вручну", url=privat_url_page)]])
      await message.answer(f"⚠️ Не вдалося отримати пряму відповідь від сервера ПриватБанку для коду `{receipt_code}`.", parse_mode="Markdown", reply_markup=keyboard)
    return

  # 2. ПЕРЕВІРКА ДЛЯ MONO, А-БАНКУ ТА ІНШИХ (через check.gov.ua)
  api_url = f"https://check.gov.ua/api/receipts/{receipt_code}"
  headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Accept": "application/json"
  }

  try:
    response = requests.get(api_url, headers=headers, timeout=7)
    
    if response.status_code == 200:
      data = response.json()
      # Перевіряємо, чи сервер повернув реальні дані квитанції (наявність ID або суми)
      if data and (data.get("id") or data.get("amount") or data.get("status") == "SUCCESS"):
        sum_uah = data.get("amount", "Не вказано")
        payer = data.get("payerName", "Не вказано")
        payee = data.get("payeeName", "Не вказано")
        payment_date = data.get("paymentDate", "Не вказано")

        check_url = f"https://check.gov.ua/{receipt_code}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Відкрити квитанцію в держреєстрі", url=check_url)]
        ])

        await message.answer(
            f"✅ **Квитанція офіційно підтверджена держреєстром Мій Господин!**\n\n"
            f"💵 **Сума:** {sum_uah} грн\n"
            f"👤 **Відправник:** {payer}\n"
            f"🏢 **Отримувач:** {payee}\n"
            f"📅 **Дата:** {payment_date}\n"
            f"🔢 **Код:** `{receipt_code}`\n"
            f"🛡 **Статус:** 100% Оригінал (підтверджено сервером check.gov.ua)",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
      else:
        # Код знайшли, але сервер каже, що документа з таким кодом не існує в базі (мальовка/фейк)
        await message.answer(
            f"❌ **УВАГА! Ця квитанція НЕ ЗНАЙДЕНА в державній базі!**\n\n"
            f"Код `{receipt_code}`, витягнутий із зображення, є підробленим або не зареєстрований у системі check.gov.ua!",
            parse_mode="Markdown"
        )
    else:
      # Якщо сервер повернув помилку (наприклад 404 - документ не знайдено)
      await message.answer(
          f"❌ **Фейкова або недійсна квитанція!**\n\n"
          f"Державний сервер відхилив запит за кодом `{receipt_code}` (Документ відсутній у базі даних).",
          parse_mode="Markdown"
      )
  except Exception as e:
    logging.error(f"Помилка запиту до API: {e}")
    await message.answer(f"⚠️ Помилка зв'язку з державним сервером перевірки. Код з картинки: `{receipt_code}`", parse_mode="Markdown")


async def main():
  await asyncio.sleep(3)
  try:
    await bot.delete_webhook(drop_pending_updates=True)
  except Exception:
    pass
    
  await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
  asyncio.run(main())
