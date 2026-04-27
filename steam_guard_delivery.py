"""
Плагин для автоматической выдачи Steam Guard кодов покупателям Steam аккаунтов.
"""
from __future__ import annotations
import json
import time
import hmac
import base64
import struct
import hashlib
import re
from typing import TYPE_CHECKING
from datetime import datetime, timedelta
from pathlib import Path

from FunPayAPI.updater.events import NewMessageEvent, NewOrderEvent
from tg_bot.utils import escape
from tg_bot import CBT
import tg_bot.static_keyboards
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
import telebot
import logging
from locales.localizer import Localizer

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("FPC.steam_guard")
localizer = Localizer()
_ = localizer.translate

NAME = "Steam Guard Delivery"
VERSION = "1.0.0"
DESCRIPTION = "Автоматическая выдача Steam Guard кодов покупателям с ограничением по времени (30 дней)"
CREDITS = "@mecccz"
UUID = "8f7a6b5c-4d3e-4f2a-9b8c-7d6e5f4a3b2c"
SETTINGS_PAGE = True

# Callback data для Telegram бота
CBT_SETTINGS = "SteamGuard_Settings"
CBT_ADD_ACCOUNT = "SteamGuard_AddAccount"
CBT_REMOVE_ACCOUNT = "SteamGuard_RemoveAccount"
CBT_LIST_ACCOUNTS = "SteamGuard_ListAccounts"
CBT_VIEW_BUYERS = "SteamGuard_ViewBuyers"

# Настройки по умолчанию
SETTINGS = {
    "enabled": True,
    "access_days": 30,
    "auto_send_on_purchase": True,
    "command": "!code",
    "watermark": False
}

# Путь к файлам данных
STORAGE_DIR = Path("storage/plugins/steam_guard")
SETTINGS_FILE = STORAGE_DIR / "settings.json"
ACCOUNTS_FILE = STORAGE_DIR / "accounts.json"
BUYERS_FILE = STORAGE_DIR / "buyers.json"

# Структура данных
# accounts.json: {"account_name": "shared_secret_key"}
# buyers.json: {"buyer_username": {"account_name": "...", "purchase_date": "...", "expires_at": "..."}}


def generate_steam_code(shared_secret: str) -> str:
    """
    Генерирует Steam Guard код из shared secret.
    
    :param shared_secret: Base64 закодированный shared secret из maFile
    :return: 5-символьный Steam Guard код
    """
    try:
        # Декодируем shared secret из base64
        secret = base64.b64decode(shared_secret)
        
        # Получаем текущее время в Unix timestamp
        timestamp = int(time.time())
        
        # Steam использует 30-секундные интервалы
        time_buffer = struct.pack('>Q', timestamp // 30)
        
        # Генерируем HMAC-SHA1
        hmac_hash = hmac.new(secret, time_buffer, hashlib.sha1).digest()
        
        # Извлекаем offset из последнего байта
        offset = hmac_hash[-1] & 0x0F
        
        # Извлекаем 4 байта начиная с offset
        code_int = struct.unpack('>I', hmac_hash[offset:offset + 4])[0] & 0x7FFFFFFF
        
        # Steam использует специальный алфавит
        chars = '23456789BCDFGHJKMNPQRTVWXY'
        code = ''
        
        for _ in range(5):
            code += chars[code_int % len(chars)]
            code_int //= len(chars)
        
        return code
    except Exception as e:
        logger.error(f"Ошибка при генерации Steam Guard кода: {e}")
        logger.debug("TRACEBACK", exc_info=True)
        return None


def load_json(file_path: Path, default=None):
    """Загружает JSON файл"""
    if default is None:
        default = {}
    try:
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при загрузке {file_path}: {e}")
    return default


def save_json(file_path: Path, data):
    """Сохраняет данные в JSON файл"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении {file_path}: {e}")
        return False


def load_settings():
    """Загружает настройки плагина"""
    global SETTINGS
    loaded = load_json(SETTINGS_FILE, SETTINGS)
    SETTINGS.update(loaded)


def save_settings():
    """Сохраняет настройки плагина"""
    save_json(SETTINGS_FILE, SETTINGS)


def load_accounts():
    """Загружает список Steam аккаунтов и их shared secrets"""
    return load_json(ACCOUNTS_FILE, {})


def save_accounts(accounts):
    """Сохраняет список Steam аккаунтов"""
    save_json(ACCOUNTS_FILE, accounts)


def load_buyers():
    """Загружает список покупателей и их доступы"""
    return load_json(BUYERS_FILE, {})


def save_buyers(buyers):
    """Сохраняет список покупателей"""
    save_json(BUYERS_FILE, buyers)


def add_buyer_access(buyer_username: str, account_name: str):
    """
    Добавляет или обновляет доступ покупателя к Steam Guard кодам
    
    :param buyer_username: Имя покупателя на FunPay
    :param account_name: Название Steam аккаунта
    """
    buyers = load_buyers()
    
    now = datetime.now()
    expires_at = now + timedelta(days=SETTINGS["access_days"])
    
    buyers[buyer_username] = {
        "account_name": account_name,
        "purchase_date": now.isoformat(),
        "expires_at": expires_at.isoformat()
    }
    
    save_buyers(buyers)
    logger.info(f"Добавлен доступ для {buyer_username} к аккаунту {account_name} до {expires_at.strftime('%d.%m.%Y %H:%M')}")


def check_buyer_access(buyer_username: str) -> tuple[bool, str, str]:
    """
    Проверяет доступ покупателя к Steam Guard кодам
    
    :param buyer_username: Имя покупателя
    :return: (имеет_доступ, название_аккаунта, сообщение_об_ошибке)
    """
    buyers = load_buyers()
    
    if buyer_username not in buyers:
        return False, None, "❌ У вас нет активного доступа к Steam Guard кодам. Пожалуйста, приобретите аккаунт."
    
    buyer_data = buyers[buyer_username]
    expires_at = datetime.fromisoformat(buyer_data["expires_at"])
    
    if datetime.now() > expires_at:
        return False, None, f"❌ Ваш доступ истек {expires_at.strftime('%d.%m.%Y')}. Для получения кодов необходимо повторно приобрести товар."
    
    account_name = buyer_data["account_name"]
    accounts = load_accounts()
    
    if account_name not in accounts:
        return False, None, "❌ Аккаунт не найден в базе данных. Обратитесь к продавцу."
    
    return True, account_name, None


def get_steam_code_for_buyer(buyer_username: str) -> str:
    """
    Получает Steam Guard код для покупателя
    
    :param buyer_username: Имя покупателя
    :return: Сообщение с кодом или ошибкой
    """
    has_access, account_name, error_msg = check_buyer_access(buyer_username)
    
    if not has_access:
        return error_msg
    
    accounts = load_accounts()
    shared_secret = accounts[account_name]
    
    code = generate_steam_code(shared_secret)
    
    if code is None:
        return "❌ Ошибка при генерации кода. Обратитесь к продавцу."
    
    buyers = load_buyers()
    expires_at = datetime.fromisoformat(buyers[buyer_username]["expires_at"])
    days_left = (expires_at - datetime.now()).days
    
    return f"""🔐 Steam Guard код для аккаунта {account_name}:

{code}

⏰ Код действителен 30 секунд
📅 Доступ истекает через {days_left} дн. ({expires_at.strftime('%d.%m.%Y')})

💡 Для получения нового кода напишите: {SETTINGS['command']}"""


def init(cardinal: Cardinal):
    """Инициализация плагина"""
    tg = cardinal.telegram
    if not tg:
        logger.warning("Telegram бот не активен. Настройки плагина через Telegram будут недоступны.")
        return
    
    bot = tg.bot
    
    # Загружаем настройки
    load_settings()
    
    # Создаем директорию для хранения данных
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    def open_settings(call: telebot.types.CallbackQuery):
        """Открывает главное меню настроек"""
        keyboard = K()
        keyboard.add(B(f"{'🟢' if SETTINGS['enabled'] else '🔴'} Плагин {'включен' if SETTINGS['enabled'] else 'выключен'}", 
                      callback_data=f"{CBT_SETTINGS}:toggle"))
        keyboard.add(B(f"⏰ Срок доступа: {SETTINGS['access_days']} дней", 
                      callback_data=f"{CBT_SETTINGS}:days"))
        keyboard.add(B(f"{'🟢' if SETTINGS['auto_send_on_purchase'] else '🔴'} Авто-отправка при покупке", 
                      callback_data=f"{CBT_SETTINGS}:auto"))
        keyboard.add(B(f"💬 Команда: {SETTINGS['command']}", 
                      callback_data=f"{CBT_SETTINGS}:command"))
        keyboard.add(B("➕ Добавить аккаунт", callback_data=CBT_ADD_ACCOUNT))
        keyboard.add(B("📋 Список аккаунтов", callback_data=CBT_LIST_ACCOUNTS))
        keyboard.add(B("👥 Список покупателей", callback_data=CBT_VIEW_BUYERS))
        keyboard.add(B("◀️ Назад", callback_data=f"{CBT.EDIT_PLUGIN}:{UUID}:0"))
        
        text = f"""⚙️ <b>Настройки Steam Guard Delivery</b>

📊 Статус: {'✅ Активен' if SETTINGS['enabled'] else '❌ Отключен'}
⏰ Срок доступа: {SETTINGS['access_days']} дней
🤖 Авто-отправка: {'Да' if SETTINGS['auto_send_on_purchase'] else 'Нет'}
💬 Команда запроса: <code>{SETTINGS['command']}</code>

📦 Аккаунтов в базе: {len(load_accounts())}
👥 Активных покупателей: {len(load_buyers())}"""
        
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=keyboard)
        bot.answer_callback_query(call.id)
    
    def toggle_setting(call: telebot.types.CallbackQuery):
        """Переключает настройки"""
        action = call.data.split(":")[-1]
        
        if action == "toggle":
            SETTINGS["enabled"] = not SETTINGS["enabled"]
        elif action == "auto":
            SETTINGS["auto_send_on_purchase"] = not SETTINGS["auto_send_on_purchase"]
        
        save_settings()
        open_settings(call)
    
    def add_account_prompt(call: telebot.types.CallbackQuery):
        """Запрашивает данные для добавления аккаунта"""
        text = """➕ <b>Добавление Steam аккаунта</b>

Отправьте данные в формате:
<code>название_аккаунта|shared_secret</code>

Пример:
<code>MyAccount|wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=</code>

Где взять shared_secret:
1. Откройте maFile вашего аккаунта
2. Найдите поле "shared_secret"
3. Скопируйте значение

Для отмены отправьте: <code>-</code>"""
        
        result = bot.send_message(call.message.chat.id, text, 
                                 reply_markup=tg_bot.static_keyboards.CLEAR_STATE_BTN())
        tg.set_state(call.message.chat.id, result.id, call.from_user.id, "steam_guard_add_account", {})
        bot.answer_callback_query(call.id)
    
    def add_account_handler(message: telebot.types.Message):
        """Обрабатывает добавление аккаунта"""
        tg.clear_state(message.chat.id, message.from_user.id, True)
        
        if message.text == "-":
            keyboard = K().add(B("◀️ К настройкам", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
            bot.reply_to(message, "❌ Добавление аккаунта отменено.", reply_markup=keyboard)
            return
        
        try:
            account_name, shared_secret = message.text.strip().split("|")
            account_name = account_name.strip()
            shared_secret = shared_secret.strip()
            
            # Проверяем, что можем сгенерировать код
            test_code = generate_steam_code(shared_secret)
            if test_code is None:
                raise ValueError("Не удалось сгенерировать код")
            
            accounts = load_accounts()
            accounts[account_name] = shared_secret
            save_accounts(accounts)
            
            keyboard = K().add(B("◀️ К настройкам", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
            bot.reply_to(message, 
                        f"✅ Аккаунт <code>{escape(account_name)}</code> успешно добавлен!\n\n"
                        f"🔐 Тестовый код: <code>{test_code}</code>", 
                        reply_markup=keyboard)
            logger.info(f"Добавлен Steam аккаунт: {account_name}")
            
        except Exception as e:
            keyboard = K().add(B("◀️ К настройкам", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
            bot.reply_to(message, 
                        f"❌ Ошибка при добавлении аккаунта:\n<code>{escape(str(e))}</code>\n\n"
                        "Проверьте формат данных.", 
                        reply_markup=keyboard)
    
    def list_accounts(call: telebot.types.CallbackQuery):
        """Показывает список аккаунтов"""
        accounts = load_accounts()
        
        if not accounts:
            text = "📋 <b>Список аккаунтов пуст</b>\n\nДобавьте аккаунты для начала работы."
        else:
            text = "📋 <b>Список Steam аккаунтов:</b>\n\n"
            for i, (account_name, secret) in enumerate(accounts.items(), 1):
                code = generate_steam_code(secret)
                text += f"{i}. <code>{escape(account_name)}</code>\n   🔐 Код: <code>{code}</code>\n\n"
        
        keyboard = K().add(B("◀️ К настройкам", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=keyboard)
        bot.answer_callback_query(call.id)
    
    def view_buyers(call: telebot.types.CallbackQuery):
        """Показывает список покупателей"""
        buyers = load_buyers()
        
        if not buyers:
            text = "👥 <b>Список покупателей пуст</b>"
        else:
            text = "👥 <b>Список покупателей с доступом:</b>\n\n"
            now = datetime.now()
            active = 0
            expired = 0
            
            for username, data in buyers.items():
                expires_at = datetime.fromisoformat(data["expires_at"])
                is_active = now < expires_at
                
                if is_active:
                    active += 1
                    days_left = (expires_at - now).days
                    status = f"✅ {days_left} дн."
                else:
                    expired += 1
                    status = "❌ Истек"
                
                text += f"• <code>{escape(username)}</code>\n"
                text += f"  📦 Аккаунт: <code>{escape(data['account_name'])}</code>\n"
                text += f"  📅 Статус: {status}\n\n"
            
            text += f"\n📊 Активных: {active} | Истекших: {expired}"
        
        keyboard = K().add(B("◀️ К настройкам", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=keyboard)
        bot.answer_callback_query(call.id)
    
    # Регистрируем обработчики
    tg.cbq_handler(open_settings, lambda c: f"{CBT.PLUGIN_SETTINGS}:{UUID}" in c.data)
    tg.cbq_handler(toggle_setting, lambda c: c.data.startswith(f"{CBT_SETTINGS}:"))
    tg.cbq_handler(add_account_prompt, lambda c: c.data == CBT_ADD_ACCOUNT)
    tg.cbq_handler(list_accounts, lambda c: c.data == CBT_LIST_ACCOUNTS)
    tg.cbq_handler(view_buyers, lambda c: c.data == CBT_VIEW_BUYERS)
    tg.msg_handler(add_account_handler, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, "steam_guard_add_account"))
    
    logger.info(f"Плагин {NAME} v{VERSION} инициализирован")


def new_order_handler(cardinal: Cardinal, event: NewOrderEvent):
    """Обрабатывает новый заказ и выдает Steam Guard код"""
    if not SETTINGS["enabled"] or not SETTINGS["auto_send_on_purchase"]:
        return
    
    order = event.order
    accounts = load_accounts()
    account_name = None
    
    # Ищем название аккаунта в описании заказа
    for acc_name in accounts.keys():
        if acc_name.lower() in order.description.lower():
            account_name = acc_name
            break
    
    if not account_name:
        logger.debug(f"Заказ #{order.id} не содержит Steam аккаунт из базы")
        return
    
    # Добавляем доступ покупателю
    add_buyer_access(order.buyer_username, account_name)
    
    # Получаем код
    code_message = get_steam_code_for_buyer(order.buyer_username)
    
    # Отправляем сообщение
    chat_id = order.chat_id
    cardinal.send_message(chat_id, code_message, order.buyer_username, watermark=False)
    
    logger.info(f"Отправлен Steam Guard код покупателю {order.buyer_username} для заказа #{order.id}")


def new_message_handler(cardinal: Cardinal, event: NewMessageEvent):
    """Обрабатывает команду запроса Steam Guard кода"""
    if not SETTINGS["enabled"]:
        return
    
    message = event.message
    
    # Проверяем, что это не наше сообщение
    if message.author_id == cardinal.account.id:
        return
    
    # Получаем текст сообщения
    if hasattr(message, 'text') and message.text:
        message_text = message.text
    else:
        message_text = str(message)
    
    # Удаляем все невидимые Unicode символы
    message_text = re.sub(r'[\u200B-\u200D\uFEFF\u00A0]', '', message_text)
    message_text = message_text.strip()
    
    # Проверяем команду
    if message_text.lower() != SETTINGS["command"].lower():
        return
    
    buyer_username = message.author
    chat_id = message.chat_id
    chat_name = message.chat_name
    
    logger.info(f"✅ Получен запрос Steam Guard кода от {buyer_username}")
    
    # Получаем код
    code_message = get_steam_code_for_buyer(buyer_username)
    
    # Отправляем сообщение
    cardinal.send_message(chat_id, code_message, chat_name, watermark=False)
    logger.info(f"✅ Отправлен код покупателю {buyer_username}")


# Привязка обработчиков к событиям
BIND_TO_PRE_INIT = [init]
BIND_TO_NEW_ORDER = [new_order_handler]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_DELETE = None
