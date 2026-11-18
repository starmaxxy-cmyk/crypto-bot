@dp.message(F.photo | F.document)
async def handle_receipt(message: types.Message):
    status_msg = await message.answer("⏳ Зчитую реквізити квитанції Мій Господин...")
    
    file_id = None
    file_name = ""
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = "receipt.jpg"
    elif message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name.lower()

    if not file_id:
        await status_msg.edit_text("❌ Не вдалося отримати файл.")
        return

    file = await bot.get_file(file_id)
    file_path_on_server = file.file_path
    downloaded_file = await bot.download_file(file_path_on_server)
    
    local_filename = f"temp_{message.from_user.id}_{file_name}"
    with open(local_filename, "wb") as f:
        f.write(downloaded_file.read())

    extracted_text = ""
    if local_filename.endswith('.pdf'):
        try:
            with pdfplumber.open(local_filename) as pdf:
                for page in pdf.pages:
                    extracted_text += page.extract_text() or ""
        except Exception as e:
            pass
    
    if not extracted_text.strip():
        extracted_text = extract_full_text(local_filename)

    if os.path.exists(local_filename):
        os.remove(local_filename)

    # 1. Парсинг коду квитанції
    code_match = re.search(r'(?:[A-Za-z0-9]{15,45}|[A-Z0-9-]{15,})', extracted_text)
    receipt_code = code_match.group(0) if code_match else "Не знайдено"

    # 2. Надійний пошук суми (працює з пробілами між тисячами, наприклад "43 683.00")
    amount = "Не вказано"
    amount_match = re.search(r'Сума\s*\(грн\)\s*([0-9\s]+[\.,]\d{2})', extracted_text, re.IGNORECASE)
    if amount_match:
        amount = amount_match.group(1).replace(' ', '').replace(',', '.')
    else:
        amounts = re.findall(r'\b([0-9]{1,3}(?:\s[0-9]{3})+[\.,]\d{2})\b', extracted_text)
        if amounts:
            amount = amounts[0].replace(' ', '').replace(',', '.')
        else:
            # Запасний варіант: шукаємо будь-яке число з копійками біля слова грн
            simple_match = re.search(r'([0-9\s]+[\.,]\d{2})\s*(?:грн|UAH)', extracted_text, re.IGNORECASE)
            if simple_match:
                amount = simple_match.group(1).replace(' ', '').replace(',', '.')

    # 3. Парсинг дати та часу (наприклад, 04.11.2026 20:28)
    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})', extracted_text)
    receipt_date = date_match.group(1) if date_match else "Не вказано"

    # Посилання для перевірки
    if "P24" in receipt_code or "PRIVAT" in extracted_text.upper():
        check_url = f"https://www.privatbank.ua/sendmoney/check?reference={receipt_code}"
        bank_name = "ПриватБанку"
    else:
        check_url = f"https://check.gov.ua/{receipt_code}"
        bank_name = "check.gov.ua"

    response_text = (
        f"✅ Номер квитанції: `{receipt_code}`\n\n"
        f"💵 Сума: {amount} грн\n"
        f"📅 Дата: {receipt_date}\n\n"
        f"⚠️ Квитанція не перевірена, будь ласка, перевірте її самі, бо я тупий!!!\n\n"
        f"Натисніть кнопку нижче для переходу до офіційного джерела:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏛 Відкрити в {bank_name}", url=check_url)]
    ])

    await status_msg.edit_text(response_text, reply_markup=keyboard, parse_mode="Markdown")
