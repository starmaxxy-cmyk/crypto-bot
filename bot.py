import asyncio
from datetime import datetime
import json
import os
import re
from aiogram import Bot, Dispatcher, F, types
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


# Стани
class DealState(StatesGroup):
  waiting_for_client = State()
  waiting_for_amount_received = State()
  waiting_for_amount_spent = State()


class EditState(StatesGroup):
  waiting_for_received = State()
  waiting_for_spent = State()


@dp.message(CommandStart())
async def cmd_start(message: Message):
  keyboard = ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="➕ Додати заявку"), KeyboardButton(text="📊 Звіт за день")],
          [
              KeyboardButton(text="🗑 Видалити останню заявку"),
              KeyboardButton(text="✏️ Редагувати останню заявку"),
          ],
          [KeyboardButton(text="🗑 Скинути день")],
      ],
      resize_keyboard=True,
  )
  await message.answer(
      "Вітаю! Це бот для обліку заявок.\nВиберіть потрібну дію на клавіатурі нижче:",
      reply_markup=keyboard,
  )


@dp.message(F.text == "➕ Додати заявку")
async def add_deal_start(message: Message, state: FSMContext):
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

    db = load_data()
    today = datetime.now().strftime("%Y-%m-%d")

    if today not in db:
      db[today] = []

    # Додаємо новий запис у список за сьогодні
    db[today].append({"client": client, "received": received, "spent": spent})

    save_data(db)

    await state.clear()
    await message.answer(
        f"✅ <b>Заявку успішно збережено!</b>\n\nКлієнт: {client}\nОтримано:"
        f" {received}\nВитрачено: {spent}",
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

  # Групуємо та підсумовуємо дані по клієнтах для звіту
  summary = {}
  total_received_day = 0
  total_spent_day = 0

  for entry in db[today]:
    c = entry["client"]
    rec = entry["received"]
    spt = entry["spent"]

    if c not in summary:
      summary[c] = {"received": 0.0, "spent": 0.0, "count": 0}

    summary[c]["received"] += rec
    summary[c]["spent"] += spt
    summary[c]["count"] += 1

    total_received_day += rec
    total_spent_day += spt

  report_text = f"📊 <b>Звіт за сьогодні ({today}):</b>\n\n"

  for client, values in summary.items():
    rec = values["received"]
    spt = values["spent"]
    cnt = values["count"]

    report_text += (
        f"• <b>{client}</b> (заявок: {cnt}):\n"
        f"   — Отримано: <code>{rec}</code>\n"
        f"   — Витрачено: <code>{spt}</code>\n\n"
    )

  report_text += "━━━━━━━━━━━━━━━━━━━\n"
  report_text += f"💰 <b>Загалом отримано:</b> <code>{total_received_day}</code>\n"
  report_text += f"💸 <b>Загалом витрачено:</b> <code>{total_spent_day}</code>"

  await message.answer(report_text, parse_mode="HTML")


@dp.message(F.text == "🗑 Видалити останню заявку")
async def delete_last_deal(message: Message):
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")

  if today in db and db[today]:
    removed = db[today].pop()
    if not db[today]:
      del db[today]
    save_data(db)
    await message.answer(
        f"✅ <b>Останню заявку успішно видалено!</b>\n\nКлієнт:"
        f" {removed['client']}\nОтримано: {removed['received']}\nВитрачено:"
        f" {removed['spent']}",
        parse_mode="HTML",
    )
  else:
    await message.answer("📅 За сьогодні немає записів для видалення.")


@dp.message(F.text == "✏️ Редагувати останню заявку")
async def edit_last_start(message: Message, state: FSMContext):
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")

  if today not in db or not db[today]:
    await message.answer("📅 За сьогодні ще немає заявок для редагування.")
    return

  last_deal = db[today][-1]
  await message.answer(
      f"Редагуємо останню заявку:\nКлієнт: <b>{last_deal['client']}</b>\nПопередня"
      f" сума отримано: {last_deal['received']}, витрачено:"
      f" {last_deal['spent']}\n\nВведіть нову суму, яку <b>отримано</b> (або"
      " надішліть крапку <code>.</code>, щоб залишити без змін):",
      parse_mode="HTML",
  )
  await state.set_state(EditState.waiting_for_received)


@dp.message(EditState.waiting_for_received)
async def edit_received(message: Message, state: FSMContext):
  text = message.text.strip()
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")
  last_deal = db[today][-1]

  if text != ".":
    try:
      amount = float(text.replace(",", "."))
      last_deal["received"] = amount
    except ValueError:
      await message.answer("Будь ласка, введіть коректне число або '.' щоб залишити поточне.")
      return

  save_data(db)
  await message.answer(
      f"Поточна витрачена сума: {last_deal['spent']}\nВведіть нову суму, яку"
      " <b>витрачено</b> (або надішліть крапку <code>.</code>, щоб залишити без"
      " змін):",
      parse_mode="HTML",
  )
  await state.set_state(EditState.waiting_for_spent)


@dp.message(EditState.waiting_for_spent)
async def edit_spent(message: Message, state: FSMContext):
  text = message.text.strip()
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")
  last_deal = db[today][-1]

  if text != ".":
    try:
      amount = float(text.replace(",", "."))
      last_deal["spent"] = amount
    except ValueError:
      await message.answer("Будь ласка, введіть коректне число або '.' щоб залишити поточне.")
      return

  save_data(db)
  await state.clear()
  await message.answer(
      f"✅ <b>Останню заявку успішно оновлено!</b>\n\nКлієнт:"
      f" {last_deal['client']}\nОтримано: {last_deal['received']}\nВитрачено:"
      f" {last_deal['spent']}",
      parse_mode="HTML",
  )


@dp.message(F.text == "🗑 Скинути день")
async def reset_today(message: Message):
  db = load_data()
  today = datetime.now().strftime("%Y-%m-%d")

  if today in db:
    del db[today]
    save_data(db)
    await message.answer("🗑 Усі сьогоднішні дані очищено. Можете вносити заявки наново!")
  else:
    await message.answer("📅 За сьогодні ще немає записів для очищення.")


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
