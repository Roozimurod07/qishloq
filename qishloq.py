import asyncio
import logging
import json
import os
import io
import re
import sqlite3
from datetime import datetime
import pytz  
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import gspread
import openpyxl 
from oauth2client.service_account import ServiceAccountCredentials

# --- SOZLAMALAR ---
BOT_TOKEN = "8867325304:AAFHOVKs8HsR8z02tSL8NcUeXmLZlPKCzNQ"
SUPER_ADMINS = [8317043750]  # Super Adminlar

GOOGLE_SHEET_NAME = "Qorabayir"  
UZ_TZ = pytz.timezone('Asia/Tashkent')

DB_PATH = "mailing_users.db"

# --- LOGGING VA BOT INITIALIZATSIYASI ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

claimed_users = {}
claimed_admin_names = {}
admin_message_ids = {}

# --- ASYNC QUEUE (GOOGLE SHEETS NAVBATI) ---
sheets_queue = asyncio.Queue()

# --- SQLITE BAZA STRUKTURASI ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, joined_at TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS extra_admins (admin_id INTEGER PRIMARY KEY)")
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('start_time', '07:00')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('end_time', '23:00')")
    cursor.execute("CREATE TABLE IF NOT EXISTS admin_stats (admin_id INTEGER, action_type TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (admin_id, action_type))")
    cursor.execute("CREATE TABLE IF NOT EXISTS voted_phones (phone TEXT PRIMARY KEY, status TEXT, added_at TEXT)")
    conn.commit()
    conn.close()

# --- BAZA BILAN ISHLASH FUNKSIYALARI ---
def add_user_to_db(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        now = datetime.now(UZ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR IGNORE INTO users (user_id, joined_at) VALUES (?, ?)", (user_id, now))
        conn.commit(); conn.close()
    except Exception as e: print(f"❌ SQLite xatolik: {e}")

def is_phone_voted_local(phone):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM voted_phones WHERE phone = ?", (phone,))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        print(f"❌ SQLite tekshiruvida xatolik: {e}")
        return False

def add_voted_phone_local(phone, status="Muvaffaqiyatli"):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        now = datetime.now(UZ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR REPLACE INTO voted_phones (phone, status, added_at) VALUES (?, ?, ?)", (phone, status, now))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"❌ SQLite saqlashda xatolik: {e}")

def get_all_db_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_extra_admins():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT admin_id FROM extra_admins")
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins

def add_extra_admin(admin_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO extra_admins (admin_id) VALUES (?)", (admin_id,))
    conn.commit(); conn.close()
    return True

def remove_extra_admin(admin_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM extra_admins WHERE admin_id = ?", (admin_id,))
    conn.commit(); conn.close()
    return True

def get_db_setting(key, default):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def set_db_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit(); conn.close()
    return True

def increment_admin_stat(admin_id, action_type):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO admin_stats (admin_id, action_type, count) VALUES (?, ?, 1) ON CONFLICT(admin_id, action_type) DO UPDATE SET count = count + 1", (admin_id, action_type))
    conn.commit(); conn.close()

def get_admin_stats_text():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT admin_id, action_type, count FROM admin_stats")
    rows = cursor.fetchall()
    conn.close()
    if not rows: return "Adminlar ish statistikasi: Hozircha ma'lumot yo'q."
    data = {}
    for r in rows:
        a_id, act, cnt = r
        if a_id not in data: data[a_id] = {}
        data[a_id][act] = cnt
    text = "📊 <b>Adminlar va Operatorlar Ish Statistikasi:</b>\n\n"
    for a_id, acts in data.items():
        text += f"👤 Admin ID: <code>{a_id}</code>\n"
        text += f" ├ Band qilingan raqamlar: {acts.get('claim', 0)} ta\n"
        text += f" ├ Tasdiqlangan (Muvaffaqiyatli): {acts.get('success', 0)} ta\n"
        text += f" └ Rad etilgan (Avval ovoz bergan): {acts.get('already', 0)} ta\n\n"
    return text

init_db()

def get_all_admins():
    return list(set(SUPER_ADMINS + get_extra_admins()))

def is_working_hours():
    now_uz = datetime.now(UZ_TZ).time()
    start_time = datetime.strptime(get_db_setting('start_time', '07:00'), "%H:%M").time()
    end_time = datetime.strptime(get_db_setting('end_time', '23:00'), "%H:%M").time()
    if start_time <= end_time: return start_time <= now_uz <= end_time
    return now_uz >= start_time or now_uz <= end_time

# --- GOOGLE SHEETS ---
def get_google_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    google_creds_env = os.getenv("GOOGLE_CREDS")
    if google_creds_env:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(google_creds_env), scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("open.json", scope)
    return gspread.authorize(creds).open(GOOGLE_SHEET_NAME).sheet1

def _sync_log_to_sheets(payload):
    """Google Sheets'ga haqiqiy yozish funksiyasi"""
    try:
        user_id = payload.get("user_id")
        full_name = payload.get("full_name", "")
        username = payload.get("username", "")
        phone = payload.get("phone", "")
        code = payload.get("code", "")
        status = payload.get("status", "")
        admin_name = payload.get("admin_name", "")

        sheet = get_google_sheet()
        all_records = sheet.get_all_values()
        now = datetime.now(UZ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        username_str = f"@{username}" if username else "Mavjud emas"
        
        row_index = -1
        for idx, row in enumerate(all_records):
            if len(row) >= 4 and str(row[0]) == str(user_id) and str(row[3]) == str(phone):
                row_index = idx + 1
                break
        
        if row_index != -1:
            if code: sheet.update_cell(row_index, 5, str(code))
            if status: sheet.update_cell(row_index, 6, status)
            sheet.update_cell(row_index, 7, now)
            if admin_name: sheet.update_cell(row_index, 8, admin_name)
        else:
            sheet.append_row([str(user_id), full_name, username_str, str(phone), str(code), status, now, admin_name])
        print(f"✅ Sheets'ga muvaffaqiyatli yozildi: {phone} -> {status}")
    except Exception as e:
        print(f"❌ Google Sheets yozishda XATOLIK: {e}")

async def sheets_worker():
    """Fonda navbat bilan Google Sheets'ga yuboruvchi mexanizm"""
    print("🚀 Google Sheets Workers fonda ishga tushdi...")
    while True:
        payload = await sheets_queue.get()
        try:
            await asyncio.to_thread(_sync_log_to_sheets, payload)
        except Exception as e:
            print(f"❌ Worker error: {e}")
        finally:
            sheets_queue.task_done()
            await asyncio.sleep(0.3)

def log_to_sheets(user_id, full_name="", username="", phone="", code="", status="", admin_name=""):
    """Ma'lumotni darhol fondagi navbatga qo'shadi"""
    payload = {
        "user_id": user_id, "full_name": full_name, "username": username,
        "phone": phone, "code": code, "status": status, "admin_name": admin_name
    }
    sheets_queue.put_nowait(payload)

# --- FSM STATES ---
class VoteState(StatesGroup):
    waiting_for_name = State()       
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_screenshot = State()
    waiting_for_admin_check = State()  

class AdminState(StatesGroup):
    waiting_for_broadcast_msg = State()
    waiting_for_new_admin = State()
    waiting_for_del_admin = State()
    waiting_for_work_hours = State()

# --- KLAVIATURALAR ---
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🗳 Ovoz berish")
    builder.button(text="🙋‍♂️ Yordam")
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)

def admin_menu(user_id):
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Jonli Statistika")
    builder.button(text="👥 Adminlar Ishi")
    if user_id in SUPER_ADMINS:
        builder.button(text="📥 Excel Hisobot (.xlsx)")
        builder.button(text="📢 Xabar yuborish (Mailing)") 
        builder.button(text="⚙️ Ish Vaqtini Sozlash")
        builder.button(text="➕ Operator Qo'shish")
        builder.button(text="➖ Operator O'chirish")
    builder.button(text="⬅️ Bosh menyu")
    if user_id in SUPER_ADMINS: builder.adjust(2, 2, 1, 2)
    else: builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)

def phone_share_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📱 Telefon raqamni yuborish", request_contact=True)
    builder.button(text="❌ Bekor qilish")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Bekor qilish")
    return builder.as_markup(resize_keyboard=True)

# --- BUYRUQLAR INTERFEYSI ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()  
    user_id = message.from_user.id
    
    add_user_to_db(user_id)

    if user_id in get_all_admins():
        await message.answer("🔑 <b>Admin panelga xush kelibsiz!</b>", reply_markup=admin_menu(user_id), parse_mode="HTML")
    else:
        await message.answer("👋 Assalomu alaykum! Open Budget ovoz berish botiga xush kelibsiz.\nQORABAYIR MFYga o'z ovozingizni berib loyihamiz rivojiga hissa qo'shing.", reply_markup=main_menu())

@dp.message(F.text == "⬅️ Bosh menyu")
async def back_to_main(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    if user_id in get_all_admins(): await message.answer("Admin menyusi:", reply_markup=admin_menu(user_id))
    else: await message.answer("Bosh menyuga qaytildi.", reply_markup=main_menu())

# --- STATISTIKA VA ADMIN BOSHQARUVLARI ---
@dp.message(F.text == "📊 Jonli Statistika")
async def show_detailed_stats(message: types.Message):
    if message.from_user.id not in get_all_admins(): return
    waiting_msg = await message.answer("🔄 Statistika hisoblanmoqda...")
    try:
        db_users = len(get_all_db_users())
        all_rows = await asyncio.to_thread(lambda: get_google_sheet().get_all_values()[1:])
        success = sum(1 for r in all_rows if len(r) >= 6 and "Muvaffaqiyatli" in r[5])
        rejected = sum(1 for r in all_rows if len(r) >= 6 and ("Avval" in r[5] or "rad" in r[5].lower()))
        
        stats_text = f"📊 **Jonli Real-Vaqt Statistikasi**\n\n👤 Bot a'zolari: {db_users}\n📥 Jami arizalar: {len(all_rows)}\n🟢 Muvaffaqiyatli: {success}\n🔴 Rad etilganlar: {rejected}"
        await waiting_msg.delete()
        await message.answer(stats_text, parse_mode="Markdown")
    except Exception as e: await waiting_msg.edit_text(f"❌ Xatolik: {e}")

@dp.message(F.text == "👥 Adminlar Ishi")
async def show_admin_work_stats(message: types.Message):
    if message.from_user.id not in get_all_admins(): return
    await message.answer(get_admin_stats_text(), parse_mode="HTML")

@dp.message(F.text == "⚙️ Ish Vaqtini Sozlash")
async def set_hours_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPER_ADMINS: return
    await message.answer(f"⚙️ Format: `08:00-22:00` shaklida kiriting:", parse_mode="Markdown")
    await state.set_state(AdminState.waiting_for_work_hours)

@dp.message(AdminState.waiting_for_work_hours)
async def set_hours_finish(message: types.Message, state: FSMContext):
    await state.clear()
    text = message.text.strip()
    if re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]-([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", text):
        sh, eh = text.split("-")
        set_db_setting('start_time', sh); set_db_setting('end_time', eh)
        await message.answer(f"✅ Ish vaqti o'rnatildi: {sh} - {eh}", reply_markup=admin_menu(message.from_user.id))
    else:
        await message.answer("❌ Format xato. Misol: 07:00-23:00", reply_markup=admin_menu(message.from_user.id))

@dp.message(F.text == "➕ Operator Qo'shish")
async def add_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id in SUPER_ADMINS:
        await message.answer("Yangi operatorning Telegram ID raqamini kiriting:")
        await state.set_state(AdminState.waiting_for_new_admin)

@dp.message(AdminState.waiting_for_new_admin)
async def add_admin_finish(message: types.Message, state: FSMContext):
    await state.clear()
    if message.text.isdigit() and add_extra_admin(int(message.text)):
        await message.answer("✅ Operator ro'yxatga qo'shildi.", reply_markup=admin_menu(message.from_user.id))
    else: await message.answer("❌ ID xato.", reply_markup=admin_menu(message.from_user.id))

@dp.message(F.text == "➖ Operator O'chirish")
async def del_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPER_ADMINS: return
    text = "O'chirish uchun ID yuboring:\n" + "\n".join([f"• <code>{a}</code>" for a in get_extra_admins()])
    await message.answer(text, parse_mode="HTML"); await state.set_state(AdminState.waiting_for_del_admin)

@dp.message(AdminState.waiting_for_del_admin)
async def del_admin_finish(message: types.Message, state: FSMContext):
    await state.clear()
    if message.text.isdigit() and remove_extra_admin(int(message.text)):
        await message.answer("✅ Operator o'chirildi.", reply_markup=admin_menu(message.from_user.id))
    else: await message.answer("❌ Topilmadi.", reply_markup=admin_menu(message.from_user.id))

# --- MAILING VA EXCEL REPORT ---
@dp.message(F.text == "📢 Xabar yuborish (Mailing)")
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id in SUPER_ADMINS:
        await message.answer("Tarqatiladigan xabarni kiriting:"); await state.set_state(AdminState.waiting_for_broadcast_msg)

@dp.message(AdminState.waiting_for_broadcast_msg)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    await state.clear()
    s_msg = await message.answer("📢 Tarqatish boshlandi...")
    sc, fc = 0, 0
    all_users = await asyncio.to_thread(lambda: get_google_sheet().get_all_values()[1:])
    
    target_users = set()
    for row in all_users:
        if row and row[0].isdigit():
            target_users.add(int(row[0]))
            
    for db_uid in get_all_db_users():
        target_users.add(int(db_uid))
        
    for u_id in target_users:
        try:
            await bot.send_message(chat_id=u_id, text=message.text)
            sc += 1; await asyncio.sleep(0.05)
        except Exception: 
            fc += 1
            
    await s_msg.edit_text(f"✅ Tugadi.\n🟢 Yetkazildi (Adminlar va foydalanuvchilar): {sc}\n🔴 Yetkazilmadi: {fc}")

@dp.message(F.text == "📥 Excel Hisobot (.xlsx)")
async def send_excel_report(message: types.Message):
    if message.from_user.id not in SUPER_ADMINS: return
    waiting_msg = await message.answer("🔄 Yuklanmoqda...")
    try:
        all_data = await asyncio.to_thread(lambda: get_google_sheet().get_all_values())
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Hisobot"
        for row in all_data: ws.append(row)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        await waiting_msg.delete()
        await message.answer_document(document=types.BufferedInputFile(buf.getvalue(), filename="Hisobot.xlsx"), caption="📊 Barcha arizalar hisoboti.")
    except Exception as e: await waiting_msg.edit_text(f"❌ Xatolik: {e}")

@dp.message(F.text == "🙋‍♂️ Yordam")
async def process_help(message: types.Message):
    await message.answer("<b>🙋‍♂️ Yordam markazi:</b>", parse_mode="HTML", reply_markup=InlineKeyboardBuilder().button(text="✍️ Operator", url="https://t.me/soibnazarov07").as_markup())

# =====================================================================
# 🔥 OVOZ BERISH JARAYONI 🔥
# =====================================================================

@dp.message(F.text == "🗳 Ovoz berish")
async def start_voting(message: types.Message, state: FSMContext):
    if not is_working_hours() and message.from_user.id not in SUPER_ADMINS:
        await message.answer(f"🌙 Bot hozirda yopiq! Ish vaqti: {get_db_setting('start_time', '07:00')} - {get_db_setting('end_time', '23:00')}")
        return
    await state.clear()
    await message.answer("👤 Iltimos, ism va familiyangizni kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(VoteState.waiting_for_name)

@dp.message(F.text == "❌ Bekor qilish", VoteState.waiting_for_name)
async def cancel_at_name(message: types.Message, state: FSMContext):
    await state.clear(); await message.answer("Bekor qilindi.", reply_markup=main_menu())

@dp.message(VoteState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    user_name = message.text.strip()
    if len(user_name) < 3 or user_name == "❌ Bekor qilish":
        await message.answer("⚠️ Iltimos, ism va familiyangizni to'liq kiriting:"); return

    await state.update_data(full_name=user_name)
    await message.answer("📱 Rahmat! Endi telefon raqamingizni yuboring yoki kiriting:", reply_markup=phone_share_keyboard())
    await state.set_state(VoteState.waiting_for_phone)

@dp.message(F.text == "❌ Bekor qilish", VoteState.waiting_for_phone)
async def cancel_voting(message: types.Message, state: FSMContext):
    await state.clear(); await message.answer("Bekor qilindi.", reply_markup=main_menu())

@dp.message(VoteState.waiting_for_phone, F.contact | F.text)
async def process_phone(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear(); await message.answer("Bekor qilindi.", reply_markup=main_menu()); return

    phone = message.contact.phone_number if message.contact else message.text.strip().replace(" ", "")
    if re.match(r"^998\d{9}$", phone): phone = "+" + phone
    elif re.match(r"^\d{9}$", phone): phone = "+998" + phone
    if not re.match(r"^\+998\d{9}$", phone):
        await message.answer("⚠️ Noto'g'ri format. Qayta kiriting:"); return

    if is_phone_voted_local(phone):
        await message.answer("❌ Ushbu raqamdan avval ovoz berilgan yoki jarayon yakunlanmagan!", reply_markup=main_menu())
        await state.clear()
        return

    user_id, username = message.from_user.id, message.from_user.username
    data = await state.get_data()
    full_name = data.get("full_name")  

    await state.update_data(phone=phone, username=username)
    log_to_sheets(user_id=user_id, full_name=full_name, username=username, phone=phone, status="Raqam kiritildi")

    builder = InlineKeyboardBuilder().button(text="✅ Qabul qilish (Band qilish)", callback_data=f"claim_{user_id}")
    admin_message_ids[user_id] = {}
    for admin in get_all_admins():
        try:
            msg = await bot.send_message(admin, f"📱 <b>Yangi raqam:</b>\n👤 Foydalanuvchi: {full_name}\n📞 Raqam: {phone}", parse_mode="HTML", reply_markup=builder.as_markup())
            admin_message_ids[user_id][admin] = msg.message_id
        except Exception: pass
    await message.answer("Raqamingiz qabul qilindi. Operatorlar ko'rib chiqmoqda...")

# --- BACKGROUND TAYMER FUNKSIYASI ---
async def session_timeout_task(user_id: int, state: FSMContext):
    await asyncio.sleep(120)  # 2 daqiqa
    current_state = await state.get_state()
    
    if current_state == VoteState.waiting_for_code:
        data = await state.get_data()
        phone = data.get("phone")
        
        log_to_sheets(user_id=user_id, phone=phone, status="Muddati o'tdi (Timeout)", admin_name=claimed_admin_names.get(user_id))
        
        try:
            await bot.send_message(
                chat_id=user_id, 
                text="⏱ <b>Vaqt tugadi!</b> Siz 2 daqiqa ichida SMS kodni yubormadingiz. Iltimos, jarayonni qaytadan boshlang.", 
                parse_mode="HTML",
                reply_markup=main_menu()
            )
        except Exception: pass
        
        admin_id = data.get("admin_id")
        if admin_id:
            try:
                await bot.send_message(
                    chat_id=admin_id, 
                    text=f"⏱ <b>Muddati o'tdi!</b> Foydalanuvchi ({phone}) 2 daqiqa ichida kod yubormadi. Ariza bekor qilindi.",
                    parse_mode="HTML"
                )
            except Exception: pass
            
        if user_id in claimed_users: del claimed_users[user_id]
        if user_id in claimed_admin_names: del claimed_admin_names[user_id]
        if user_id in admin_message_ids: del admin_message_ids[user_id]
        await state.clear()

# --- OPERATOR BOSHQARUVI VA SMS KOD ---
@dp.callback_query(F.data.startswith("claim_"))
async def admin_claim(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    admin_id, admin_name = callback.from_user.id, callback.from_user.full_name
    if user_id in claimed_users:
        await callback.answer("❌ Kech qoldingiz! Band qilingan.", show_alert=True); return

    claimed_users[user_id] = admin_id; claimed_admin_names[user_id] = admin_name
    increment_admin_stat(admin_id, 'claim')
    
    u_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    await u_state.set_state(VoteState.waiting_for_code)
    await u_state.update_data(admin_id=admin_id)
    u_data = await u_state.get_data()

    log_to_sheets(user_id=user_id, phone=u_data.get("phone"), status="Admin qabul qildi", admin_name=admin_name)
    
    if user_id in admin_message_ids:
        for a_id, m_id in admin_message_ids[user_id].items():
            try: await bot.edit_message_text(text=f"📱 Raqam keldi\n🔒 <b>[{admin_name}] qabul qildi!</b>", chat_id=a_id, message_id=m_id, parse_mode="HTML")
            except Exception: pass

    await bot.send_message(user_id, "Sizning raqamingiz kiritildi. SMS kodni yuboring. ⏱ 2:00 daqiqa", parse_mode="HTML")
    asyncio.create_task(session_timeout_task(user_id, u_state))

@dp.message(VoteState.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text; data = await state.get_data(); user_id = message.from_user.id
    await state.update_data(code=code)
    log_to_sheets(user_id=user_id, phone=data.get("phone"), code=code, status="Kod kiritildi", admin_name=claimed_admin_names.get(user_id))

    verify_kb = InlineKeyboardBuilder().button(text="✅ To'g'ri", callback_data=f"v_correct_{user_id}").button(text="❌ Xato", callback_data=f"v_wrong_{user_id}").adjust(2)
    try: await bot.send_message(data.get("admin_id"), f"🔢 Kod keldi: <code>{code}</code>\nTelefon: {data.get('phone')}", parse_mode="HTML", reply_markup=verify_kb.as_markup())
    except Exception: pass
    await message.answer("Kod tekshirilmoqda...")

@dp.callback_query(F.data.startswith("v_"))
async def handle_code_verification(callback: types.CallbackQuery):
    _, status, user_id = callback.data.split("_")
    user_id = int(user_id)
    u_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    data = await u_state.get_data()

    if status == "correct":
        log_to_sheets(user_id=user_id, phone=data.get("phone"), status="Kod tasdiqlandi", admin_name=callback.from_user.full_name)
        await callback.message.edit_text("🟢 Kod to'g'ri deb belgilandi."); await u_state.set_state(VoteState.waiting_for_screenshot)
        await bot.send_message(user_id, "🎉 Kod tasdiqlandi. 1 soat ichida sizga ovozingiz tasdiqlanganlik haqida SMS xabar boradi. O'shani skrinshot qilib yuboring! 📸")
    else:
        log_to_sheets(user_id=user_id, phone=data.get("phone"), status="Kod xato", admin_name=callback.from_user.full_name)
        await callback.message.edit_text("🔴 Kod xato deb belgilandi.")
        await bot.send_message(user_id, "⚠️ Kod rad etildi. To'g'ri kodni qayta kiriting.")

# --- SKRINSHOT VA YAKUNIY TASDIQLASH ---
@dp.message(VoteState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    p_id = message.photo[-1].file_id; data = await state.get_data(); user_id = message.from_user.id
    log_to_sheets(user_id=user_id, phone=data.get("phone"), status="Skrinshot keldi", admin_name=claimed_admin_names.get(user_id))

    builder = InlineKeyboardBuilder().button(text="🟢 Muvaffaqiyatli", callback_data=f"c_success_{user_id}").button(text="🔴 Avval ovoz bergan", callback_data=f"c_already_{user_id}").adjust(1)
    try: await bot.send_photo(data.get("admin_id"), p_id, caption=f"📸 Skrinshot keldi:\nRaqam: {data.get('phone')}", reply_markup=builder.as_markup())
    except Exception: pass
    await message.answer("Skrinshot yuborildi, admin tasdiqlashini kuting...")
    await state.set_state(VoteState.waiting_for_admin_check)

@dp.callback_query(F.data.startswith("c_"))
async def handle_admin_check(callback: types.CallbackQuery):
    _, action, user_id = callback.data.split("_")
    user_id = int(user_id)
    u_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    data = await u_state.get_data()
    phone = data.get("phone")

    if action == "success":
        if phone:
            add_voted_phone_local(phone, status="Muvaffaqiyatli")

        log_to_sheets(user_id=user_id, phone=phone, status="Muvaffaqiyatli", admin_name=callback.from_user.full_name)
        increment_admin_stat(callback.from_user.id, 'success')
        await callback.message.edit_caption(caption="✅ Tasdiqlandi!")
        
        beautiful_thanks_text = (
            "🎉 <b>Tabriklaymiz! Sizning ovozingiz muvaffaqiyatli tasdiqlandi.</b>\n\n"
            "✨ Tashabbusimizni qo'llab-quvvatlaganingiz hamda mahallamiz "
            "rivojiga befarq bo'lmaganingiz uchun sizga samimiy minnatdorchilik bildiramiz.\n\n"
            "🤝 <b>Ovoz berganingiz uchun rahmat QORABAYIR MFY!</b>"
        )
        await bot.send_message(chat_id=user_id, text=beautiful_thanks_text, parse_mode="HTML", reply_markup=main_menu())
    else:
        if phone:
            add_voted_phone_local(phone, status="Avval ovoz bergan")

        log_to_sheets(user_id=user_id, phone=phone, status="Avval ovoz bergan", admin_name=callback.from_user.full_name)
        increment_admin_stat(callback.from_user.id, 'already')
        await callback.message.edit_caption(caption="❌ Rad etildi (Avval ovoz bergan)")
        await bot.send_message(user_id, "Uzr, bu raqamdan avval foydalanilgan.", reply_markup=main_menu())

    if user_id in claimed_users: del claimed_users[user_id]
    if user_id in claimed_admin_names: del claimed_admin_names[user_id]
    if user_id in admin_message_ids: del admin_message_ids[user_id]
    await u_state.clear()

async def main():
    asyncio.create_task(sheets_worker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
