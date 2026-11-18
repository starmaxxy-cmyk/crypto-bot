import asyncio
import logging
import os
import re
import sys
import pdfplumber
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

# Твій токен бота від BotFather
TOKEN = "9033168856:AAGAP6KKPAI7Sb5Tswxmdv4dBBuNFMqISzA"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  await message.answer(
      "Привіт! 👋 Надішли мені квитанцію ( PDF-файлом або скріншотом/фото ), і я"
      " спробую знайти її код та перевірити через державні бази."
  )


@dp.message(F.document | F.photo)
async def handle_receipt(message: types.Message):
  await message.answer(
      "⏳ Отримую файл та зчитую текст з квитанції, зачекай секунду..."
  )

  file_id = None
  file_ext = ""

  if message.document:
    file_id = message.document.file_id
    file_name = message.document.file_name.lower()
    file_ext = os.path.splitext(file_name)[1]
  elif message.photo:
    # Беремо найбільше фото
    file_id = message.photo[-1].file_id
    file_ext = ".jpg"

  # Завантажуємо файл з сервери Telegram
  file_info = await bot.get_file(file_id)
  file_path_tg = file_info.file_path
  downloaded_file = await bot.download_file(file_path_tg)

  temp_filename = f"temp_file{file_ext}"
  with open(temp_filename, "wb") as f:
    f.write(downloaded_file.read())

  extracted_text = ""

  # Витягуємо текст залежно від типу файлу
  try:
    if file_ext == ".pdf":
      with pdfplumber.open(temp_filename) as pdf:
        for page in pdf.pages:
          extracted_text += page.extract_text() or ""
    else:
      # Для простоти поки що повідомимо, якщо це фото
      extracted_text = "Скріншот"
  except Exception as e:
    logging.error(f"Помилка читання файлу: {e}")

  # Шукаємо схожі коди квитанцій (наприклад, 16-значні коди або схожі формати)
  # Монобанк/А-Банк/Приват часто мають літерно-цифрові коди
  code_match = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", extracted_text)
  
  if code_match:
    receipt_code = code_match.group(0)
    await message.answer(
        f"🔍 Знайдено код квитанції: `{receipt_code}`\nПеревіряю в базі...",
        parse_mode="Markdown"
    )
    # Тут бот може віддати статус
    await message.answer(f"✅ Код успішно витягнуто! (Тут буде інтеграція сайту перевірки).")
  else:
    await message.answer(
        "⚠️ Не вдалося автоматично знайти 16-значний код квитанції у цьому"
        " файлі. Спробуй надіслати інший формат або PDF."
    )

  # Видаляємо тимчасовий файл
  if os.path.exists(temp_filename):
    os.remove(temp_filename)


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
