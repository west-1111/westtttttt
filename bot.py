import asyncio
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import logging

# ========== ТОЛЬКО ТОКЕН (всё остальное в БД) ==========
BOT_TOKEN = '8752409184:AAFHBonoFK1buVQv_v8MdaR4jezbG2ommEU'  # Заменить

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_database():
    conn = sqlite3.connect('wise_bot.db')
    cur = conn.cursor()

    # Пользователи
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        join_date TEXT,
        balance REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        total_orders INTEGER DEFAULT 0
    )''')

    # Аккаунты для автоматической выдачи
    cur.execute('''CREATE TABLE IF NOT EXISTS accounts_pool (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_data TEXT,
        is_used INTEGER DEFAULT 0,
        created_at TEXT,
        used_at TEXT,
        used_by INTEGER
    )''')

    # Заказы
    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        quantity INTEGER,
        price_per_unit REAL,
        total_price REAL,
        status TEXT,
        created_at TEXT,
        account_data TEXT
    )''')

    # Заявки на пополнение (крипта)
    cur.execute('''CREATE TABLE IF NOT EXISTS deposits (
        dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        method TEXT,
        status TEXT,
        created_at TEXT,
        proof TEXT,
        admin_note TEXT
    )''')

    # Криптокошельки
    cur.execute('''CREATE TABLE IF NOT EXISTS crypto_wallets (
        currency TEXT PRIMARY KEY,
        wallet_address TEXT,
        is_active INTEGER DEFAULT 1
    )''')

    # Цены
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value REAL
    )''')

    # Администраторы (храним user_id)
    cur.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        added_at TEXT
    )''')

    # Контакт для СБП (username администратора, который будет отображаться)
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    # Вставляем настройки по умолчанию
    cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)', ('price_single', 9.0))
    cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)', ('price_exact_10', 8.0))
    cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)', ('price_wholesale', 7.5))

    # Криптокошельки
    cur.execute('INSERT OR IGNORE INTO crypto_wallets (currency, wallet_address) VALUES (?,?)', ('USDT', 'TXvYOURWALLET'))
    cur.execute('INSERT OR IGNORE INTO crypto_wallets (currency, wallet_address) VALUES (?,?)', ('BTC', '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa'))
    cur.execute('INSERT OR IGNORE INTO crypto_wallets (currency, wallet_address) VALUES (?,?)', ('TON', 'UQYOURTONWALLET'))

    # Контакт администратора для СБП (по умолчанию)
    cur.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?,?)', ('sbp_admin_username', 'support_username'))

    conn.commit()
    conn.close()
    logger.info("Database initialized")

# ========== РАБОТА С БД ==========
class DB:
    @staticmethod
    def add_user(user_id, username, first_name, last_name=None):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, join_date) VALUES (?,?,?,?,?)',
                    (user_id, username, first_name, last_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    @staticmethod
    def get_user(user_id):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            cols = ['user_id','username','first_name','last_name','join_date','balance','total_spent','total_orders']
            return dict(zip(cols, row))
        return None
    @staticmethod
    def update_balance(user_id, delta):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (delta, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def add_account(account_data):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO accounts_pool (account_data, created_at) VALUES (?,?)', (account_data, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    @staticmethod
    def get_unused_account():
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT id, account_data FROM accounts_pool WHERE is_used = 0 LIMIT 1')
        row = cur.fetchone()
        conn.close()
        return {'id': row[0], 'data': row[1]} if row else None
    @staticmethod
    def mark_used(acc_id, user_id):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE accounts_pool SET is_used=1, used_at=?, used_by=? WHERE id=?', (datetime.now().isoformat(), user_id, acc_id))
        conn.commit()
        conn.close()
    @staticmethod
    def get_accounts_stats():
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM accounts_pool WHERE is_used = 0')
        free = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM accounts_pool')
        total = cur.fetchone()[0]
        conn.close()
        return free, total

    @staticmethod
    def create_order(user_id, quantity, price_per_unit, total_price, account_data):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO orders (user_id, quantity, price_per_unit, total_price, status, created_at, account_data) VALUES (?,?,?,?,?,?,?)',
                    (user_id, quantity, price_per_unit, total_price, 'completed', datetime.now().isoformat(), account_data))
        order_id = cur.lastrowid
        conn.commit()
        conn.close()
        conn2 = sqlite3.connect('wise_bot.db')
        cur2 = conn2.cursor()
        cur2.execute('UPDATE users SET total_spent = total_spent + ?, total_orders = total_orders + 1 WHERE user_id = ?', (total_price, user_id))
        conn2.commit()
        conn2.close()
        return order_id

    @staticmethod
    def add_deposit(user_id, amount, method, proof=None):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO deposits (user_id, amount, method, status, created_at, proof) VALUES (?,?,?,?,?,?)',
                    (user_id, amount, method, 'pending', datetime.now().isoformat(), proof))
        dep_id = cur.lastrowid
        conn.commit()
        conn.close()
        return dep_id
    @staticmethod
    def get_pending_deposits():
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT * FROM deposits WHERE status = "pending" ORDER BY created_at ASC')
        rows = cur.fetchall()
        conn.close()
        cols = ['dep_id','user_id','amount','method','status','created_at','proof','admin_note']
        return [dict(zip(cols, r)) for r in rows]
    @staticmethod
    def approve_deposit(dep_id):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT user_id, amount FROM deposits WHERE dep_id = ?', (dep_id,))
        row = cur.fetchone()
        if row:
            user_id, amount = row
            cur.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
            cur.execute('UPDATE deposits SET status = "approved" WHERE dep_id = ?', (dep_id,))
            conn.commit()
            conn.close()
            return user_id, amount
        conn.close()
        return None
    @staticmethod
    def reject_deposit(dep_id):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE deposits SET status = "rejected" WHERE dep_id = ?', (dep_id,))
        conn.commit()
        conn.close()
    @staticmethod
    def cleanup_expired_deposits(hours=24):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cur.execute('SELECT dep_id, user_id, amount FROM deposits WHERE status = "pending" AND created_at < ?', (cutoff,))
        expired = cur.fetchall()
        for dep_id, user_id, amount in expired:
            cur.execute('UPDATE deposits SET status = "rejected", admin_note = "timeout" WHERE dep_id = ?', (dep_id,))
        conn.commit()
        conn.close()
        return expired

    @staticmethod
    def get_crypto_wallet(currency):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT wallet_address FROM crypto_wallets WHERE currency = ? AND is_active = 1', (currency,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    @staticmethod
    def update_crypto_wallet(currency, address):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE crypto_wallets SET wallet_address = ? WHERE currency = ?', (address, currency))
        conn.commit()
        conn.close()

    @staticmethod
    def get_price(key):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    @staticmethod
    def set_price(key, value):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
        conn.commit()
        conn.close()

    @staticmethod
    def add_admin(user_id, added_by):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?,?,?)',
                    (user_id, added_by, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    @staticmethod
    def remove_admin(user_id):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    @staticmethod
    def get_all_admins():
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT user_id FROM admins')
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows]
    @staticmethod
    def is_admin(user_id):
        return user_id in DB.get_all_admins()

    @staticmethod
    def get_sbp_admin_username():
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('SELECT value FROM bot_config WHERE key = "sbp_admin_username"')
        row = cur.fetchone()
        conn.close()
        return row[0] if row else "support_username"
    @staticmethod
    def set_sbp_admin_username(username):
        conn = sqlite3.connect('wise_bot.db')
        cur = conn.cursor()
        cur.execute('UPDATE bot_config SET value = ? WHERE key = "sbp_admin_username"', (username,))
        conn.commit()
        conn.close()

# ========== ФУНКЦИЯ ЦЕНЫ ==========
def get_current_prices():
    return {
        'single': DB.get_price('price_single'),
        'exact_10': DB.get_price('price_exact_10'),
        'wholesale': DB.get_price('price_wholesale')
    }

def calculate_price(quantity):
    prices = get_current_prices()
    if quantity == 1:
        return prices['single'], prices['single'], "розница"
    elif quantity == 10:
        return prices['exact_10'], 10 * prices['exact_10'], "опт (10 шт)"
    elif quantity > 10:
        return prices['wholesale'], quantity * prices['wholesale'], "опт (>10 шт)"
    else:
        return prices['single'], quantity * prices['single'], "розница"

# ========== FSM ==========
class OrderStates(StatesGroup):
    choosing_quantity = State()
class DepositStates(StatesGroup):
    choosing_amount = State()
    choosing_method = State()
    waiting_proof = State()
class AdminStates(StatesGroup):
    sending_accounts = State()
    broadcasting = State()
    editing_crypto = State()
    editing_price = State()
    adding_admin = State()
    removing_admin = State()
    editing_sbp_contact = State()

# ========== КЛАВИАТУРЫ ==========
class Keyboards:
    @staticmethod
    def main_menu(user_id=None):
        is_admin = DB.is_admin(user_id) if user_id else False
        buttons = [
            [InlineKeyboardButton(text="💳 Купить WISE (авто)", callback_data="buy_wise_auto")],
            [InlineKeyboardButton(text="🏦 Купить через СБП (ручная)", callback_data="buy_sbp_manual")],
            [InlineKeyboardButton(text="💰 Мой баланс", callback_data="my_balance")],
            [InlineKeyboardButton(text="💸 Пополнить баланс", callback_data="deposit")],
            [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders")],
            [InlineKeyboardButton(text="ℹ️ Инструкция", callback_data="instructions")],
            [InlineKeyboardButton(text="👨‍💻 Поддержка", callback_data="support")]
        ]
        if is_admin:
            buttons.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def quantity_kb():
        prices = get_current_prices()
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"1 аккаунт - ${prices['single']}", callback_data="qty_1")],
            [InlineKeyboardButton(text=f"10 аккаунтов - ${10*prices['exact_10']} (${prices['exact_10']}/шт)", callback_data="qty_10")],
            [InlineKeyboardButton(text=f"20 аккаунтов - ${20*prices['wholesale']} (${prices['wholesale']}/шт)", callback_data="qty_20")],
            [InlineKeyboardButton(text=f"50 аккаунтов - ${50*prices['wholesale']} (${prices['wholesale']}/шт)", callback_data="qty_50")],
            [InlineKeyboardButton(text="💬 Свое количество", callback_data="qty_custom")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
        ])

    @staticmethod
    def deposit_methods():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="₿ Bitcoin (BTC)", callback_data="dep_method_BTC")],
            [InlineKeyboardButton(text="💎 TON", callback_data="dep_method_TON")],
            [InlineKeyboardButton(text="🪙 USDT (TRC20)", callback_data="dep_method_USDT")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
        ])

    @staticmethod
    def admin_panel():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Загрузить аккаунты", callback_data="admin_add_accounts")],
            [InlineKeyboardButton(text="💰 Пополнения на проверке", callback_data="admin_deposits")],
            [InlineKeyboardButton(text="💳 Реквизиты крипты", callback_data="admin_crypto_wallets")],
            [InlineKeyboardButton(text="💰 Изменить цены", callback_data="admin_edit_prices")],
            [InlineKeyboardButton(text="👥 Управление админами", callback_data="admin_manage_admins")],
            [InlineKeyboardButton(text="📞 Контакт для СБП", callback_data="admin_edit_sbp_contact")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="◀ Выход", callback_data="back_main")]
        ])

    @staticmethod
    def deposit_actions(dep_id):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Зачислить", callback_data=f"approve_dep_{dep_id}"),
             InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_dep_{dep_id}")]
        ])

    @staticmethod
    def crypto_edit_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="₿ BTC", callback_data="edit_crypto_BTC")],
            [InlineKeyboardButton(text="💎 TON", callback_data="edit_crypto_TON")],
            [InlineKeyboardButton(text="🪙 USDT", callback_data="edit_crypto_USDT")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel")]
        ])

    @staticmethod
    def price_edit_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔹 Цена 1 аккаунта (розница)", callback_data="edit_price_single")],
            [InlineKeyboardButton(text="🔸 Цена за 10 аккаунтов (за шт)", callback_data="edit_price_exact_10")],
            [InlineKeyboardButton(text="🚀 Цена при >10 аккаунтов (за шт)", callback_data="edit_price_wholesale")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel")]
        ])

    @staticmethod
    def admin_manage_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить администратора", callback_data="admin_add_admin")],
            [InlineKeyboardButton(text="➖ Удалить администратора", callback_data="admin_remove_admin")],
            [InlineKeyboardButton(text="📋 Список админов", callback_data="admin_list_admins")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel")]
        ])

# ========== ОСНОВНОЙ БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ---------- Утилиты ----------
async def notify_admins(text, reply_markup=None):
    for admin_id in DB.get_all_admins():
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup, parse_mode="Markdown")
        except:
            pass

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    DB.add_user(user.id, user.username or "", user.first_name, user.last_name)
    sbp_contact = DB.get_sbp_admin_username()
    
    # Текст приветственного сообщения
    welcome_text = (
        f"💳 *Добро пожаловать в магазин WISE аккаунтов!*\n\n"
        f"💰 Ваш баланс: $0\n"
        f"🔹 1 аккаунт: ${DB.get_price('price_single')}\n"
        f"🔸 10 аккаунтов: ${10*DB.get_price('price_exact_10')} (${DB.get_price('price_exact_10')}/шт)\n"
        f"🔹 от 11 аккаунтов: ${DB.get_price('price_wholesale')}/шт\n\n"
        f"Выберите действие:"
    )
    
    # Проверяем наличие файла photo.jpg в папке с ботом
    photo_path = "photo.jpg"
    
    if os.path.exists(photo_path):
        # Если фото существует - отправляем с фото
        photo = FSInputFile(photo_path)
        await message.answer_photo(
            photo=photo,
            caption=welcome_text,
            reply_markup=Keyboards.main_menu(user.id),
            parse_mode="Markdown"
        )
    else:
        # Если файл не найден - отправляем только текст
        logger.warning(f"Файл {photo_path} не найден в директории")
        await message.answer(
            welcome_text,
            reply_markup=Keyboards.main_menu(user.id),
            parse_mode="Markdown"
        )

@dp.callback_query(lambda c: c.data == "my_balance")
async def show_balance(callback: CallbackQuery, **kwargs):
    user = DB.get_user(callback.from_user.id)
    bal = user['balance'] if user else 0
    await callback.message.edit_text(
        f"💰 *Ваш баланс:* ${bal:.2f}\n\n"
        f"Пополнить через криптовалюту — кнопка ниже.\n"
        f"Для покупки через СБП используйте отдельную кнопку в меню.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Пополнить криптой", callback_data="deposit")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
        ]), parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "buy_sbp_manual")
async def buy_sbp_manual(callback: CallbackQuery, **kwargs):
    sbp_contact = DB.get_sbp_admin_username()
    await callback.message.edit_text(
        f"🏦 *Покупка через СБП (ручная выдача)*\n\n"
        f"Вы выбрали ручной способ оплаты.\n"
        f"Напишите администратору @{sbp_contact}, он предоставит реквизиты для перевода и выдаст аккаунты после оплаты.\n\n"
        f"Укажите в сообщении желаемое количество аккаунтов.\n\n"
        f"👨‍💻 Контакт: @{sbp_contact}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
        ]), parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "buy_wise_auto")
async def buy_wise_auto(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.choosing_quantity)
    await callback.message.edit_text("Выберите количество аккаунтов (автоматическая выдача с баланса):",
                                     reply_markup=Keyboards.quantity_kb())

@dp.callback_query(lambda c: c.data.startswith("qty_"))
async def process_auto_buy(callback: CallbackQuery, state: FSMContext):
    data = callback.data.split("_")[1]
    if data == "custom":
        await callback.message.edit_text("Введите количество (целое число):")
        return
    quantity = int(data)
    await auto_buy(callback, state, quantity)

@dp.message(OrderStates.choosing_quantity)
async def custom_quantity_input(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
        if qty <= 0: raise ValueError
        class Mock:
            def __init__(self, msg, uid):
                self.message = msg
                self.from_user = type('obj', (object,), {'id': uid})
            async def answer(self): pass
        mock = Mock(message, message.from_user.id)
        await auto_buy(mock, state, qty)
    except:
        await message.answer("Введите целое число больше 0")

async def auto_buy(callback, state, quantity):
    user_id = callback.from_user.id
    user = DB.get_user(user_id)
    price_per_unit, total_price, _ = calculate_price(quantity)
    if user['balance'] < total_price:
        need = total_price - user['balance']
        await callback.message.edit_text(
            f"❌ *Недостаточно средств*\n"
            f"💰 Баланс: ${user['balance']:.2f}\n"
            f"💵 Нужно: ${total_price:.2f}\n"
            f"💸 Не хватает: ${need:.2f}\n\n"
            f"Пополните баланс (криптовалюта) или используйте ручную покупку через СБП.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Пополнить криптой", callback_data="deposit")],
                [InlineKeyboardButton(text="🏦 Купить через СБП", callback_data="buy_sbp_manual")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
            ]), parse_mode="Markdown"
        )
        await state.clear()
        return
    free, total = DB.get_accounts_stats()
    if free < quantity:
        await callback.message.edit_text(
            f"😞 Недостаточно аккаунтов в базе (свободно {free}). Администратор уведомлён.",
            reply_markup=Keyboards.main_menu(user_id)
        )
        await notify_admins(f"⚠️ Закончились аккаунты! Нужно {quantity}, есть {free}")
        await state.clear()
        return
    DB.update_balance(user_id, -total_price)
    accounts = []
    for _ in range(quantity):
        acc = DB.get_unused_account()
        if acc:
            DB.mark_used(acc['id'], user_id)
            accounts.append(acc['data'])
    if len(accounts) != quantity:
        DB.update_balance(user_id, total_price)
        await callback.message.edit_text("Ошибка выдачи, деньги возвращены. Попробуйте позже.")
        return
    accounts_text = '\n'.join(accounts)
    DB.create_order(user_id, quantity, price_per_unit, total_price, accounts_text)
    await callback.message.edit_text(
        f"✅ *Покупка успешна!*\n"
        f"🔢 {quantity} аккаунтов WISE\n"
        f"💰 Списано: ${total_price:.2f}\n"
        f"📦 Аккаунты:\n```\n{accounts_text}\n```\n\n"
        f"Смените пароли, гарантия 30 дней.",
        parse_mode="Markdown"
    )
    await state.clear()

@dp.callback_query(lambda c: c.data == "my_orders")
async def my_orders(callback: CallbackQuery, **kwargs):
    conn = sqlite3.connect('wise_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT order_id, quantity, total_price, created_at FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 10', (callback.from_user.id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("У вас ещё нет заказов.", reply_markup=Keyboards.main_menu(callback.from_user.id))
        return
    text = "📦 *Ваши заказы:*\n\n"
    for r in rows:
        text += f"#{r[0]} | {r[1]} шт | ${r[2]:.2f} | {r[3][:10]}\n"
    await callback.message.edit_text(text, reply_markup=Keyboards.main_menu(callback.from_user.id), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "instructions")
async def instructions(callback: CallbackQuery, **kwargs):
    sbp_contact = DB.get_sbp_admin_username()
    text = (f"📚 *Инструкция*\n\n"
            f"1. *Автоматическая покупка*: пополните баланс криптовалютой (админ подтвердит), затем выберите количество — аккаунты придут сразу.\n"
            f"2. *Ручная покупка через СБП*: нажмите соответствующую кнопку и напишите @{sbp_contact}.\n\n"
            f"👨‍💻 Поддержка: @{sbp_contact}")
    await callback.message.edit_text(text, reply_markup=Keyboards.main_menu(callback.from_user.id), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "support")
async def support(callback: CallbackQuery, **kwargs):
    sbp_contact = DB.get_sbp_admin_username()
    await callback.message.edit_text(
        f"👨‍💻 *Поддержка*\n\nПо любым вопросам пишите: @{sbp_contact}",
        reply_markup=Keyboards.main_menu(callback.from_user.id), parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=Keyboards.main_menu(callback.from_user.id))

# ========== ПОПОЛНЕНИЕ КРИПТОЙ ==========
@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.choosing_amount)
    await callback.message.edit_text("💰 Введите сумму пополнения в долларах (мин $5):", parse_mode="Markdown")

@dp.message(DepositStates.choosing_amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount < 5:
            await message.answer("Минимум $5. Введите другую сумму:")
            return
        await state.update_data(amount=amount)
        await state.set_state(DepositStates.choosing_method)
        await message.answer("Выберите криптовалюту для оплаты:", reply_markup=Keyboards.deposit_methods())
    except:
        await message.answer("Введите число, например 10")

@dp.callback_query(lambda c: c.data.startswith("dep_method_"))
async def deposit_crypto(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split("_")[2]
    data = await state.get_data()
    amount = data['amount']
    wallet = DB.get_crypto_wallet(method)
    if not wallet:
        await callback.answer("Реквизиты не настроены администратором", show_alert=True)
        return
    dep_id = DB.add_deposit(callback.from_user.id, amount, method)
    await state.update_data(dep_id=dep_id)
    await state.set_state(DepositStates.waiting_proof)
    await callback.message.edit_text(
        f"💳 *Заявка #{dep_id}*\nСумма: ${amount:.2f}\nМетод: {method}\n\n"
        f"Переведите ровно ${amount:.2f} на кошелёк:\n`{wallet}`\n\n"
        f"После перевода нажмите кнопку и отправьте скриншот чека.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я перевел", callback_data="send_proof")]
        ]), parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "send_proof")
async def request_proof(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📸 Отправьте скриншот или фото чека об оплате:")

@dp.message(DepositStates.waiting_proof)
async def receive_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    dep_id = data.get('dep_id')
    if not dep_id:
        await message.answer("Ошибка, начните пополнение заново /start")
        await state.clear()
        return
    proof = message.photo[-1].file_id if message.photo else message.text
    conn = sqlite3.connect('wise_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE deposits SET proof = ? WHERE dep_id = ?', (proof, dep_id))
    conn.commit()
    conn.close()
    await message.answer("✅ Чек отправлен администратору. Дождитесь подтверждения (до 30 минут).")
    user = DB.get_user(message.from_user.id)
    for admin_id in DB.get_all_admins():
        await bot.send_message(admin_id,
                               f"🆕 *Новая заявка #{dep_id}*\n"
                               f"👤 {user['username']} (ID {user['user_id']})\n"
                               f"💰 ${data['amount']:.2f}\n"
                               f"💳 Метод: {data.get('method','?')}\n"
                               f"📎 Доказательство: {'фото' if message.photo else message.text}",
                               reply_markup=Keyboards.deposit_actions(dep_id),
                               parse_mode="Markdown")
    await state.clear()

# ========== АДМИН-ПАНЕЛЬ ==========
def admin_required(handler):
    async def wrapper(callback: CallbackQuery, *args, **kwargs):
        if not DB.is_admin(callback.from_user.id):
            await callback.answer("Нет прав", show_alert=True)
            return
        return await handler(callback, *args, **kwargs)
    return wrapper

@dp.callback_query(lambda c: c.data == "admin_panel")
@admin_required
async def admin_panel(callback: CallbackQuery, **kwargs):
    free, total = DB.get_accounts_stats()
    await callback.message.edit_text(
        f"🔧 *Админ-панель*\n📦 Аккаунтов: {total} (свободно {free})\n\n"
        f"Выберите действие:",
        reply_markup=Keyboards.admin_panel(), parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "admin_add_accounts")
@admin_required
async def add_accounts_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.sending_accounts)
    await callback.message.edit_text("Отправьте аккаунты (каждый с новой строки) или .txt файл.\nФормат: логин:пароль")

@dp.message(AdminStates.sending_accounts)
async def add_accounts(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    if message.document:
        file = await bot.get_file(message.document.file_id)
        content = await bot.download_file(file.file_path)
        lines = content.decode().strip().split('\n')
    else:
        lines = message.text.strip().split('\n')
    count = 0
    for line in lines:
        line = line.strip()
        if line:
            DB.add_account(line)
            count += 1
    await message.answer(f"✅ Добавлено {count} аккаунтов в базу.")
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_deposits")
@admin_required
async def list_deposits(callback: CallbackQuery, **kwargs):
    # Очищаем просроченные заявки
    expired = DB.cleanup_expired_deposits()
    for dep_id, user_id, _ in expired:
        try:
            await bot.send_message(user_id, f"❌ Ваша заявка #{dep_id} автоматически отклонена (прошло более 24 часов). Создайте новую.")
        except:
            pass
    deposits = DB.get_pending_deposits()
    if not deposits:
        await callback.message.edit_text("Нет неподтверждённых пополнений.", reply_markup=Keyboards.admin_panel())
        return
    text = "💰 *Заявки на пополнение:*\n\n"
    for d in deposits:
        text += f"#{d['dep_id']} | {d['user_id']} | ${d['amount']:.2f} | {d['method']} | {d['created_at'][:10]}\n"
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_panel(), parse_mode="Markdown")
    for d in deposits:
        caption = f"📌 Заявка #{d['dep_id']}\n👤 Пользователь: {d['user_id']}\n💰 Сумма: ${d['amount']:.2f}\n💳 Метод: {d['method']}\n🕐 Создана: {d['created_at'][:10]}"
        if d['proof'] and d['proof'].startswith('AgAC'):
            try:
                await bot.send_photo(callback.from_user.id, d['proof'], caption=caption, reply_markup=Keyboards.deposit_actions(d['dep_id']))
            except:
                await callback.message.answer(caption, reply_markup=Keyboards.deposit_actions(d['dep_id']))
        else:
            await callback.message.answer(caption, reply_markup=Keyboards.deposit_actions(d['dep_id']))

@dp.callback_query(lambda c: c.data.startswith("approve_dep_"))
@admin_required
async def approve_dep(callback: CallbackQuery, **kwargs):
    dep_id = int(callback.data.split("_")[2])
    res = DB.approve_deposit(dep_id)
    if res:
        user_id, amount = res
        await bot.send_message(user_id, f"✅ Ваше пополнение на ${amount:.2f} одобрено! Баланс обновлён.")
        await callback.answer("Средства зачислены")
    else:
        await callback.answer("Ошибка")
    await list_deposits(callback)

@dp.callback_query(lambda c: c.data.startswith("reject_dep_"))
@admin_required
async def reject_dep(callback: CallbackQuery, **kwargs):
    dep_id = int(callback.data.split("_")[2])
    DB.reject_deposit(dep_id)
    await callback.answer("Заявка отклонена")
    await list_deposits(callback)

@dp.callback_query(lambda c: c.data == "admin_crypto_wallets")
@admin_required
async def crypto_menu(callback: CallbackQuery, **kwargs):
    await callback.message.edit_text("Выберите валюту для изменения кошелька:", reply_markup=Keyboards.crypto_edit_menu())

@dp.callback_query(lambda c: c.data.startswith("edit_crypto_"))
@admin_required
async def edit_crypto(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[2]
    await state.update_data(crypto_currency=currency)
    await state.set_state(AdminStates.editing_crypto)
    await callback.message.edit_text(f"Введите новый адрес кошелька для {currency}:")

@dp.message(AdminStates.editing_crypto)
async def save_crypto(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    currency = data['crypto_currency']
    address = message.text.strip()
    DB.update_crypto_wallet(currency, address)
    await message.answer(f"✅ Кошелёк для {currency} обновлён")
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_edit_prices")
@admin_required
async def edit_prices_menu(callback: CallbackQuery, **kwargs):
    await callback.message.edit_text("Выберите цену для изменения:", reply_markup=Keyboards.price_edit_menu())

@dp.callback_query(lambda c: c.data.startswith("edit_price_"))
@admin_required
async def edit_price_start(callback: CallbackQuery, state: FSMContext):
    key_map = {
        'edit_price_single': 'price_single',
        'edit_price_exact_10': 'price_exact_10',
        'edit_price_wholesale': 'price_wholesale'
    }
    key = key_map[callback.data]
    await state.update_data(price_key=key)
    await state.set_state(AdminStates.editing_price)
    current = DB.get_price(key)
    await callback.message.edit_text(f"Текущая цена: ${current}\nВведите новую цену (в долларах):")

@dp.message(AdminStates.editing_price)
async def save_price(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    key = data['price_key']
    try:
        new_price = float(message.text.strip())
        if new_price <= 0:
            raise ValueError
        DB.set_price(key, new_price)
        await message.answer(f"✅ Цена для {key} обновлена на ${new_price}")
    except:
        await message.answer("Ошибка. Введите положительное число.")
    await state.clear()

# ---------- Управление админами ----------
@dp.callback_query(lambda c: c.data == "admin_manage_admins")
@admin_required
async def admin_manage_menu(callback: CallbackQuery, **kwargs):
    await callback.message.edit_text("Управление администраторами:", reply_markup=Keyboards.admin_manage_menu())

@dp.callback_query(lambda c: c.data == "admin_list_admins")
@admin_required
async def list_admins(callback: CallbackQuery, **kwargs):
    admins = DB.get_all_admins()
    if not admins:
        text = "Список администраторов пуст."
    else:
        text = "👥 *Список администраторов:*\n"
        for uid in admins:
            try:
                user = await bot.get_chat(uid)
                name = user.full_name or str(uid)
            except:
                name = str(uid)
            text += f"- {name} (ID: {uid})\n"
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_manage_menu(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "admin_add_admin")
@admin_required
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.adding_admin)
    await callback.message.edit_text("Введите Telegram ID пользователя, которого хотите сделать администратором:")

@dp.message(AdminStates.adding_admin)
async def add_admin(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    try:
        new_admin_id = int(message.text.strip())
        if DB.is_admin(new_admin_id):
            await message.answer("Этот пользователь уже администратор.")
        else:
            DB.add_admin(new_admin_id, message.from_user.id)
            await message.answer(f"✅ Пользователь {new_admin_id} добавлен в администраторы.")
            try:
                await bot.send_message(new_admin_id, "🎉 Вы стали администратором бота! Используйте /start для доступа к админ-панели.")
            except:
                pass
    except:
        await message.answer("Ошибка. Введите числовой ID.")
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_remove_admin")
@admin_required
async def remove_admin_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.removing_admin)
    await callback.message.edit_text("Введите Telegram ID администратора, которого хотите удалить.\n(Вы не можете удалить самого себя)")

@dp.message(AdminStates.removing_admin)
async def remove_admin(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    try:
        admin_id = int(message.text.strip())
        if admin_id == message.from_user.id:
            await message.answer("❌ Нельзя удалить самого себя.")
        elif not DB.is_admin(admin_id):
            await message.answer("Этот пользователь не является администратором.")
        else:
            DB.remove_admin(admin_id)
            await message.answer(f"✅ Администратор {admin_id} удалён.")
    except:
        await message.answer("Ошибка. Введите числовой ID.")
    await state.clear()

# ---------- Контакт для СБП ----------
@dp.callback_query(lambda c: c.data == "admin_edit_sbp_contact")
@admin_required
async def edit_sbp_contact_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.editing_sbp_contact)
    current = DB.get_sbp_admin_username()
    await callback.message.edit_text(
        f"Текущий контакт для СБП и поддержки: @{current}\n\n"
        f"Введите новый username (без @):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel")]
        ])
    )

@dp.message(AdminStates.editing_sbp_contact)
async def save_sbp_contact(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    new_username = message.text.strip().replace('@', '')
    if new_username:
        DB.set_sbp_admin_username(new_username)
        await message.answer(f"✅ Контакт для СБП обновлён: @{new_username}")
    else:
        await message.answer("Некорректный username.")
    await state.clear()

# ---------- Рассылка и статистика ----------
@dp.callback_query(lambda c: c.data == "admin_broadcast")
@admin_required
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.broadcasting)
    await callback.message.edit_text("Введите текст для рассылки (Markdown):")

@dp.message(AdminStates.broadcasting)
async def do_broadcast(message: Message, state: FSMContext):
    if not DB.is_admin(message.from_user.id):
        return
    conn = sqlite3.connect('wise_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users')
    users = [r[0] for r in cur.fetchall()]
    conn.close()
    ok = 0
    for uid in users:
        try:
            await bot.send_message(uid, message.text, parse_mode="Markdown")
            ok += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"Рассылка завершена. Отправлено {ok} из {len(users)}")
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_stats")
@admin_required
async def admin_stats(callback: CallbackQuery, **kwargs):
    conn = sqlite3.connect('wise_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users')
    users = cur.fetchone()[0]
    cur.execute('SELECT SUM(total_spent) FROM users')
    total_spent = cur.fetchone()[0] or 0
    cur.execute('SELECT COUNT(*) FROM orders')
    orders = cur.fetchone()[0]
    cur.execute('SELECT SUM(total_price) FROM orders')
    revenue = cur.fetchone()[0] or 0
    free, total_acc = DB.get_accounts_stats()
    conn.close()
    text = (f"📊 *Статистика*\n👥 Пользователей: {users}\n📦 Заказов: {orders}\n"
            f"💰 Выручка: ${revenue:.2f}\n💳 Продано акков: {total_acc - free}\n"
            f"📦 Аккаунтов в базе: {total_acc} (свободно {free})")
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_panel(), parse_mode="Markdown")

# ========== ЗАПУСК ==========
async def main():
    init_database()
    FIRST_ADMIN_ID = 7750744693  # Замените на ваш Telegram ID
    if not DB.get_all_admins():
        DB.add_admin(FIRST_ADMIN_ID, 0)
        logger.info(f"Добавлен первый администратор {FIRST_ADMIN_ID}")
    await bot.set_my_commands([types.BotCommand(command="start", description="Главное меню")])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
