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

    # 2. Шукаємо ВСІ числа з копійками формату 12345.00 або 12 345.00
    amount = "Не вказано"
    # Знаходимо всі входження великих сум (від 10 грн і вище з копійками)
    found_amounts = re.findall(r'\b([0-9]{1,3}(?:\s?[0-9]{3})*[\.,]\d{2})\b', extracted_text)
    
    valid_amounts = []
    for a in found_amounts:
        clean_a = a.replace(' ', '').replace(',', '.')
        try:
            val = float(clean_a)
            # Відсікаємо комісії (0.00 тощо) або дрібні числа, залишаємо реальні суми платежу
            if val > 1.0: 
                valid_amounts.append(val)
        except:
            pass

    if valid_amounts:
        # Беремо найбільшу суму на квитанції (це і буде сума переказу 43683.00)
        amount = f"{max(valid_amounts):.2f}"

    # 3. Парсинг дати та часу
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
