import asyncio
import os
import re
import logging
import easyocr
import fitz  # Bu PyMuPDF (PDF o'qish uchun)
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

# Supabase ulanish
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Botni sozlash
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)

# OCR Reader (Rasmdan yozuv o'qish)
reader = easyocr.Reader(['en'], gpu=False) 

# Holatlar (States)
class PaymentState(StatesGroup):
    waiting_for_check = State() # Email bor, chek kutyapmiz
    waiting_for_email = State() # Chek bor, email kutyapmiz
    completed = State()         # ✅ USER TAYYOR (Bot jim turadi)

# Email Regex
EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

# Tasdiqlash uchun kalit so'zlar
VALID_KEYWORDS = ["5614", "6847", "07", "ELDOR", "ATAJANOV", "PAYME", "CLICK"]

# =========================================================================
# YORDAMCHI FUNKSIYALAR
# =========================================================================

async def create_user_auto(email, message: Message, state: FSMContext):
    try:
        # Parol yaratish (email boshidagi qism)
        password = email.split("@")[0]
        
        # Supabase Admin orqali user yaratish
        user = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True
        })
        
        # -------------------------------------------------
        # 1-XABAR: Login va Parol
        # -------------------------------------------------
        await message.answer(
            f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
            f"Sizning profilingiz yaratildi:\n"
            f"📧 <b>Login:</b> <code>{email}</code>\n"
            f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
            f"Saytga kirib bemalol foydalanishingiz mumkin."
        )

        # 0.5 soniya kutish (xabarlar ketma-ketligi chiroyli chiqishi uchun)
        await asyncio.sleep(0.5)

        # -------------------------------------------------
        # 2-XABAR: Kanal havolasi (ALOHIDA)
        # -------------------------------------------------
        await message.answer(
            f"👇 <b>Bizning yopiq kanalimizga qo'shiling:</b>\n"
            f"https://t.me/+G5z5KWbXBZ04OTAy"
        )

        print(f"✅ User yaratildi: {email}")
        
        # ✅ Holatni 'completed' ga o'tkazamiz -> Bot endi bu userga javob bermaydi
        await state.set_state(PaymentState.completed)
        
    except Exception as e:
        error_text = str(e)
        if "already registered" in error_text:
            # Agar oldin o'tgan bo'lsa
            await message.answer(f"⚠️ Bu email ({email}) allaqachon ro'yxatdan o'tgan.")
            # Kanalni barbir tashlab qo'yamiz
            await message.answer("Kanalimiz: https://t.me/+G5z5KWbXBZ04OTAy")
            # Va jim turish rejimiga o'tkazamiz
            await state.set_state(PaymentState.completed)
        else:
            await message.answer(f"❌ Xatolik yuz berdi: {error_text}")

def is_valid_check(text):
    text = text.upper()
    for word in VALID_KEYWORDS:
        if word in text:
            return True
    return False

# =========================================================================
# BOT LOGIKASI
# =========================================================================

# 0. ✅ TAYYOR USERLAR (JIM TURISH)
# Agar user 'completed' holatida bo'lsa, bot hech narsa qilmaydi.
@dp.message(PaymentState.completed)
@dp.business_message(PaymentState.completed)
async def handle_completed_user(message: Message):
    return # <-- SHU YERDA KOD TO'XTAYDI (Javob qaytarmaydi)

# 1. Mijoz EMAIL yozganda
@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text
    email_match = re.search(EMAIL_REGEX, text)
    
    # A) Agar EMAIL topilsa
    if email_match:
        email = email_match.group(0)
        
        current_state = await state.get_state()
        
        if current_state == PaymentState.waiting_for_email:
            # Agar oldin chek tashlangan bo'lsa -> User ochamiz
            await message.answer(f"📧 Email qabul qilindi: {email}. User ochilmoqda...")
            await create_user_auto(email, message, state)
        else:
            # Agar birinchi email tashlagan bo'lsa -> Chek so'raymiz
            await state.update_data(email=email)
            await state.set_state(PaymentState.waiting_for_check)
            await message.answer(f"📧 Email ({email}) saqlandi.\nEndi iltimos, to'lov <b>cheki rasmini</b> yuboring.")
            
    # B) Agar oddiy so'z bo'lsa (Salom, karta...)
    else:
        is_business = message.business_connection_id is not None
        keywords = ["karta", "to'lov", "narx", "salom", "pro", "sotib","салом"]
        
        # Botga to'g'ridan-to'g'ri yozsa yoki kalit so'z bo'lsa
        if not is_business or any(word in text.lower() for word in keywords):
            await message.answer(
                "Assalomu alaykum! Pro versiyani olish uchun to'lov qiling:\n\n"
                "💳 <b>Karta raqam:</b>\n"
                "<code>5614 6847 0893 9507</code>\n"
                "👤 <b>Eldor Atajanov</b>\n\n"
                "❗️ To'lovdan so'ng <b>Chek</b> va <b>Emailingizni</b> shu yerga yuboring."
            )

# 2. Mijoz RASM yoki PDF (Chek) tashlaganda
@dp.message(F.photo | F.document)
@dp.business_message(F.photo | F.document)
async def handle_files(message: Message, state: FSMContext):
    msg = await message.answer("⏳ Chek tekshirilmoqda, iltimos kuting...")
    
    full_text = ""
    
    try:
        # --- A) Agar RASM bo'lsa ---
        if message.photo:
            file_id = message.photo[-1].file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path
            
            downloaded_file = await bot.download_file(file_path)
            image_bytes = downloaded_file.read()
            
            results = reader.readtext(image_bytes, detail=0)
            full_text = " ".join(results)
            
        # --- B) Agar PDF bo'lsa ---
        elif message.document and message.document.file_name.lower().endswith('.pdf'):
            file_id = message.document.file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path
            
            downloaded_file = await bot.download_file(file_path)
            pdf_bytes = downloaded_file.read()
            
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                for page in doc:
                    full_text += page.get_text()

        # --- TEKSHIRISH ---
        print(f"📄 O'qilgan matn: {full_text}")
        
        if is_valid_check(full_text):
            # Agar chek to'g'ri bo'lsa
            data = await state.get_data()
            saved_email = data.get("email")
            
            if saved_email:
                # User ochamiz va TUGATAMIZ
                await msg.edit_text("✅ Chek tasdiqlandi! User yaratilmoqda...")
                await create_user_auto(saved_email, message, state)
            else:
                # Email so'raymiz
                await state.set_state(PaymentState.waiting_for_email)
                await msg.edit_text("✅ Chek qabul qilindi!\nEndi user ochish uchun <b>Email manzilingizni</b> yozib yuboring.")
        else:
            await msg.edit_text("⚠️ Kechirasiz, bu chekda kerakli ma'lumotlarni o'qiy olmadim.\nIltimos, tiniqroq rasm yuboring.")

    except Exception as e:
        print(f"Xatolik: {e}")
        await msg.edit_text("❌ Tizimda xatolik yuz berdi.")

# --- ISHGA TUSHIRISH ---
async def main():
    print("🤖 Avtotest Smart Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())