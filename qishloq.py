import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- KONFIGURATSIYA ---
BOT_TOKEN = "8867325304:AAFHOVKs8HsR8z02tSL8NcUeXmLZlPKCzNQ"
ADMIN_IDS = [8317043750]  # Siz taqdim etgan Admin ID

# Server (Railway/Render) uchun muhit o'zgaruvchisidan Google Sheet kalitini olish
# Agar topilmasa, pastdagi standart ID ishlatiladi (o'z jadvallaringiz ID sini yozib qo'ying)
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY", "https://docs.google.com/spreadsheets/d/1FAA7ejE4b1s7gxHudVTC1eS62tZotgnAyvFSBkpnyJc/edit?gid=0#gid=0")

# Loggingni sozlash
logging.basicConfig(level=logging.INFO)

# Google Sheets ulanishi
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_KEY).sheet1
except Exception as e:
    logging.error(f"Google Sheets bilan bog'lanishda xatolik: {e}")
    sheet = None

# Bot va Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# FSM (Ro'yxatdan o'tish holatlari)
class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()

# --- KLAVIATURALAR ---
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🗳 Ovoz berish"))
    builder.add(types.KeyboardButton(text="👤 Profil"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def phone_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True))
    return builder.as_markup(resize_keyboard=True)

def voting_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Nomzod #1", callback_data="vote_nomzod_1"))
    builder.add(types.InlineKeyboardButton(text="Nomzod #2", callback_data="vote_nomzod_2"))
    builder.add(types.InlineKeyboardButton(text="Nomzod #3", callback_data="vote_nomzod_3"))
    builder.adjust(1)
    return builder.as_markup()

def admin_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📊 Statistika"))
    builder.add(types.KeyboardButton(text="⬅️ Chiqish"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

# --- GOOGLE SHEETS FUNKSIYALARI ---
def user_exists(user_id):
    if not sheet: return None
    try:
        cell = sheet.find(str(user_id), in_column=1)
        return cell.row if cell else None
    except gspread.exceptions.CellNotFound:
        return None

def register_user(user_id, username, name, phone):
    if sheet and not user_exists(user_id):
        sheet.append_row([str(user_id), f"@{username}" if username else "Yo'q", name, phone, "Ovoz bermagan"])

def update_vote(user_id, vote_choice):
    if not sheet: return
    row = user_exists(user_id)
    if row:
        sheet.update_cell(row, 5, vote_choice) # 5-ustun Ovoz berish qismi

def get_user_data(user_id):
    if not sheet: return None
    row = user_exists(user_id)
    if row:
        values = sheet.row_values(row)
        return {
            "name": values[2] if len(values) > 2 else "Kiritilmagan",
            "phone": values[3] if len(values) > 3 else "Kiritilmagan",
            "vote": values[4] if len(values) > 4 else "Ovoz bermagan"
        }
    return None

# --- HANDLERLAR ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_exists(user_id):
        await message.answer("Xush kelibsiz! Quyidagi menudan foydalanishingiz mumkin:", reply_markup=main_menu())
    else:
        await message.answer("Assalomu alaykum! Botdan foydalanish uchun ro'yxatdan o'ting.\n\nIsm va familiyangizni kiriting:")
        await state.set_state(Registration.waiting_for_name)

@dp.message(Registration.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Rahmat. Endi pastdagi tugmani bosib telefon raqamingizni yuboring:", reply_markup=phone_keyboard())
    await state.set_state(Registration.waiting_for_phone)

@dp.message(Registration.waiting_for_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = data.get("full_name")
    phone = message.contact.phone_number
    
    register_user(user_id, username, full_name, phone)
    
    await state.clear()
    await message.answer("Ro'yxatdan muvaffaqiyatli o'tdingiz!", reply_markup=main_menu())

@dp.message(F.text == "👤 Profil")
async def view_profile(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    if user_data:
        text = f"👤 **Sizning profilingiz:**\n\n" \
               f"📝 Ism: {user_data['name']}\n" \
               f"📞 Tel: {user_data['phone']}\n" \
               f"🗳 Tanlov: {user_data['vote']}"
        await message.answer(text, parse_mode="Markdown")
    else:
        await message.answer("Ma'lumot topilmadi. Qaytadan /start bosing.")

@dp.message(F.text == "🗳 Ovoz berish")
async def start_voting(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    if user_data and user_data['vote'] != "Ovoz bermagan":
        await message.answer(f"Siz allaqachon ovoz bergansiz! Tanlovingiz: {user_data['vote']}")
    else:
        await message.answer("O'zingizga ma'qul nomzodga ovoz bering:", reply_markup=voting_keyboard())

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    user_data = get_user_data(callback.from_user.id)
    if user_data and user_data['vote'] != "Ovoz bermagan":
        await callback.answer("Siz oldin ovoz bergansiz!", show_alert=True)
        return

    choice = callback.data.split("_")[-1]
    nomzod_name = f"Nomzod #{choice}"
    
    update_vote(callback.from_user.id, nomzod_name)
    
    await callback.message.edit_text(f"Rahmat! Siz muvaffaqiyatli ovoz berdingiz: {nomzod_name}")
    await callback.answer()

# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Admin panelga xush kelibsiz!", reply_markup=admin_menu())
    else:
        await message.answer("Siz admin emassiz.")

@dp.message(F.text == "📊 Statistika")
async def view_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    if not sheet:
        await message.answer("Google Sheets bilan aloqa yo'q.")
        return
    all_records = sheet.get_all_records()
    total_users = len(all_records)
    await message.answer(f"📊 **Bot statistikasi:**\n\nJami ro'yxatdan o'tganlar: {total_users} ta foydalanuvchi.")

@dp.message(F.text == "⬅️ Chiqish")
async def exit_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Foydalanuvchi menyusiga qaytdingiz.", reply_markup=main_menu())

# Botni ishga tushirish
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
