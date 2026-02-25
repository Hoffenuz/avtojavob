import asyncio
import os
import re
import logging
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Sozlamalar
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") # BU YERDA SERVICE_ROLE_KEY BO'LISHI SHART!
OCR_API_KEY = "K87990866288957"

# Supabase ulanish
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Botni sozlash
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)

# Holatlar
class PaymentState(StatesGroup):
    waiting_for_check = State() 
    waiting_for_email = State() 
    completed = State()        

EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
VALID_KEYWORDS = ["5614", "6847", "07", "ELDOR", "ATAJANOV", "PAYME", "CLICK", "OTKAZMA", "ПЕРЕВОД"]
PRICE_KEYWORDS = ["narx", "qancha", "necha", "pul", "som", "so'm", "sum", "нарх", "қанча", "неча", "пул", "сўм", "сум"]

# =========================================================================
# OCR FUNKSIYASI (API)
# =========================================================================
def get_text_from_api(file_bytes, file_type='jpg'):
    filename = 'file.pdf' if file_type == 'pdf' else 'file.jpg'
    mime_type = 'application/pdf' if file_type == 'pdf' else 'image/jpeg'

    # Qiymatlarni satr (str) holatida yuboramiz
    payload = {
        'apikey': OCR_API_KEY,
        'language': 'eng',
        'isOverlayRequired': 'false',
        'OCREngine': '2' 
    }
    
    files = {'file': (filename, file_bytes, mime_type)}

    try:
        # Timeout 30 soniyaga ko'paytirildi
        response = requests.post('https://api.ocr.space/parse/image', files=files, data=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get('ParsedResults'):
            return " ".join([res.get('ParsedText', '') for res in result['ParsedResults']])
        return ""
    except Exception as e:
        logging.error(f"OCR Error: {e}")
        return "ERROR_API"

# =========================================================================
# USER YARATISH
# =========================================================================
async def create_user_auto(email, message: Message, state: FSMContext):
    try:
        password = email.split("@")[0]
        # Supabase Admin Auth orqali yaratish
        supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True
        })
        
        await message.answer(
            f"✅ <b>To'lov tasdiqlandi!</b>\n\nProfilingiz yaratildi:\n"
            f"📧 <b>Login:</b> <code>{email}</code>\n"
            f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
            f"Saytga kirib foydalanishingiz mumkin."
        )
        await asyncio.sleep(0.5)
        await message.answer("👇 <b>Yopiq kanalimiz:</b>\nhttps://t.me/+G5z5KWbXBZ04OTAy")
        await state.set_state(PaymentState.completed)
    except Exception as e:
        if "already registered" in str(e):
            await message.answer(f"⚠️ Bu email ({email}) allaqachon mavjud.\nKanal: https://t.me/+G5z5KWbXBZ04OTAy")
            await state.set_state(PaymentState.completed)
        else:
            await message.answer(f"❌ Supabase xatosi: {str(e)}")

# =========================================================================
# BOT LOGIKASI
# =========================================================================

@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text.lower()
    email_match = re.search(EMAIL_REGEX, message.text)
    current_state = await state.get_state()

    # 1. Email
    if email_match:
        email = email_match.group(0)
        if current_state == PaymentState.waiting_for_email:
            await message.answer(f"📧 Email qabul qilindi. User ochilmoqda...")
            await create_user_auto(email, message, state)
        else:
            await state.update_data(email=email)
            if current_state != PaymentState.completed:
                await state.set_state(PaymentState.waiting_for_check)
            await message.answer(f"📧 Email ({email}) saqlandi. Endi to'lov cheki rasmini yuboring.")
        return

    # 2. Narx
    if any(word in text for word in PRICE_KEYWORDS):
        await message.answer("💰 <b>Avtotest Pro narxlari:</b>\n\n• 1 haftalik: 15,000 so'm\n• 1 oylik: 33,000 so'm\n• 3 oylik: 83,000 so'm")
        return

    # 3. Karta (Faqat bir marta)
    if current_state is None:
        await message.answer(
            "Assalomu alaykum! Pro versiyani olish uchun to'lov qiling:\n\n"
            "💳 <b>Karta:</b> <code>5614684708939507</code>\n"
            "👤 <b>Eldor Atajanov</b>\n\n"
            "❗️ To'lovdan so'ng <b>Chek</b> va <b>Emailni</b> yuboring."
        )
        await asyncio.sleep(0.5)
        await message.answer("Boshqa masalada admin javobini kuting. 👨‍💻")
        await state.set_state(PaymentState.waiting_for_check)
        return
    
    return

# =========================================================================
# FAYLLARNI QABUL QILISH
# =========================================================================
@dp.message(F.photo | F.document)
@dp.business_message(F.photo | F.document)
async def handle_files(message: Message, state: FSMContext):
    msg = await message.answer("⏳ Chek tekshirilmoqda, iltimos kuting...")
    file_bytes, file_type = None, 'jpg'

    try:
        if message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
            file_bytes = (await bot.download_file(file.file_path)).read()
        elif message.document and message.document.file_name.lower().endswith('.pdf'):
            file = await bot.get_file(message.document.file_id)
            file_bytes = (await bot.download_file(file.file_path)).read()
            file_type = 'pdf'

        if file_bytes:
            # API chaqiruvini alohida thread'da bajaramiz
            full_text = await asyncio.to_thread(get_text_from_api, file_bytes, file_type)
            
            if full_text == "ERROR_API":
                await msg.edit_text("❌ OCR serveri javob bermadi. Iltimos, bir ozdan so'ng qayta yuboring.")
                return

            # Keywordlarni tekshirish (Katta-kichik harfni inobatga olib)
            is_valid = any(word in full_text.upper() for word in VALID_KEYWORDS)

            if is_valid:
                data = await state.get_data()
                email = data.get("email")
                if email:
                    await msg.edit_text("✅ Chek tasdiqlandi! Profil yaratilmoqda...")
                    await create_user_auto(email, message, state)
                else:
                    await state.set_state(PaymentState.waiting_for_email)
                    await msg.edit_text("✅ Chek qabul qilindi! Endi <b>Email manzilingizni</b> yozib yuboring.")
            else:
                await msg.edit_text("⚠️ Chekni o'qib bo'lmadi yoki xato chek yuborildi. Iltimos, tiniqroq rasm yuboring.")
        else:
            await msg.edit_text("⚠️ Rasm yoki PDF formatida yuboring.")
    except Exception as e:
        logging.error(f"Handle Files Error: {e}")
        await msg.edit_text("❌ Tizimda xatolik yuz berdi.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
