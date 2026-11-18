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
  clean_text_no_spaces = full_text.replace(" ", "").replace("\n", "")

  # 1. Номер квитанції
  code_match = re.search(r"(P24[A-Z0-9]{10,25}D[A-Z0-9]{1,6}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text_no_spaces, re.IGNORECASE)
  if not code_match:
    doc_code_search = re.search(r"(?:квитанція|коддокумента|код)[:\-]?\s*([A-Z0-9P24\-]{10,30})", full_text, re.IGNORECASE)
    receipt_code = doc_code_search.group(1).strip() if doc_code_search else "Не знайдено"
  else:
    receipt_code = code_match.group(0).strip()

  receipt_code = re.sub(r'[^A-Z0-9\-]', '', receipt_code)

  # 2. Відправник
  sender_str = "Не вказано"
  sender_section = re.search(r"Відправник(.*?)(?=Одержувач|Отримувач|$)", full_text, re.DOTALL | re.IGNORECASE)
  if sender_section:
    s_text = sender_section.group(1)
    s_match = re.search(r"(?:Ім'?я|ПІБ)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s]{6,45})", s_text)
    if s_match:
      sender_str = s_match.group(1).strip()

  # 3. Одержувач та картка
  payee_str = "Не вказано"
  payee_section = re.search(r"(?:Одержувач|Отримувач)(.*?)(?=Деталі|Сума|$)", full_text, re.DOTALL | re.IGNORECASE)
  if payee_section:
    p_text = payee_section.group(1)
    p_name = re.search(r"(?:Ім'?я)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s\.]{2,30})", p_text)
    p_card = re.search(r"(?:UA\d{2}\d{4,}\d*|\d{4}\s?\*{4}\s?\*{4}\s?\d{4})", p_text)
    
    n_val = p_name.group(1).strip() if p_name else ""
    c_val = p_card.group(0).strip() if p_card else ""
    if n_val or c_val:
      payee_str = f"{n_val}, {c_val}".strip(", ")

  if payee_str == "Не вказано":
    alt_p = re.search(r"(?:Одержувач|Отримувач)[:\s]*([А-Яа-яІіЇїЄєҐґA-Za-z\s,\*\-\d]{5,50})", full_text)
    if alt_p:
      payee_str = alt_p.group(1).strip()

  # 4. Виправлене витягування Суми (щоб не чіпляти дрібні комісії типу 0.00 і брати повне число)
  amount_str = "Не вказано"
  # Шукаємо суму літерами або ціле число з деталей транзакції
  det_section = re.search(r"Деталі транзакції(.*?)(?:Операційний|$)", full_text, re.DOTALL | re.IGNORECASE)
  search_scope = det_section.group(1) if det_section else full_text

  # Шукаємо великі суми (наприклад, 20000.00 або суми з пробілами)
  amount_matches = re.findall(r"(?:Сума\s*(?:\(грн\))?[:\s]*)?(\d{1,3}(?:\s?\d{3})*[\.,]\d{2})", search_scope, re.IGNORECASE)
  if amount_matches:
    # Беремо найбільшу знайдену суму у блоці деталей (щоб відкинути копійки/комісії на кшталт 0.00)
    cleaned_amounts = [float(amt.replace(" ", "").replace(",", ".")) for amt in amount_matches if float(amt.replace(" ", "").replace(",", ".")) > 1.0]
    if cleaned_amounts:
      max_amt = max(cleaned_amounts)
      amount_str = f"{max_amt:.2f}"
    else:
      amount_str = amount_matches[0].replace(" ", "")

  # 5. Дата та час операції
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

  # Формуємо посилання
  if receipt_code.startswith("P24"):
    service_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
    service_name = "ПриватБанку"
  else:
    service_url = f"https://check.gov.ua/{receipt_code}"
    service_name = "державному реєстрі check.gov.ua"

  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=f"📥 Відкрити в {service_name}", url=service_url)]
  ])

  # Фінальний формат повідомлення
  await message.answer(
      f"✅ **Номер квитанції:** `{receipt_code}`\n\n"
      f"👤 **Відправник:** {sender_str}\n"
      f"🏢 **Отримувач / Картка:** {payee_str}\n"
      f"💵 **Сума:** {amount_str} грн\n"
      f"📅 **Дата:** {date_str}\n"
      f"📝 **Призначення:** {purpose_str}\n\n"
      f"Натисніть кнопку нижче для переходу до офіційного джерела:",
      parse_mode="Markdown",
      reply_markup
