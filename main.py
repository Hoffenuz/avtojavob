import asyncio
import os
import re
import logging
import requests
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from supabase import create_client, Client

# =========================================================================
# 1. SOZLAMALAR VA BAZAGA ULANISH
# =========================================================================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
# DIQQAT: Bu yerda albatta SERVICE_ROLE_KEY bo'lishi shart, aks holda parol o'zgartirib bo'lmaydi!
SUPABASE_KEY = os.getenv("SUPABASE_KEY") 
OCR_API_KEY = "K87990866288957"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
# 2. OCR FUNKSIYASI (API)
# =========================================================================
def get_text_from_api(file_bytes, file_type='jpg'):
    filename = 'file.pdf' if file_type == 'pdf' else 'file.jpg'
    mime_type = 'application/pdf' if file_type == 'pdf' else 'image/jpeg'

    payload = {
        'apikey': OCR_API_KEY,
        'language': 'eng',
        'isOverlayRequired': 'false',
        'OCREngine': '2' 
    }
    
    files = {'file': (filename, file_bytes, mime_type)}

    try:
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
# 3. KUNLARNI HISOBLASH (95,000 gacha limit bilan)
# =========================================================================
def calculate_tariff_days(text: str) -> int:
    raw_numbers = re.findall(r'\b\d{2}[.,\s]?\d{3}\b|\b\d{5,6}\b', text)
    
    for num_str in raw_numbers:
        try:
            clean_num = int(re.sub(r'\D', '', num_str))
            if 15000 <= clean_num <= 15500:
                return 7
            elif 33000 <= clean_num <= 34000:
                return 31
            elif 80000 <= clean_num <= 95000: 
                return 93
        except:
            continue
    # Xatolik yoki summa topilmasa 7 kun beriladi
    return 7 

# =========================================================================
# 4. USER YARATISH YOKI YANGILASH (To'liq aqlli versiya)
# =========================================================================
async def create_user_auto(email: str, tariff_days: int, message: Message, state: FSMContext):
    try:
        password = email.split("@")[0]
        user_id = None
        
        # A. Supabase Auth orqali yaratishga urinish
        try:
            response = supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })
            user_id = response.user.id
            await asyncio.sleep(1.5) # SQL trigger ishlashi uchun biroz kutish
        except Exception as e:
            error_msg = str(e).lower()
            
            # Agar foydalanuvchi bazada bor bo'lsa (Google orqali kirgan yoki eski user)
            if "already" in error_msg and "registered" in error_msg:
                
                # B. Profil ID sini topamiz
                profile_resp = supabase.table("profiles").select("id").eq("email", email).execute()
                
                if profile_resp.data:
                    user_id = profile_resp.data[0]["id"]
                    
                    # C. ENG MUHIMI: Unga majburiy parol o'rnatamiz!
                    supabase.auth.admin.update_user_by_id(
                        user_id, 
                        {"password": password}
                    )
                else:
                    raise Exception("Profil topilmadi. Adminga murojaat qiling.")
            else:
                raise e # Jiddiy xato bo'lsa, jarayonni to'xtatadi

        # D. Bazaga kunlarni va HOZIRGI VAQTNI yozish (Trial muammosini yo'q qiladi)
        current_time = datetime.now(timezone.utc).isoformat()
        
        if user_id:
            supabase.table("profiles").update({
                "tariff_days": tariff_days,
                "tariff_start_date": current_time, # Vaqtni aynan hozirgiga yangilash
                "is_trial_used": True
            }).eq("id", user_id).execute()

        # E. Foydalanuvchiga yagona tushunarli xabar
        await message.answer(
            f"✅ <b>To'lov tasdiqlandi!</b>\n\nProfilingiz PRO tarifga o'tkazildi:\n"
            f"📧 <b>Login:</b> <code>{email}</code>\n"
            f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
            f"Saytga avvalgidek <b>Google orqali</b> YOKI yuqoridagi <b>Login/Parol</b> bilan bemalol kira olasiz."
        )
            
        await asyncio.sleep(0.5)
        await message.answer("👇 <b>Yopiq kanalimiz:</b>\nhttps://t.me/+G5z5KWbXBZ04OTAy")
        await state.set_state(PaymentState.completed)
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi. Adminga murojaat qiling: {str(e)}")

# =========================================================================
# 5. BOT MATNLARNI QABUL QILISHI
# =========================================================================
@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text.lower()
    email_match = re.search(EMAIL_REGEX, message.text)
    current_state = await state.get_state()

    # Email yuborilsa
    if email_match:
        email = email_match.group(0)
        if current_state == PaymentState.waiting_for_email:
            # Chek oldin yuborilgan bo'lsa, saqlangan kunni olamiz
            data = await state.get_data()
            tariff_days = data.get("tariff_days", 7)
            
            await message.answer(f"📧 Email qabul qilindi. Profil tayyorlanmoqda...")
            await create_user_auto(email, tariff_days, message, state)
        else:
            await state.update_data(email=email)
            if current_state != PaymentState.completed:
                await state.set_state(PaymentState.waiting_for_check)
            await message.answer(f"📧 Email ({email}) saqlandi. Endi to'lov cheki rasmini yuboring.")
        return

    # Narx so'ralsa
    if any(word in text for word in PRICE_KEYWORDS):
        await message.answer("💰 <b>Avtotest Pro narxlari:</b>\n\n• 1 haftalik: 15,000 so'm\n• 1 oylik: 33,000 so'm\n• 3 oylik: 83,000 so'm")
        return

    # Start yoki boshqa matn
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

# =========================================================================
# 6. FAYLLAR VA CHEKLARNI QABUL QILISH
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
            
            if full_text == "ERROR_API":
                await msg.edit_text("❌ OCR serveri javob bermadi. Iltimos, bir ozdan so'ng qayta yuboring.")
                return

            is_valid = any(word in full_text.upper() for word in VALID_KEYWORDS)

            if is_valid:
                calculated_days = calculate_tariff_days(full_text)
                
                data = await state.get_data()
                email = data.get("email")
                
                if email:
                    await msg.edit_text("✅ Chek tasdiqlandi! Profil tayyorlanmoqda...")
                    await create_user_auto(email, calculated_days, message, state)
                else:
                    await state.update_data(tariff_days=calculated_days)
                    await state.set_state(PaymentState.waiting_for_email)
                    await msg.edit_text("✅ Chek qabul qilindi! Endi <b>Email manzilingizni</b> yozib yuboring.")
            else:
                await msg.edit_text("⚠️ Chekni o'qib bo'lmadi yoki xato chek yuborildi. Iltimos, tiniqroq rasm yuboring.")
        else:
            await msg.edit_text("⚠️ Rasm yoki PDF formatida yuboring.")
    except Exception as e:
        logging.error(f"Handle Files Error: {e}")
        await msg.edit_text("❌ Tizimda xatolik yuz berdi.")

# =========================================================================
# 7. BOTNI ISHGA TUSHIRISH
# =========================================================================
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
