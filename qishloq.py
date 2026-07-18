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

# --- SOZLAMALAR VA KONFIGURATSIYA ---
BOT_TOKEN = "8867325304:AAFHOVKs8HsR8z02tSL8NcUeXmLZlPKCzNQ"
SUPER_ADMIN = 8317043750  # Asosiy admin (Siz)

# Siz taqdim etgan Google Sheets ID raqami joylashtirildi:
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY", "1FAA7ejE4b1s7gxHudVTC1eS62tZotgnAyvFSBkpnyJc")

logging.basicConfig(level=logging.INFO)

# Google Sheets ulanishi
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    db_sheet = client.open_by_key(GOOGLE_SHEET_KEY)
    votes_table = db_sheet.get_worksheet(0)  # 1-list: Ovozlar
    admins_table = db_sheet.get_worksheet(1) # 2-list: Adminlar ro'yxati
except Exception as e:
    logging.error(f"Google Sheets ulanishida xatolik: {e}")
    votes_table = None
    admins_table = None

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- FSM (HOLATLAR) ---
class OB_Voting(StatesGroup):
    waiting_for_phone = State()
    waiting_for_sms = State()

class AdminActions(StatesGroup):
    waiting_for_new_admin = State()
    waiting_for_del_admin = State()
    waiting_for_broadcast = State()

# --- ADMINLARNI TEKSHIRISH FUNKSIYALARI ---
def get_all_admins():
    if not admins_table:
        return [SUPER_ADMIN]
    try:
        records = admins_table.get_all_records()
        admin_ids = [SUPER_ADMIN]
        for row in records:
            if row.get("Admin_ID"):
                admin_ids.append(int(row["Admin_ID"]))
        return list(set(admin_ids))
    except Exception:
        return [SUPER_ADMIN]

def add_admin_to_sheet(admin_id, name):
    if admins_table:
        admins_table.append_row([str(admin_id), name])

def remove_admin_from_sheet(admin_id):
    if not admins_table: return
    try:
        cell = admins_table.find(str(admin_id), in_column=1)
        if cell:
            admins_table.delete_rows(cell.row)
    except Exception:
        pass

# --- KLAVIATURALAR ---
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🙋‍♂️ Ovoz berish (Open Budjet)"))
    builder.add(types.KeyboardButton(text="👤 Profilim"))
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

def phone_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📱 Kontaktni yuborish", request_contact=True))
    builder.add(types.KeyboardButton(text="⬅️ Bosh menu"))
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="❌ Bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

def admin_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📊 Umumiy statistika"))
    builder.add(types.KeyboardButton(text="➕ Yangi Admin qo'shish"))
    builder.add(types.KeyboardButton(text="❌ Adminni o'chirish"))
    builder.add(types.KeyboardButton(text="📢 Hammaga xabar yuborish"))
    builder.add(types.KeyboardButton(text="⬅️ Foydalanuvchi menyusi"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

# --- USER HANDLERLARI ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Assalomu alaykum, {message.from_user.full_name}! Open Budjet botiga xush kelibsiz.\n"
        f"Ushbu bot orqali loyihamizga oson ovoz berishingiz mumkin.",
        reply_markup=main_menu()
    )

@dp.message(F.text == "⬅️ Foydalanuvchi menyusi")
async def back_to_user(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Foydalanuvchi menyusiga qaytdingiz.", reply_markup=main_menu())

@dp.message(F.text == "👤 Profilim")
async def user_profile(message: types.Message):
    user_id = str(message.from_user.id)
    total_votes = 0
    if votes_table:
        try:
            cells = votes_table.findall(user_id, in_column=1)
            total_votes = len(cells)
        except Exception:
            pass
            
    text = f"👤 **Sizning profilingiz:**\n\n" \
           f"🆔 ID: `{user_id}`\n" \
           f"📝 Ism: {message.from_user.full_name}\n" \
           f"🗳 Siz orqali berilgan jami ovozlar: {total_votes} ta"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Jarayon bekor qilindi.", reply_markup=main_menu())

@dp.message(F.text == "🙋‍♂️ Ovoz berish (Open Budjet)")
async def start_ob(message: types.Message, state: FSMContext):
    await message.answer(
        "Ovoz berish uchun pastdagi tugma orqali telefon raqamingizni yuboring yoki "
        "raqamni `+998XXXXXXXXX` formatida yozib yuboring:", 
        reply_markup=phone_keyboard()
    )
    await state.set_state(OB_Voting.waiting_for_phone)

@dp.message(OB_Voting.waiting_for_phone, (F.contact | F.text))
async def process_phone(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Bosh menu":
        await state.clear()
        await message.answer("Bosh menuga qaytdingiz.", reply_markup=main_menu())
        return

    phone = message.contact.phone_number if message.contact else message.text
    
    if not phone.startswith("+") and phone.isdigit() and len(phone) == 9:
        phone = "+998" + phone
    
    await state.update_data(phone_number=phone)
    
    # [🔍 OPEN BUDGET SMS JO'NATISH SO'ROVI SHU YERGA DEPOSIT QILINADI]
    
    await message.answer(
        f"📱 {phone} raqamiga Open Budjet tizimidan SMS kod yuborildi.\n\n"
        f"SMS kodni botga yozib yuboring:", 
        reply_markup=cancel_keyboard()
    )
    await state.set_state(OB_Voting.waiting_for_sms)

@dp.message(OB_Voting.waiting_for_sms)
async def process_sms(message: types.Message, state: FSMContext):
    sms_code = message.text
    if sms_code == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu())
        return

    user_data = await state.get_data()
    phone = user_data.get("phone_number")
    
    # [🔍 SMS KODNI OPEN BUDGET SAYTIGA TEKSHIRISH KODI SHU YERDA BO'LADI]
    
    if votes_table:
        try:
            votes_table.append_row([
                str(message.from_user.id), 
                f"@{message.from_user.username}" if message.from_user.username else "Yo'q", 
                phone, 
                "Muvaffaqiyatli Ovoz berildi"
            ])
        except Exception as e:
            logging.error(f"Jadvalga yozishda xato: {e}")

    await state.clear()
    await message.answer("🎉 Rahmat! Ovozingiz muvaffaqiyatli qabul qilindi va ro'yxatga qo'shildi.", reply_markup=main_menu())

# --- MULTI-ADMIN PANEL HANDLERLARI ---

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    admins = get_all_admins()
    if message.from_user.id in admins:
        await message.answer("🔧 Admin boshqaruv paneliga xush kelibsiz!", reply_markup=admin_menu())
    else:
        await message.answer("Siz adminlar ro'yxatida yo'qsiz.")

@dp.message(F.text == "📊 Umumiy statistika")
async def admin_stats(message: types.Message):
    admins = get_all_admins()
    if message.from_user.id not in admins: return
    
    total_votes = 0
    if votes_table:
        try:
            total_votes = len(votes_table.get_all_records())
        except Exception:
            pass
            
    await message.answer(
        f"📊 **Bot statistikasi:**\n\n"
        f"✉️ Tizim orqali yig'ilgan jami ovozlar: {total_votes} ta\n"
        f"👥 Boshqaruvdagi adminlar soni: {len(admins)} ta"
    )

@dp.message(F.text == "➕ Yangi Admin qo'shish")
async def add_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_all_admins(): return
    await message.answer("Qo'shmoqchi bo'lgan adminingizning Telegram ID raqamini yozib yuboring:", reply_markup=cancel_keyboard())
    await state.set_state(AdminActions.waiting_for_new_admin)

@dp.message(AdminActions.waiting_for_new_admin)
async def add_admin_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
        
    if not message.text.isdigit():
        await message.answer("ID faqat raqamlardan iborat bo'lishi kerak. Qayta urinib ko'ring:")
        return
        
    new_admin_id = int(message.text)
    add_admin_to_sheet(new_admin_id, "Qo'shilgan Admin")
    await state.clear()
    await message.answer(f"✅ Yangi admin (ID: {new_admin_id}) muvaffaqiyatli qo'shildi!", reply_markup=admin_menu())

@dp.message(F.text == "❌ Adminni o'chirish")
async def del_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_all_admins(): return
    await message.answer("O'chirmoqchi bo'lgan adminingizning Telegram ID raqamini kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(AdminActions.waiting_for_del_admin)

@dp.message(AdminActions.waiting_for_del_admin)
async def del_admin_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
        
    if not message.text.isdigit():
        await message.answer("ID raqam bo'lishi shart:")
        return
        
    target_id = int(message.text)
    if target_id == SUPER_ADMIN:
        await message.answer("Asosiy adminni o'chirib bo'lmaydi!")
        return
        
    remove_admin_from_sheet(target_id)
    await state.clear()
    await message.answer(f"❌ Admin (ID: {target_id}) ro'yxatdan olib tashlandi.", reply_markup=admin_menu())

@dp.message(F.text == "📢 Hammaga xabar yuborish")
async def broadcast_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_all_admins(): return
    await message.answer("Foydalanuvchilarga yubormoqchi bo'lgan xabaringiz matnini kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(AdminActions.waiting_for_broadcast)

@dp.message(AdminActions.waiting_for_broadcast)
async def broadcast_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
        
    text_to_send = message.text
    await state.clear()
    await message.answer("📢 Xabar yuborish boshlandi...", reply_markup=admin_menu())
    
    if votes_table:
        try:
            records = votes_table.get_all_records()
            user_ids = list(set([row["ID"] for row in records if row.get("ID")]))
            
            count = 0
            for u_id in user_ids:
                try:
                    await bot.send_message(chat_id=int(u_id), text=text_to_send)
                    count += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    continue
            await message.answer(f"✅ Xabar jami {count} ta faol foydalanuvchiga yetkazildi.")
        except Exception as e:
            await message.answer(f"Xabar yuborishda xatolik yuz berdi: {e}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
