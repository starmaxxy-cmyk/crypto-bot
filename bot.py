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

    # Використовуємо комбінацію англійської та української мов
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
  # Виводимо весь розпізнаний текст у логи Render для зручності налаштування
  logging.info(f"--- ПОВНИЙ ТЕКСТ З КВИТАНЦІЇ ---\n{full_text}\n-----------------------------")

  clean_text_no_spaces = full_text.replace(" ", "").replace("\n", "")

  # 1. Пошук номера квитанції
  code_match = re.search(r"(P24[A-Z0-9]{10,25}D[A-Z0-9]{1,6}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text_no_spaces, re.IGNORECASE)
  if not code_match:
    doc_code_search = re.search(r"(?:квитанція|коддокумента|код)[:\-]?\s*([A-Z0-9P24\-]{10,30})", full_text, re.IGNORECASE)
    receipt_code = doc_code_search.group(1).strip() if doc_code_search else "Не знайдено"
  else:
    receipt_code = code_match.group(0).strip()

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

  # 2. Пошук Відправника (шукаємо ПІБ після слова «Відправник» або «Ім'я» у блоці відправника)
  sender_str = "Не вказано"
  sender_block = re.search(r"Відправник.*?(?=Одержувач|Отримувач|$)", full_text, re.DOTALL | re.IGNORECASE)
  if sender_block:
    name_match = re.search(r"(?:Ім'?я|ПІБ)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s]{6,40})", sender_block.group(0))
    if name_match:
      sender_str = name_match.group(1).strip()

  # 3. Пошук Отримувача та його карти/рахунку
  payee_str = "Не вказано"
  payee_block = re.search(r"(?:Одержувач|Отримувач).*?(?=Деталі|Сума|$)", full_text, re.DOTALL | re.IGNORECASE)
  if payee_block:
    block_text = payee_block.group(0)
    p_name = re.search(r"(?:Ім'?я)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s\.]{2,25})", block_text)
    p_card = re.search(r"(?:UA\d{2}\d{4,}\d*|\d{4}\s?\*{4}\s?\*{4}\s?\d{4})", block_text)
    
    name_val = p_name.group(1).strip() if p_name else ""
    card_val = p_card.group(0).strip() if p_card else ""
    if name_val or card_val:
      payee_str = f"{name_val}, {card_val}".strip(", ")

  # Якщо блоковий пошук не спрацював, шукаємо просто по рядках
  if payee_str == "Не вказано":
    alt_payee = re.search(r"(?:Одержувач|Отримувач)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s,\*\-\d]{5,50})", full_text)
    if alt_payee:
      payee_str = alt_payee.group(1).strip()

  # 4. Пошук Суми (у Monobank це зазвичай сума поруч з «Сума (грн)» або просто число з копійками)
  amount_str = "Не вказано"
  amount_match = re.search(r"Сума\s*(?:\(грн\))?[:\s]*([\d\s\.,]+)", full_text, re.IGNORECASE)
  if amount_match:
    amount_str = amount_match.group(1).replace(" ", "").strip()
  else:
    alt_amount = re.search(r"(\d{1,3}(?:\s?\d{3})*[\.,]\d{2})\s*(?:₴|грн|UAH)?", full_text, re.IGNORECASE)
    if alt_amount:
      amount_str = alt_amount.group(1).replace(" ", "").strip()

  # 5. Пошук Дати та часу операції
  date_str = "Не вказано"
  date_match = re.search(r"(?:Дата і час операції|Дата)[:\s]*(\d{2}[\-\.]\d{2}[\-\.]\d{4}\s*\d{2}:\d{2})", full_text, re.IGNORECASE)
  if not date_match:
    date_match = re.search(r"(\d{2}[\-\.]\d{2}[\-\.]\d{4}\s*\d{2}:\d{2})", full_text)
  if date_match:
    date_str = date_match.group(1).strip()

  # 6. Призначення платежу
  purpose_str = "Переказ коштів"
  purpose_match = re.search(r"Призначення платежу[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z0-9\s\.,\-]{3,60})", full_text, re.IGNORECASE)
  if purpose_match:
    purpose_str = purpose_match.group(1).strip()

  # Формуємо посилання для перевірки
  if receipt_code.startswith("P24"):
    service_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
    service_name = "ПриватБанку"
  else:
    service_url = f"https://check.gov.ua/{receipt_code}"
    service_name = "державному реєстрі check.gov.ua"

  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=f"📥 Відкрити в {service_name}", url=service_url)]
  ])

  await message.answer(
      f"📋 **Детальний звіт по квитанції Мій Господин:**\n\n"
      f"🔢 **Номер квитанції:** `{receipt_code}`\n"
      f"👤 **Відправник:** {sender_str}\n"
      f"🏢 **Отримувач / Картка:** {payee_str}\n"
      f"💵 **Сума:** {amount_str} грн\n"
      f"📅 **Дата:** {date_str}\n"
      f"📝 **Призначення:** {purpose_str}\n\n"
      f"Натисніть кнопку нижче для переходу до офіційного джерела:",
      parse_mode="Markdown",
      reply_markup=keyboard
  )


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
