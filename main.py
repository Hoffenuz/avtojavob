import asyncio
import os
import re
import logging
import requests
import base64
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# =========================================================================
# 1. SOZLAMALAR
# =========================================================================
load_dotenv()
BOT_TOKEN       = os.getenv("BOT_TOKEN")
SUPABASE_URL    = os.getenv("SUPABASE_URL")
BOT_SECRET      = os.getenv("BOT_SECRET")
OCR_API_KEY     = os.getenv("OCR_API_KEY", "K87990866288957")

# Edge Function URL
EDGE_URL = f"{SUPABASE_URL}/functions/v1/bot-user-manager"

# Bot
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)


# =========================================================================
# 2. HOLATLAR
# =========================================================================
class PaymentState(StatesGroup):
    waiting_for_check = State()
    waiting_for_email = State()
    completed         = State()


EMAIL_REGEX    = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
VALID_KEYWORDS = ["5614", "6847", "07", "ELDOR", "ATAJANOV", "PAYME", "CLICK", "OTKAZMA", "ПЕРЕВОД"]
PRICE_KEYWORDS = ["narx", "qancha", "necha", "pul", "som", "so'm", "sum",
                  "нарх", "қанча", "неча", "пул", "сўм", "сум"]


# =========================================================================
# 3. EDGE FUNCTION CHAQIRUVI (service_role bot da emas, Edge da)
# =========================================================================
async def call_bot_manager(action: str, **kwargs) -> dict:
    """
    Barcha Supabase operatsiyalari Edge Function orqali.
    Bot naqd service_role ni ko'rmaydi.
    """
    payload = {"action": action, **kwargs}
    headers = {
        "x-bot-secret": BOT_SECRET,
        "Content-Type":  "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(EDGE_URL, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


# =========================================================================
# 4. OCR (Base64 + anti-blok)
# =========================================================================
def get_text_from_api(file_bytes: bytes, file_type: str = 'jpg') -> str:
    b64 = base64.b64encode(file_bytes).decode('utf-8')
    prefix = "data:application/pdf;base64," if file_type == 'pdf' else "data:image/jpeg;base64,"
    payload = {
        'apikey':           OCR_API_KEY,
        'base64Image':      prefix + b64,
        'language':         'eng',
        'isOverlayRequired': 'false',
        'OCREngine':        '2',
    }
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36'}
    try:
        r = requests.post('https://api.ocr.space/parse/image',
                          data=payload, headers=headers, timeout=40)
        r.raise_for_status()
        result = r.json()
        if result.get('ParsedResults'):
            return " ".join(res.get('ParsedText', '') for res in result['ParsedResults'])
        return ""
    except Exception as e:
        logging.error(f"OCR Error: {e}")
        return "ERROR_API"


# =========================================================================
# 5. TARIF KUNLARINI HISOBLASH
# =========================================================================
def calculate_tariff_days(text: str) -> int:
    raw_numbers = re.findall(r'\b\d{2}[.,\s]?\d{3}\b|\b\d{5,6}\b', text)
    for num_str in raw_numbers:
        try:
            n = int(re.sub(r'\D', '', num_str))
            if 15000 <= n <= 15500:  return 7
            if 33000 <= n <= 34000:  return 31
            if 80000 <= n <= 95000:  return 93
        except Exception:
            continue
    return 7


# =========================================================================
# 6. USER YARATISH / YANGILASH (Edge Function orqali)
# =========================================================================
async def create_user_auto(email: str, tariff_days: int,
                            message: Message, state: FSMContext):
    try:
        result = await call_bot_manager(
            action="create_or_update_user",
            email=email,
            tariff_days=tariff_days,
        )

        if not result.get("success"):
            raise Exception(result.get("error", "Noma'lum xatolik"))

        password    = result["password"]
        is_new_user = result["is_new_user"]

        if is_new_user:
            await message.answer(
                f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
                f"Profilingiz yaratildi:\n"
                f"📧 <b>Login:</b> <code>{email}</code>\n"
                f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
                f"Saytga kirib foydalanishingiz mumkin."
            )
        else:
            await message.answer(
                f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
                f"Mavjud profilingizga PRO tarif qo'shildi!\n"
                f"📧 <b>Login:</b> <code>{email}</code>\n"
                f"🔑 <b>Parol:</b> <code>{password}</code>\n\n"
                f"Saytga <b>Google orqali</b> YOKI yuqoridagi "
                f"<b>Login va Parol</b> bilan kira olasiz."
            )

        await asyncio.sleep(0.5)
        await message.answer("👇 <b>Yopiq kanalimiz:</b>\nhttps://t.me/+G5z5KWbXBZ04OTAy")
        await state.set_state(PaymentState.completed)

    except Exception as e:
        logging.error(f"create_user_auto error: {e}")
        await message.answer(f"❌ Xatolik yuz berdi. Adminga murojaat qiling.\n<code>{e}</code>")


# =========================================================================
# 7. MATN HANDLERI
# =========================================================================
@dp.message(F.text)
@dp.business_message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text          = message.text.lower()
    email_match   = re.search(EMAIL_REGEX, message.text)
    current_state = await state.get_state()

    # Email keldi
    if email_match:
        email = email_match.group(0)
        if current_state == PaymentState.waiting_for_email:
            data         = await state.get_data()
            tariff_days  = data.get("tariff_days", 7)
            await message.answer("📧 Email qabul qilindi. User ochilmoqda...")
            await create_user_auto(email, tariff_days, message, state)
        else:
            await state.update_data(email=email)
            if current_state != PaymentState.completed:
                await state.set_state(PaymentState.waiting_for_check)
            await message.answer(
                f"📧 Email ({email}) saqlandi. Endi to'lov cheki rasmini yuboring."
            )
        return

    # Narx so'rovi
    if any(w in text for w in PRICE_KEYWORDS):
        await message.answer(
            "💰 <b>Avtotest Pro narxlari:</b>\n\n"
            "• 1 haftalik: 15,000 so'm\n"
            "• 1 oylik: 33,000 so'm\n"
            "• 3 oylik: 83,000 so'm"
        )
        return

    # Birinchi murojaat
    if current_state is None:
        await message.answer(
            "Assalomu alaykum! Pro versiyani olish uchun ushbu karta raqamiga  to'lov qiling:\n\n"
            "💳 <b>Karta:</b> <code>5614684708939507</code>\n"
            "👤 <b>Eldor Atajanov</b>\n\n"
            "❗️ To'lovdan so'ng <b>Chek</b> va <b>Emailni</b> yuboring."
        )
        await asyncio.sleep(0.5)
        await message.answer("Boshqa masalada bo'lsa admin javob beradi iltimos kuting.")
        await state.set_state(PaymentState.waiting_for_check)


# =========================================================================
# 8. FAYL / RASM HANDLERI
# =========================================================================
@dp.message(F.photo | F.document)
@dp.business_message(F.photo | F.document)
async def handle_files(message: Message, state: FSMContext):
    msg        = await message.answer("⏳ Chek tekshirilmoqda, iltimos kuting...")
    file_bytes = None
    file_type  = 'jpg'

    try:
        if message.photo:
            file       = await bot.get_file(message.photo[-1].file_id)
            file_bytes = (await bot.download_file(file.file_path)).read()
        elif message.document and message.document.file_name.lower().endswith('.pdf'):
            file       = await bot.get_file(message.document.file_id)
            file_bytes = (await bot.download_file(file.file_path)).read()
            file_type  = 'pdf'

        if not file_bytes:
            await msg.edit_text("⚠️ Rasm yoki PDF formatida yuboring.")
            return

        full_text = await asyncio.to_thread(get_text_from_api, file_bytes, file_type)

        if full_text == "ERROR_API":
            await msg.edit_text(
                "❌ OCR serveri bilan ulanishda muammo (API xatosi). "
                "Iltimos, bir ozdan so'ng qayta yuboring."
            )
            return

        is_valid = any(w in full_text.upper() for w in VALID_KEYWORDS)

        if is_valid:
            calculated_days = calculate_tariff_days(full_text)
            data  = await state.get_data()
            email = data.get("email")

            if email:
                await msg.edit_text("✅ Chek tasdiqlandi! Profil yaratilmoqda...")
                await create_user_auto(email, calculated_days, message, state)
            else:
                await state.update_data(tariff_days=calculated_days)
                await state.set_state(PaymentState.waiting_for_email)
                await msg.edit_text(
                    "✅ Chek qabul qilindi! Endi <b>Email manzilingizni</b> yozib yuboring."
                )
        else:
            await msg.edit_text(
                "⚠️ Chekni o'qib bo'lmadi yoki xato chek yuborildi. "
                "Iltimos, tiniqroq rasm yuboring."
            )

    except Exception as e:
        logging.error(f"handle_files error: {e}")
        await msg.edit_text("❌ Tizimda xatolik yuz berdi.")


# =========================================================================
# 9. ISHGA TUSHIRISH
# =========================================================================
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
