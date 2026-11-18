# --- ПАКЕТНИЙ ЗБІР І ОЧИЩЕННЯ ЗАЯВОК ---
order_batches = {}

@dp.message(F.forward_from_chat | F.forward_from | F.forward_origin)
async def catch_and_clean_order(message: types.Message):
    await process_order_message(message)

# Також зробимо обробку, якщо ви просто скопіюєте і вставите текст заявки без пересилання
@dp.message(F.text & ~F.text.startswith('/'))
async def catch_text_order(message: types.Message):
    # Ловимо тільки ті повідомлення, де є слова Exchange або New order
    text = message.text or ""
    if "Exchange" in text or "order" in text:
        await process_order_message(message)

async def process_order_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    
    if not text:
        return

    # Витягуємо номер заявки (наприклад, #74141)
    order_id_match = re.search(r'#(\d+)', text)
    order_id = order_id_match.group(1) if order_id_match else "00000"
    
    # Витягуємо частину "IN: [сума] [валюта]"
    in_match = re.search(r'IN:\s*([^\n]+)', text)
    in_text = in_match.group(1).strip() if in_match else ""
    
    # Витягуємо частину "OUT: [сума] [валюта]" і відсікаємо все зайве знизу
    out_match = re.search(r'OUT:\s*([^\n]+)', text)
    out_text = out_match.group(1).split('\n')[0].strip() if out_match else ""

    # Формуємо чистий рядок під вашу таблицю
    cleaned_line = f"#{order_id}. Exchange: IN: {in_text} OUT: {out_text}"
    
    if user_id not in order_batches:
        order_batches[user_id] = []
        
    order_batches[user_id].append(cleaned_line)
    count = len(order_batches[user_id])
    
    await message.answer(f"✅ Заявку очищено! У стовпчику: {count}\nЯк закінчите, напишіть /export")

@dp.message(Command("export"))
async def export_batch(message: types.Message):
    user_id = message.from_user.id
    if user_id not in order_batches or not order_batches[user_id]:
        await message.answer("⚠️ Стовпчик порожній. Пересилайте заявки!")
        return
        
    # Формуємо блок для копіювання одним кліком
    output_block = "📋 **Готовий стовпчик для таблиці:**\n\n```text\n"
    for item in order_batches[user_id]:
        output_block += f"{item}\n"
    output_block += "```\n*💡 Скопіюйте текст із блоку вище, клікніть на першу клітинку в таблиці та натисніть Ctrl+V.*"
    
    # Очищуємо пам'ять
    order_batches[user_id] = []
    
    await message.answer(output_block, parse_mode="Markdown")
