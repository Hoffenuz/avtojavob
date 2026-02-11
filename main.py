import asyncio
import os
import re
import logging
import requests  # API ga ulanish uchun
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
# Siz bergan API kalit
OCR_API_KEY = "K87990866288957"

# Supabase ulanish
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Botni sozlash
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)

# Holatlar (States)
class PaymentState(StatesGroup):
    waiting_for_check = State() # To'lov ma'lumoti berildi, chek kutyapmiz
    waiting_for_email = State() # Chek oldik, email kutyapmiz
    completed = State()         # Jarayon tugadi

# Regex va Kalit so'zlar
EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

# Check ichidagi so'zlarni tekshirish uchun (Lotin va Kirill)
VALID_KEYWORDS = [
    "5614", "6847", "07", "ELDOR", "ATAJANOV", "PAYME", "CLICK", 
    "OTKAZMA", "O'TKAZMA", "ЎТКАЗМА", "УТКАЗМА", "ПЕРЕВОД"
]

# Narx so'ralganda ishlatiladigan so'zlar
PRICE_KEYWORDS = [
    "narx", "qancha", "necha", "pul", "som", "so'm", "sum", 
    "нарх", "қанча", "неча", "пул", "сўм", "сум", "баҳо"
]

# Salomlashish va to'lov so'ralganda (Start berish)
PAYMENT_START_KEYWORDS = [
    "karta", "to'lov", "sotib", "salom", "pro", "obuna", "oylik", "tarif", 
    "vip", "premium", "start",
    "карта", "тўлов", "толов", "сотиб", "салом", "про", "обуна", "ойлик", "тариф",
    "вип", "премиум", "старт"
]

# =========================================================================
# OCR FUNKSIYASI (API ORQALI)
# =========================================================================
def get_text_from_api(file_bytes, file_type='jpg'):
    """
    Rasm yoki PDF baytlarini OCR.space API ga yuboradi va matnni qaytaradi.
    """
    filename = 'file.pdf' if file_type == 'pdf' else 'file.jpg'
    mime_type = 'application/pdf' if file_type == 'pdf' else 'image/jpeg'

    payload = {
        'apikey': OCR_API_KEY,
        'language': 'eng', 
        'isOverlayRequired': False,
        'OCREngine': 2 
    }
    
    files = {
        'file': (filename, file_bytes, mime_type)
    }

    try:
        response = requests.post('https://api.ocr.space/parse/image', files=files, data=payload)
        result = response.json()
        
        if result.get('IsErroredOnProcessing'):
            return ""
        
        parsed_results = result.get('ParsedResults')
        if parsed_results:
            full_text = " ".join([res.get('ParsedText', '') for res in parsed_results])
            return full_text
        return ""
    except Exception as e:
        print(f"API Xatolik: {e}")
        return ""

# =========================================================================
# YORDAMCHI FUNKSIYALAR
# =========================================================================

async def create_user_auto(email, message: Message, state: FSMContext):
    try:
        password = email.split("@")[0]
        # Supabase da user yaratish
        user = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True
        })
        
        await message.answer(
            f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
            f"Sizning profilingiz yaratildi:\n"
            f"📧 <b>Login:</b> <code>{email}</code>\n"
            f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
            f"Saytga kirib bemalol foydalanishingiz mumkin."
        )
        await asyncio.sleep(0.5)
        await message.answer(
            f"👇 <b>Bizning yopiq kanalimizga qo'shiling:</b>\n"
            f"https://t.me/+G5z5KWbXBZ04OTAy"
        )
        # Jarayon tugadi, holatni completed ga tushiramiz
        await state.set_state(PaymentState.completed)
        
    except Exception as e:
        error_text = str(e)
        if "already registered" in error_text:
            await message.answer(f"⚠️ Bu email ({email}) allaqachon ro'yxatdan o'tgan.")
            await message.answer("Kanalimiz: https://t.me/+G5z5KWbXBZ04OTAy")
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

# Agar user completed holatida bo'lsa va yana yozsa, uni "noldan" boshlatishimiz mumkin
# yoki jim turishimiz mumkin. Hozircha jim turishni afzal ko'ramiz, 
# lekin kalit so'z yozsa reaksiya bildiramiz.

@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text.lower() # Hammasini kichik harf qilamiz
    email_match = re.search(EMAIL_REGEX, message.text) # Original textdan email qidiramiz
    
    current_state = await state.get_state()

    # ---------------------------------------------------------
    # 1. EMAIL TEKSHIRISH (Eng yuqori ustuvorlik)
    # ---------------------------------------------------------
    if email_match:
        email = email_match.group(0)
        
        # Agar biz email kutayotgan bo'lsak
        if current_state == PaymentState.waiting_for_email:
            await message.answer(f"📧 Email qabul qilindi: {email}. User ochilmoqda...")
            await create_user_auto(email, message, state)
            return

        # Agar biz hali hech narsa kutmayotgan bo'lsak yoki chek kutayotgan bo'lsak
        else:
            await state.update_data(email=email)
            await state.set_state(PaymentState.waiting_for_check)
            await message.answer(f"📧 Email ({email}) saqlandi.\nEndi iltimos, to'lov <b>cheki rasmini</b> yuboring.")
            return

    # ---------------------------------------------------------
    # 2. NARX SO'RASH (Har doim javob beradi)
    # ---------------------------------------------------------
    if any(word in text for word in PRICE_KEYWORDS):
        await message.answer(
            "💰 <b>Avtotest Pro narxlari:</b>\n\n"
            "• 1 haftalik: <b>15,000 so'm</b>\n"
            "• 1 oylik: <b>33,000 so'm</b>\n"
            "• 3 oylik: <b>83,000 so'm</b>\n\n"
            
        )
        return

    # ---------------------------------------------------------
    # 3. TO'LOV / SALOM / START (Faqat 1 marta javob beradi)
    # ---------------------------------------------------------
    is_business = message.business_connection_id is not None
    
    # Agar so'zlar ichida "to'lov", "salom" va h.k. bo'lsa
    if not is_business or any(word in text for word in PAYMENT_START_KEYWORDS):
        
        # MANTIQ: Agar foydalanuvchi allaqachon jarayonni boshlagan bo'lsa (waiting_for_check),
        # unga qayta-qayta karta raqam tashlamaymiz.
        # Faqat holati "None" (yangi) yoki "Completed" (tugatgan) bo'lsagina tashlaymiz.
        
        if current_state is None or current_state == PaymentState.completed:
            await message.answer(
                "Assalomu alaykum! Pro versiyani olish uchun to'lov qiling:\n\n"
                "💳 <b>Karta raqam:</b>\n"
                "<code>5614684708939507</code>\n"
                "👤 <b>Eldor Atajanov</b>\n\n"
                "❗️ To'lovdan so'ng <b>Chek</b> va <b>Emailingizni</b> shu yerga yuboring."
            )
            
            await asyncio.sleep(0.5)
            
            await message.answer(
                "📞 Boshqa masalada savollaringiz bo'lsa @avtotestu_ad2 ga murojat qiling."
            )
            
            # Holatni "Chek kutish"ga o'tkazamiz. 
            # Endi user qayta "salom" desa, bu if ga kirmaydi.
            await state.set_state(PaymentState.waiting_for_check)
        
        return

# ---------------------------------------------------------
# 4. RASM YOKI PDF (CHEK) QABUL QILISH
# ---------------------------------------------------------
@dp.message(F.photo | F.document)
@dp.business_message(F.photo | F.document)
async def handle_files(message: Message, state: FSMContext):
    # Har qanday rasm kelsa reaksiya bildiramiz (lekin holatni tekshirish mumkin)
    msg = await message.answer("⏳ Chek tekshirilmoqda, iltimos kuting...")
    
    file_bytes = None
    file_type = 'jpg'

    try:
        # --- Faylni yuklab olish ---
        if message.photo:
            file_id = message.photo[-1].file_id
            file = await bot.get_file(file_id)
            downloaded_file = await bot.download_file(file.file_path)
            file_bytes = downloaded_file.read()
            file_type = 'jpg'
            
        elif message.document and message.document.file_name.lower().endswith('.pdf'):
            file_id = message.document.file_id
            file = await bot.get_file(file_id)
            downloaded_file = await bot.download_file(file.file_path)
            file_bytes = downloaded_file.read()
            file_type = 'pdf'

        if file_bytes:
            # --- API GA YUBORISH ---
            full_text = await asyncio.to_thread(get_text_from_api, file_bytes, file_type)
            print(f"📄 API dan kelgan matn: {full_text}")

            if is_valid_check(full_text):
                data = await state.get_data()
                saved_email = data.get("email")
                
                if saved_email:
                    await msg.edit_text("✅ Chek tasdiqlandi! User yaratilmoqda...")
                    await create_user_auto(saved_email, message, state)
                else:
                    await state.set_state(PaymentState.waiting_for_email)
                    await msg.edit_text("✅ Chek qabul qilindi!\nEndi user ochish uchun <b>Email manzilingizni</b> yozib yuboring.")
            else:
                await msg.edit_text("⚠️ Chekni o'qib bo'lmadi yoki noto'g'ri chek.\nIltimos, tiniqroq rasm yuboring.")
        else:
             await msg.edit_text("⚠️ Faqat Rasm yoki PDF formatidagi chek qabul qilinadi.")

    except Exception as e:
        print(f"Xatolik: {e}")
        await msg.edit_text("❌ Tizimda xatolik yuz berdi.")

# --- ISHGA TUSHIRISH ---
async def main():
    print("🤖 Avtotest Smart Bot ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())