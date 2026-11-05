import asyncio
from datetime import datetime
import json
import os
import re
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_FILE = "deals.json"

# Оновлений список клієнтів
CLIENTS_LIST = [
    "Карта Лічка",
    "LarMaks",
    "Коваленко",
    "Саша Фоп",
    "Yrii",
    "SwapLine",
    "Crypcie",
    "Финики",
    "TradX",
    "Посилання",
    "Михайло",
]


def load_data():
  if not os.path.exists(DB_FILE):
    return {}
  with open(DB_FILE, "r", encoding="utf-8") as f:
    return json.load(f)


def save_data(data):
  with open(DB_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)


# Стани для створення заявки
class DealState(StatesGroup):
  waiting_for_client = State()
  waiting_for_amount_received = State()
  waiting_for_amount_spent = State()


@dp.message(CommandStart())
async def cmd_start(message: Message):
  keyboard = ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="➕ Додати заявку"), KeyboardButton(text="📊 Звіт за день")]
      ],
      resize_keyboard=True,
  )
  await message.answer(
      "Вітаю! Це бот для обчислення та обліку заявок.\nВиберіть потрібну дію на"
      " клавіатурі нижче:",
      reply_markup=keyboard,
  )


@dp.message(F.text == "➕ Додати заявку")
async def add_deal_start(message: Message, state: FSMContext):
  # Створюємо інлайн-клавіатуру зі списком клієнтів
  keyboard_buttons = [
      [InlineKeyboardButton(text=client, callback_data=f"client_{client}")]
      for client in CLIENTS_LIST
  ]
  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

  await message.answer("Виберіть клієнта для нової заявки:", reply_markup=keyboard)
  await state.set_state(DealState.waiting_for_client)


@dp.callback_query(F.data.startswith("client_"))
async def process_client(callback: types.CallbackQuery, state: FSMContext):
  client_name = callback.data.replace("client_", "")
  await state.update_data(client=client_name)

  await callback.message.answer(
      f"Вибрано клієнта: <b>{client_name}</b>.\nВведіть суму, яку <b>отримано</b>"
      " (тільки число):",
      parse_mode="HTML",
  )
  await callback.answer()
  await state.set_state(DealState.waiting_for_amount_received)


@dp.message(DealState.waiting_for_amount_received)
async def process_amount_received(message: Message, state: FSMContext):
  try:
    amount = float(message.text.replace(",", "."))
    await state.update_data(received=amount)
    await message.answer("Тепер введіть суму, яку <b>витрачено</b> (тільки число):", parse_mode="HTML")
    await state.set_state(DealState.waiting_for_amount_spent)
  except ValueError:
    await message.answer("Будь ласка, введіть коректне число для отриманої суми.")


@dp.message(DealState.waiting_for_amount_spent)
async def process_amount_spent(message: Message, state: FSMContext):
  try:
    spent = float(message.text.replace(",", "."))
    data = await state.get_data()
    client = data["client"]
    received = data["received"]

    # Зберігаємо дані в JSON з урахуванням накопичення за сьогодні
    db = load_data()
    today = datetime.now().strftime("%Y-%m-%d")

    if today not in db:
      db[today] = {}

    if client not in db[today]:
      db[today][client] = {"received": 0.0, "spent": 0.0, "count": 0}

    # Додаємо нові суми до вже існуючих за день
    db[today][client]["received"] += received
    db[today][client]["spent"] += spent
    db[today][client]["count"] += 1

    save_data(db)

    await state.clear()
    await message.answer(
        f"✅ <b>Заявку успішно збережено!</b>\n\nКлієнт: {client}\nДодано отримано:"
        f" {received}\nДодано витрачено: {spent}",
        parse_mode="HTML",
    )
  except ValueError:
    await message.answer("Будь ласка, введіть коректне число для витраченої суми.")


@dp.message(F.text == "📊 Звіт за день")
async def daily_report(message: Message):
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")

  if today not in db or not db[today]:
    await message.answer("📅 За сьогодні ще немає записаних заявок.")
    return

  report_text = f"📊 <b>Звіт за сьогодні ({today}):</b>\n\n"
  total_received_day = 0
  total_spent_day = 0

  for client, values in db[today].items():
    rec = values["received"]
    spt = values["spent"]
    cnt = values["count"]
    total_received_day += rec
    total_spent_day += spt

    report_text.__(
        f"• <b>{client}</b> (заявок: {cnt}):\n"
        f"   — Отримано: <code>{rec}</code>\n"
        f"   — Витрачено: <code>{spt}</code>\n\n"
    )

  report_text += "━━━━━━━━━━━━━━━━━━━\n"
  report_text += f"💰 <b>Загалом отримано:</b> <code>{total_received_day}</code>\n"
  report_text += f"💸 <b>Загалом витрачено:</b> <code>{total_spent_day}</code>"

  await message.answer(report_text, parse_mode="HTML")


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
