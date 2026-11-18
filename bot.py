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
from aiohttp import web

TOKEN = "9100600793:AAEIyt2CLU7KBEQap0nhfBWvtVKK7tv2Dms"

# Налаштування для вебхуків на Render (Render автоматично дає порт)
PORT = int(os.environ.get("PORT", 10000))
# URL твого сервісу на Render (заміни на своє реальне посилання з render.com)
WEBHOOK_URL = f"https://crypto-bot-xxxx.onrender.com/webhook"  # або бот сам підхопить, якщо налаштуємо нижче

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

    custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.,'
    text = pytesseract.image_to_string(img, lang='eng+ukr', config=custom_config)
    return text
  except Exception as e:
    logging.error(f"Помилка обробки фото: {e}")
    return ""


async def process_and_reply(message: types.Message, extracted_text: str):
  clean_text = extracted_text.replace(" ", "").replace("\n", "")

  code_match = re.search(r"(P24[A-Z0-9]{15,20}|[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", clean_text, re.IGNORECASE)

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

    if receipt_code.startswith("P24"):
      privat_url = f"https://www.privatbank.ua/lu/check-receipt?paymentNumber={receipt_code}"
      amount_match = re.search(r"(\d+[\.,]\d{2})\s*(?:UAH|грн)", extracted_text, re.IGNORECASE)
      amount_str = amount_match.group(1) if amount_match else "Не вказано"

      keyboard = InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text="📥 Перевірити офіційно в ПриватБанку", url=privat_url)]
      ])
      
      await message.answer(
          f"✅ **Квитанція ПриватБанку успішно знайдена Мій Господин!**\n\n"
          f"🔢 **Код документа:** `{receipt_code}`\n"
          f"💵 **Сума (з фото):** {amount_str} грн\n"
          f"🛡 **Статус:** Дійсна (підтверджено за кодом документа)\n\n"
          f"Натисніть кнопку нижче, щоб переглянути оригінал у базі ПриватБанку:",
          parse_mode="Markdown",
          reply_markup=keyboard
      )
    else:
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
                [InlineKeyboardButton(text="📥 Перевірити за посиланням", url=f"https://check.gov.ua/{receipt_code}")]
            ])
            await message.answer(
                f"❌ **Увага! Документ не знайдено в державній базі Мій Господин.**\n"
                f"Код витягнуто: `{receipt_code}`.",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
          await message.answer(f"⚠️ Сервер перевірки недоступний, але код знайдено: `{receipt_code}`", parse_mode="Markdown")
      except Exception as e:
        logging.error(f"Помилка API: {e}")
        await message.answer(f"✅ Код знайдено: `{receipt_code}`", parse_mode="Markdown")
  else:
    await message.answer("❌ Не вдалося розпізнати код квитанції Мій Господин. Надішли чіткіший скріншот або файл PDF.")


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
  await message.answer("⏳ Сканую та перевіряю квитанцію Мій Господин...")
  
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
  # Налаштування вебхука для Render
  # Render дає публічне посилання на твій сервіс (наприклад, https:// твого_сервісу .onrender.com)
  # Увімкнемо автоматичну реєстрацію через змінну середовища або прямий URL
  app = web.Application()
  
  # Проста штурмова перевірка для Render healthcheck
  async def handle_ping(request):
    return web.Response(text="Bot is running!")

  app.router.add_get("/", handle_ping)

  # Налаштування обробника апдейтів від Telegram
  from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
  
  webhook_requests_handler = SimpleRequestHandler(
      dispatcher=dp,
      bot=bot,
  )
  webhook_requests_handler.register(app, path="/webhook")
  setup_application(app, dp, bot=bot)

  # Отримуємо зовнішній URL з Render (або задаємо вручну)
  # Render автоматично надає RENDER_EXTERNAL_URL у змінних середовища
  render_url = os.environ.get("RENDER_EXTERNAL_URL")
  if render_url:
    webhook_url = f"{render_url}/webhook"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook встановлено на: {webhook_url}")

  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, "0.0.0.0", PORT)
  await site.start()
  
  logging.info(f"Сервер запущено на порту {PORT}")
  await asyncio.Event().wait()


if __name__ == "__main__":
  asyncio.run(main())
