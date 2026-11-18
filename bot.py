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

TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer("Надішли мені квитанцію Мій Господин")


def extract_full_text(image_path: str) -> str:
  try:
    img = Image.open(image_path)
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=2)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)

    text = pytesseract.image_to_string(img, lang='eng+ukr')
    return text
  except Exception as e:
    logging.error(f"Помилка OCR: {e}")
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

    await process_receipt_data(message, extracted_text)
  except Exception as e:
    logging.error(f"Помилка PDF: {e}")
    await message.answer("❌ Сталася помилка при читанні PDF документу.")


@dp.message(F.photo)
async def handle_photo(message: types.Message):
  await message.answer("⏳ Зчитую реквізити квитанції Мій Господин...")
  try:
    file_id = message.photo[-1].file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = "temp_photo.jpg"
    with open(temp_filename, "wb") as f:
      f.write(downloaded_file.read())

    full_text = extract_full_text(temp_filename)

    if os.path.exists(temp_filename):
      os.remove(temp_filename)

    await process_receipt_data(message, full_text)
  except Exception as e:
    logging.error(f"Помилка фото: {e}")
    await message.answer("❌ Сталася помилка при скануванні зображення.")


async def process_receipt_data(message: types.Message, full_text: str):
  # Логуємо текст у консоль Render, щоб бачити, що читає Приват
  logging.info(f"РОЗПІЗНАНИЙ ТЕКСТ:\n{full_text}")

  clean_text_no_spaces = full_text.replace(" ", "").replace("\n", "")

  # 1. Пошук номера квитанції (Monobank + PrivatBank коди типу P24... або стандартні шматочки)
  code_match = re.search(r"(P24[A-Z0-9]{10,30}D[A-Z0-9]{1,10}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text_no_spaces, re.IGNORECASE)
  if not code_match:
    # Шукаємо спеціально для ПриватБанку поблизу слів "Код документа"
    privat_code_search = re.search(r"(?:Код документа|Коддок\.|Призначення)[:\-]?\s*([A-Z0-9P24\-]{10,35})", full_text, re.IGNORECASE)
    if privat_code_search:
      receipt_code = privat_code_search.group(1).strip()
    else:
      doc_code_search = re.search(r"(?:квитанція|код)[:\-]?\s*([A-Z0-9P24\-]{10,30})", full_text, re.IGNORECASE)
      receipt_code = doc_code_search.group(1).strip() if doc_code_search else "Не знайдено"
  else:
    receipt_code = code_match.group(0).strip()

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

  # 2. Витягування Суми (універсально для обох банків)
  amount_str = "Не вказано"
  amount_matches = re.findall(r"(?:Сума\s*(?:\(грн\))?[:\s]*)?(\d{1,3}(?:\s?\d{3})*[\.,]\d{2})", full_text, re.IGNORECASE)
  if amount_matches:
    cleaned_amounts = [float(amt.replace(" ", "").replace(",", ".")) for amt in amount_matches if float(amt.replace(" ", "").replace(",", ".")) > 1.0]
    if cleaned_amounts:
      max_amt = max(cleaned_amounts)
      amount_str = f"{max_amt:.2f}"
    else:
      amount_str = amount_matches[0].replace(" ", "")

  # 3. Дата та час операції (з урахуванням форматів Привату та Моно)
  date_str = "Не вказано"
  date_match = re.search(r"(\d{2}[\-\.]\d{2}[\-\.]\d{4}\s*\d{2}:\d{2})", full_text)
  if date_match:
    date_str = date_match.group(1).strip()

  # Формуємо посилання (якщо починається з P24 — кидаємо на Приват, інакше на check.gov.ua)
  if receipt_code.startswith("P24") or "P24" in full_text.upper():
    service_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
    service_name = "ПриватБанку"
  else:
    service_url = f"https://check.gov.ua/{receipt_code}"
    service_name = "державному реєстрі check.gov.ua"

  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=f"📥 Відкрити в {service_name}", url=service_url)]
  ])

  # Фінальний текст повідомлення
  response_text = (
      f"✅ **Номер квитанції:** `{receipt_code}`\n\n"
      f"💵 **Сума:** {amount_str} грн\n"
      f"📅 **Дата:** {date_str}\n\n"
      f"⚠️ **Квитанція не перевірена, будь ласка, перевірте її самі, бо я тупий!!!**\n\n"
      f"Натисніть кнопку нижче для переходу до офіційного джерела:"
  )

  await message.answer(response_text, parse_mode="Markdown", reply_markup=keyboard)


async def main():
  await asyncio.sleep(5)
  try:
    await bot.delete_webhook(drop_pending_updates=True)
  except Exception:
    pass
    
  logging.info("Бот успішно запущено!")
  await dp.start_polling(bot, handle_signals=False, close_bot_session=True)


if __name__ == "__main__":
  asyncio.run(main())
