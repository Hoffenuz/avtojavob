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

# 1. Sozlamalarni yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OCR_API_KEY = "K87990866288957"

# Supabase ulanish
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Botni sozlash
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)

# =========================================================================
# HOLATLAR VA KALIT SO'ZLAR
# =========================================================================

class PaymentState(StatesGroup):
    waiting_for_check = State() 
    waiting_for_email = State() 
    completed = State()        

EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

PRICE_KEYWORDS = [
    "narx", "qancha", "necha", "pul", "som", "so'm", "sum", "bahosi", "obuna", "tarif", "pro", "vip", "premium",
    "нарх", "қанча", "неча", "пул", "сўм", "сум", "баҳо", "обуна", "тариф", "про", "вип", "премиум"
]

VALID_KEYWORDS = ["5614", "6847", "07", "ELDOR", "ATAJANOV", "PAYME", "CLICK", "O'TKAZMA", "ЎТКАЗМА", "ПЕРЕВОД"]

# =========================================================================
# YORDAMCHI FUNKSIYALAR
# =========================================================================

def get_text_from_api(file_bytes, file_type='jpg'):
    filename = 'file.pdf' if file_type == 'pdf' else 'file.jpg'
    payload = {'apikey': OCR_API_KEY, 'language': 'eng', 'OCREngine': 2}
    files = {'file': (filename, file_bytes, 'application/pdf' if file_type == 'pdf' else 'image/jpeg')}
    try:
        response = requests.post('https://api.ocr.space/parse/image', files=files, data=payload)
        result = response.json()
        if result.get('ParsedResults'):
            return " ".join([res.get('ParsedText', '') for res in result['ParsedResults']])
        return ""
    except Exception as e:
        logging.error(f"OCR API Error: {e}")
        return ""

async def create_user_auto(email, message: Message, state: FSMContext):
    try:
        password = email.split("@")[0]
        supabase.auth.admin.create_user({"email": email, "password": password, "email_confirm": True})
        
        await message.answer(
            f"✅ <b>To'lov tasdiqlandi! Profil yaratildi:</b>\n\n"
            f"📧 <b>Login:</b> <code>{email}</code>\n"
            f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
            f"👇 <b>Yopiq kanalimizga qo'shiling:</b>\nhttps://t.me/+G5z5KWbXBZ04OTAy"
        )
        await state.set_state(PaymentState.completed)
    except Exception as e:
        if "already registered" in str(e):
            await message.answer(f"⚠️ Bu email ({email}) allaqachon mavjud.\nKanal: https://t.me/+G5z5KWbXBZ04OTAy")
            await state.set_state(PaymentState.completed)
        else:
            await message.answer(f"❌ Xatolik: {str(e)}")

# =========================================================================
# ASOSIY MATNLI XABARLAR ISHLOVCHISI
# =========================================================================

@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text.lower()
    email_match = re.search(EMAIL_REGEX, message.text)
    current_state = await state.get_state()
    
    # User ma'lumotlarini olish (narx yuborilganmi yoki yo'qligini tekshirish uchun)
    user_data = await state.get_data()
    price_sent = user_data.get("price_sent", False)

    # 1. EMAIL TEKSHIRISH (Har doim birinchi o'rinda)
    if email_match:
        email = email_match.group(0)
        if current_state == PaymentState.waiting_for_email:
            await message.answer(f"📧 Email qabul qilindi. Profil ochilmoqda...")
            await create_user_auto(email, message, state)
        else:
            await state.update_data(email=email)
            if current_state != PaymentState.completed:
                await state.set_state(PaymentState.waiting_for_check)
            await message.answer(f"📧 Email ({email}) saqlandi. Endi to'lov cheki rasmini yuboring.")
        return

    # 2. NARX VA TARIFLAR (FAQAT BIR MARTA YUBORILADI)
    if any(word in text for word in PRICE_KEYWORDS):
        if not price_sent:
            await message.answer(
                "💰 <b>Avtotest Pro narxlari:</b>\n\n"
                "• 1 haftalik: <b>15,000 so'm</b>\n"
                "• 1 oylik: <b>33,000 so'm</b>\n"
                "• 3 oylik: <b>83,000 so'm</b>"
            )
            # Narx yuborilganini belgilab qo'yamiz
            await state.update_data(price_sent=True)
            # Agar hali state None bo'lsa, uni waiting_for_check ga o'tkazamiz
            if current_state is None:
                await state.set_state(PaymentState.waiting_for_check)
        else:
            # Agar narx allaqachon yuborilgan bo'lsa
            await message.answer(
                "Boshqa masalada savollaringiz bo'lsa yozib qoldiring hamda adminning javobini kutishingizni iltimos qilamiz. 👨‍💻"
            )
        return

    # 3. KARTA MA'LUMOTI (Faqat birinchi marta yozganda)
    if current_state is None:
        await message.answer(
            "Assalomu alaykum! Pro versiyani olish uchun to'lov qiling:\n\n"
            "💳 <b>Karta raqam:</b>\n"
            "<code>5614684708939507</code>\n"
            "👤 <b>Eldor Atajanov</b>\n\n"
            "❗️ To'lovdan so'ng <b>Chek</b> va <b>Emailingizni</b> shu yerga yuboring."
        )
        await state.set_state(PaymentState.waiting_for_check)
        return

    # 4. DEFAULT: ADMIN XABARI
    if current_state != PaymentState.completed:
        await message.answer(
            "Boshqa masalada savollaringiz bo'lsa yozib qoldiring hamda adminning javobini kutishingizni iltimos qilamiz. 👨‍💻"
        )

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
            full_text = await asyncio.to_thread(get_text_from_api, file_bytes, file_type)
            if any(word in full_text.upper() for word in VALID_KEYWORDS):
                data = await state.get_data()
                email = data.get("email")
                if email:
                    await msg.edit_text("✅ Chek tasdiqlandi! User yaratilmoqda...")
                    await create_user_auto(email, message, state)
                else:
                    await state.set_state(PaymentState.waiting_for_email)
                    await msg.edit_text("✅ Chek qabul qilindi!\nEndi user ochish uchun <b>Email manzilingizni</b> yuboring.")
            else:
                await msg.edit_text("⚠️ Chekni o'qib bo'lmadi yoki xato chek.\nIltimos, tiniqroq rasm yuboring.")
        else:
            await msg.edit_text("⚠️ Faqat rasm yoki PDF qabul qilinadi.")
    except Exception as e:
        await msg.edit_text("❌ Xatolik yuz berdi.")

async def main():
    print("🤖 Avtotest Smart Bot ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
