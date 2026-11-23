"""Выдача Steam Guard кодов покупателям через FunPay Cardinal."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import re
import struct
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TYPE_CHECKING
from uuid import uuid4

from FunPayAPI.common.enums import OrderStatuses
from Utils.cardinal_tools import ENTITY_RE
from telebot.apihelper import ApiException
from telebot.handler_backends import ContinueHandling
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from tg_bot import CBT

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import (
        LastChatMessageChangedEvent,
        NewMessageEvent,
        NewOrderEvent,
        OrderStatusChangedEvent,
    )


NAME = "Steam Guard Delivery"
VERSION = "1.1.2"
DESCRIPTION = "Выдача Steam Guard кодов с ограничением по времени или количеству"
CREDITS = "@mecccz"
UUID = "8f7a6b5c-4d3e-4f2a-9b8c-7d6e5f4a3b2c"
SETTINGS_PAGE = True

logger = logging.getLogger("FPC.steam_guard")
STORAGE_DIR = Path("storage/plugins/steam_guard")
SETTINGS_FILE = STORAGE_DIR / "settings.json"
ACCOUNTS_FILE = STORAGE_DIR / "accounts.json"
BUYERS_FILE = STORAGE_DIR / "buyers.json"
DATA_LOCK = RLock()

DEFAULT_SETTINGS = {
    "enabled": True,
    "mode": "time",
    "access_days": 30,
    "codes_limit": 5,
    "auto_send_on_purchase": True,
    "command": "!code",
    "watermark": False,
}
SETTINGS = DEFAULT_SETTINGS.copy()
CALLBACK_PREFIX = "sgd:"
LEGACY_CALLBACKS = {
    "SteamGuard_Settings:toggle": "toggle",
    "SteamGuard_Settings:days": "days",
    "SteamGuard_Settings:auto": "auto",
    "SteamGuard_Settings:command": "command",
    "SteamGuard_AddAccount": "add",
    "SteamGuard_RemoveAccount": "find_account",
    "SteamGuard_ListAccounts": "accounts",
    "SteamGuard_ViewBuyers": "buyers",
}
STATE = "steam_guard_input"
PAGE_SIZE = 6
MAX_ACCESS_DAYS = 36500
MAX_CODES = 1000000
MAX_COMMAND_LENGTH = 64
NO_ACCESS = "❌ У вас нет активного доступа к Steam Guard кодам."
INVISIBLE_RE = re.compile(r"[\u200B-\u200D\uFEFF]")


class InputError(ValueError):
    """Ошибка ввода, которую можно показать пользователю."""


def load_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в {path}")
    if path == BUYERS_FILE and any(not isinstance(buyer, dict) for buyer in data.values()):
        raise ValueError(f"Некорректная запись покупателя в {path}")
    return data


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def normalize_command(text: str) -> str:
    return INVISIBLE_RE.sub("", text).strip()


def valid_command(command: str) -> bool:
    return (
        1 <= len(command) <= MAX_COMMAND_LENGTH
        and command != "-"
        and command.isprintable()
        and not ENTITY_RE.search(command)
    )


def load_settings() -> None:
    loaded = load_json(SETTINGS_FILE)
    SETTINGS.clear()
    SETTINGS.update(DEFAULT_SETTINGS)
    SETTINGS.update(loaded)
    if SETTINGS["mode"] not in ("time", "count"):
        SETTINGS["mode"] = "time"
    for key, (minimum, maximum) in {
        "access_days": (0, MAX_ACCESS_DAYS),
        "codes_limit": (1, MAX_CODES),
    }.items():
        value = SETTINGS[key]
        if type(value) is not int or not minimum <= value <= maximum:
            SETTINGS[key] = DEFAULT_SETTINGS[key]
    for key in ("enabled", "auto_send_on_purchase", "watermark"):
        if type(SETTINGS[key]) is not bool:
            SETTINGS[key] = DEFAULT_SETTINGS[key]
    value = SETTINGS["command"]
    command = normalize_command(value) if isinstance(value, str) else ""
    SETTINGS["command"] = command if valid_command(command) else DEFAULT_SETTINGS["command"]


def update_setting(key: str, value) -> None:
    updated = {**SETTINGS, key: value}
    save_json(SETTINGS_FILE, updated)
    SETTINGS[key] = value


def decode_secret(shared_secret: str) -> bytes:
    secret = base64.b64decode(shared_secret, validate=True)
    if not secret:
        raise ValueError("Пустой shared_secret")
    return secret


def generate_steam_code(shared_secret: str, timestamp: int | None = None) -> str | None:
    try:
        secret = decode_secret(shared_secret)
    except (ValueError, TypeError):
        return None

    if timestamp is None:
        timestamp = int(time.time())
    digest = hmac.digest(secret, struct.pack(">Q", timestamp // 30), "sha1")
    offset = digest[-1] & 0x0F
    number = struct.unpack_from(">I", digest, offset)[0] & 0x7FFFFFFF
    alphabet = "23456789BCDFGHJKMNPQRTVWXY"
    code = []
    for _ in range(5):
        number, index = divmod(number, len(alphabet))
        code.append(alphabet[index])
    return "".join(code)


def find_name(names, query: str) -> str | None:
    if query in names:
        return query
    matches = [name for name in names if name.casefold() == query.casefold()]
    return matches[0] if len(matches) == 1 else None


def find_buyer(buyers: dict, buyer_id: int, username: str | None) -> str | None:
    name = next((name for name, buyer in buyers.items() if buyer.get("buyer_id") == buyer_id), None)
    if name is not None or not username:
        return name
    name = find_name(buyers, username)
    return name if name is not None and buyers[name].get("buyer_id") is None else None


def account_matches(description: str, accounts: dict) -> list[str]:
    description = description.casefold()
    return [
        name for name in accounts
        if name and re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", description)
    ]


def safe_label(value: str) -> str:
    return escape(str(value)[:80], quote=False)


def is_count_access(buyer: dict) -> bool:
    return buyer.get("mode", "count" if "codes_limit" in buyer else "time") == "count"


def access_status(buyer: dict, accounts: dict) -> tuple[str, str | None]:
    name = buyer.get("account_name")
    if not isinstance(name, str) or name not in accounts:
        return "Аккаунт удалён", "❌ Аккаунт недоступен. Обратитесь к продавцу."

    used = buyer.get("codes_used", 0)
    if type(used) is not int or used < 0:
        return "Ошибка данных", "❌ Не удалось проверить доступ. Обратитесь к продавцу."
    if is_count_access(buyer):
        limit = buyer.get("codes_limit")
        if type(limit) is not int or limit < 1:
            return "Ошибка данных", "❌ Не удалось проверить лимит. Обратитесь к продавцу."
        status = f"Использовано {used}/{limit}"
        if used >= limit:
            return status, f"❌ Лимит кодов исчерпан ({used}/{limit})."
        return status, None

    try:
        expiry = buyer["expires_at"]
        if expiry is None:
            return f"Бессрочно · выдач: {used}", None
        expiry = datetime.fromisoformat(expiry)
    except (KeyError, TypeError, ValueError):
        return "Ошибка данных", "❌ Не удалось проверить срок доступа. Обратитесь к продавцу."
    date = expiry.strftime("%d.%m.%Y %H:%M")
    if datetime.now(expiry.tzinfo) >= expiry:
        return f"Истёк {date} · выдач: {used}", f"❌ Ваш доступ истёк {date}."
    return f"Действует до {date} · выдач: {used}", None


def send_code(
    cardinal: Cardinal, buyers: dict, username: str, accounts: dict,
    chat_id: int | str, buyer_id: int, *, automatic: bool = False,
) -> None:
    """Вызывается под DATA_LOCK, чтобы две выдачи не превысили лимит."""
    buyer = buyers[username]
    if buyer.get("buyer_id") not in (None, buyer_id):
        return
    if automatic and buyer.get("auto_sent"):
        return

    _, error = access_status(buyer, accounts)
    if error:
        if not automatic:
            cardinal.send_message(chat_id, error, username, interlocutor_id=buyer_id, watermark=False)
        return

    timestamp = int(time.time())
    code = generate_steam_code(accounts[buyer["account_name"]], timestamp)
    if code is None:
        cardinal.send_message(
            chat_id, "❌ Не удалось получить код. Обратитесь к продавцу.",
            username, interlocutor_id=buyer_id, watermark=False,
        )
        return

    updated = {**buyer, "buyer_id": buyer_id, "codes_used": buyer.get("codes_used", 0) + 1}
    if automatic:
        updated["auto_sent"] = True
    status, _ = access_status(updated, accounts)
    remaining = ""
    if is_count_access(updated):
        remaining = f"\nОсталось кодов: {updated['codes_limit'] - updated['codes_used']}"
    text = (
        f"🔐 Steam Guard код: {code}\n"
        f"⏰ Код действителен ещё {30 - timestamp % 30} сек.\n"
        f"📊 {status}{remaining}\n"
        f"💬 Для запроса нового кода напишите {SETTINGS['command']}."
    )

    # Сохраняем выдачу перед отправкой. При явном отказе Cardinal восстанавливаем лимит.
    buyers[username] = updated
    save_json(BUYERS_FILE, buyers)
    result = cardinal.send_message(
        chat_id, text, username, interlocutor_id=buyer_id, watermark=SETTINGS["watermark"],
    )
    if not result:
        buyers[username] = buyer
        save_json(BUYERS_FILE, buyers)
        logger.warning("Cardinal не подтвердил выдачу кода покупателю %s", username)


def new_order_handler(cardinal: Cardinal, event: NewOrderEvent) -> None:
    order = event.order
    if order.status not in (OrderStatuses.PAID, OrderStatuses.CLOSED) or not order.buyer_id:
        return

    with DATA_LOCK:
        if not SETTINGS["enabled"]:
            return
        accounts = load_json(ACCOUNTS_FILE)
        if not account_matches(order.description or "", accounts):
            return

    # Сверяем совпадение с заголовком оплаченного лота, отдельно от текста в списке продаж.
    details = cardinal.account.get_order(order.id)
    if (
        details.status not in (OrderStatuses.PAID, OrderStatuses.CLOSED)
        or details.seller_id != cardinal.account.id
        or details.buyer_id != order.buyer_id
    ):
        return
    username = details.buyer_username or order.buyer_username
    if not username or not details.chat_id:
        return

    with DATA_LOCK:
        if not SETTINGS["enabled"]:
            return
        accounts = load_json(ACCOUNTS_FILE)
        matches = account_matches(details.short_description or "", accounts)
        if len(matches) != 1:
            if matches:
                logger.warning("Несколько Steam аккаунтов в заголовке заказа #%s", order.id)
            return

        buyers = load_json(BUYERS_FILE)
        key = find_buyer(buyers, order.buyer_id, username)
        previous = buyers.get(key)
        if previous is None or previous.get("order_id") != order.id:
            order_date = order.date.isoformat() if order.date else None
            if previous and previous.get("order_date") and order_date and order_date < previous["order_date"]:
                return
            now = datetime.now()
            buyer = {
                "account_name": matches[0],
                "buyer_id": order.buyer_id,
                "order_id": order.id,
                "order_date": order_date,
                "purchase_date": now.isoformat(),
                "mode": SETTINGS["mode"],
                "codes_used": 0,
            }
            if buyer["mode"] == "count":
                buyer["codes_limit"] = SETTINGS["codes_limit"]
            else:
                days = SETTINGS["access_days"]
                buyer["expires_at"] = (now + timedelta(days=days)).isoformat() if days else None
            if key is not None and key != username:
                del buyers[key]
            key = username
            buyers[key] = buyer
            save_json(BUYERS_FILE, buyers)
        if SETTINGS["auto_send_on_purchase"]:
            send_code(cardinal, buyers, key, accounts, details.chat_id, order.buyer_id, automatic=True)


def order_status_handler(cardinal: Cardinal, event: OrderStatusChangedEvent) -> None:
    if event.order.status != OrderStatuses.REFUNDED:
        return
    with DATA_LOCK:
        buyers = load_json(BUYERS_FILE)
        for username, buyer in buyers.items():
            if buyer.get("order_id") == event.order.id and buyer.get("buyer_id") == event.order.buyer_id:
                del buyers[username]
                save_json(BUYERS_FILE, buyers)
                break


def is_command(text: str | None) -> bool:
    return bool(text and normalize_command(text).casefold() == SETTINGS["command"].casefold())


def handle_command(cardinal: Cardinal, message) -> None:
    buyer_id = message.author_id
    if not buyer_id or buyer_id <= 0 or buyer_id == cardinal.account.id or buyer_id != message.interlocutor_id:
        return

    with DATA_LOCK:
        if not SETTINGS["enabled"] or not is_command(message.text):
            return
        buyers = load_json(BUYERS_FILE)
        username = find_buyer(buyers, buyer_id, message.author)
        if username is None:
            cardinal.send_message(
                message.chat_id, NO_ACCESS, message.chat_name, interlocutor_id=buyer_id, watermark=False,
            )
            return
        send_code(cardinal, buyers, username, load_json(ACCOUNTS_FILE), message.chat_id, buyer_id)


def last_chat_message_handler(cardinal: Cardinal, event: LastChatMessageChangedEvent) -> None:
    chat = event.chat
    if not (
        cardinal.old_mode_enabled and SETTINGS["enabled"] and chat.name
        and not chat.last_by_bot and not chat.last_by_vertex and is_command(chat.last_message_text)
    ):
        return
    messages = cardinal.account.get_chat_history(
        chat.id, interlocutor_username=chat.name, from_id=chat.node_msg_id,
    )
    for message in messages:
        if message.id == chat.node_msg_id:
            handle_command(cardinal, message)
            break


def new_message_handler(cardinal: Cardinal, event: NewMessageEvent) -> None:
    if not cardinal.old_mode_enabled:
        handle_command(cardinal, event.message)


def callback(action: str, offset: int, value: int | str = 0) -> str:
    return f"{CALLBACK_PREFIX}{action}:{offset}:{value}"


def button(text: str, action: str, offset: int, value: int | str = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=callback(action, offset, value))


def settings_page(offset: int) -> tuple[str, InlineKeyboardMarkup]:
    accounts = load_json(ACCOUNTS_FILE)
    buyers = load_json(BUYERS_FILE)
    active = sum(access_status(buyer, accounts)[1] is None for buyer in buyers.values())
    keyboard = InlineKeyboardMarkup()
    enabled = "🟢 Включён" if SETTINGS["enabled"] else "🔴 Выключен"
    auto = "🟢" if SETTINGS["auto_send_on_purchase"] else "🔴"
    mode = "по времени" if SETTINGS["mode"] == "time" else "по количеству"
    keyboard.add(button(enabled, "toggle", offset))
    keyboard.add(button(f"🔄 Режим: {mode}", "mode", offset))
    if SETTINGS["mode"] == "time":
        days = SETTINGS["access_days"]
        label = f"{days} дн." if days else "бессрочно"
        keyboard.add(button(f"⏰ Срок доступа: {label}", "days", offset))
    else:
        keyboard.add(button(f"🔢 Лимит кодов: {SETTINGS['codes_limit']}", "limit", offset))
    keyboard.add(button(f"{auto} Код сразу после покупки", "auto", offset))
    keyboard.add(button(f"💬 Команда: {SETTINGS['command']}", "command", offset))
    keyboard.add(button("➕ Добавить аккаунт", "add", offset))
    keyboard.add(button("📋 Список аккаунтов", "accounts", offset))
    keyboard.add(button("👥 Список покупателей", "buyers", offset))
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data=f"{CBT.EDIT_PLUGIN}:{UUID}:{offset}"))
    return (
        "<b>⚙️ Настройки Steam Guard Delivery</b>\n\n"
        f"📦 Аккаунтов: {len(accounts)}\n👥 Активных покупателей: {active} из {len(buyers)}",
        keyboard,
    )


def list_page(kind: str, offset: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    accounts = load_json(ACCOUNTS_FILE)
    is_buyers = kind == "buyers"
    entries = load_json(BUYERS_FILE) if is_buyers else accounts
    names = sorted(entries, key=str.casefold)
    last_page = max(0, (len(names) - 1) // PAGE_SIZE)
    page = min(page, last_page)
    keyboard = InlineKeyboardMarkup()
    title = "Покупатели" if is_buyers else "Steam аккаунты"
    lines = [f"<b>{title}</b>", ""]
    for name in names[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        if is_buyers:
            buyer = entries[name]
            status, _ = access_status(buyer, accounts)
            lines.append(f"• <code>{safe_label(name)}</code> → <code>{safe_label(buyer.get('account_name', '—'))}</code>")
            lines.append(f"  {status}")
        else:
            code = generate_steam_code(entries[name])
            lines.append(f"• <code>{safe_label(name)}</code> · <code>{code or 'ошибка ключа'}</code>")

    if not names:
        lines.append("Список пуст.")
    if last_page:
        lines.append(f"\nСтраница {page + 1} из {last_page + 1}")
        navigation = []
        if page:
            navigation.append(button("⬅️", kind, offset, page - 1))
        if page < last_page:
            navigation.append(button("➡️", kind, offset, page + 1))
        keyboard.row(*navigation)
    if names:
        action = "find_buyer" if is_buyers else "find_account"
        keyboard.add(button("🔍 Поиск и удаление", action, offset, page))
    keyboard.add(button("◀️ К настройкам", "settings", offset))
    return "\n".join(lines), keyboard


PROMPTS = {
    "days": f"Введите срок доступа в днях от 0 до {MAX_ACCESS_DAYS}. 0 означает бессрочный доступ.",
    "limit": f"Введите лимит выдач от 1 до {MAX_CODES}.",
    "command": (
        f"Отправьте текст, который покупатель должен написать в FunPay. До {MAX_COMMAND_LENGTH} символов в одной строке.\n"
        "Например <code>!code</code>, <code>/code</code> или <code>дай код</code>."
    ),
    "add": "Отправьте <code>название_аккаунта|shared_secret</code>.",
    "find_account": "Введите название Steam аккаунта.",
    "find_buyer": "Введите ник покупателя на FunPay.",
}
SEARCH_LISTS = {"find_account": "accounts", "find_buyer": "buyers"}


def init(cardinal: Cardinal) -> None:
    try:
        load_settings()
    except (OSError, ValueError):
        SETTINGS["enabled"] = False
        logger.exception("Не удалось загрузить настройки Steam Guard. Выдача отключена.")
    tg = cardinal.telegram
    if tg is not None:
        register_telegram(cardinal)
    logger.info("Плагин %s v%s инициализирован", NAME, VERSION)


def register_telegram(cardinal: Cardinal) -> None:
    tg = cardinal.telegram
    bot = tg.bot
    settings_callback = f"{CBT.PLUGIN_SETTINGS}:{UUID}"
    back_callback = f"{CBT.EDIT_PLUGIN}:{UUID}:"

    def state_for(message, user_id):
        state = tg.get_state(message.chat.id, user_id)
        return state if state and state["state"] == STATE else None

    def answer(call, text=None):
        bot.answer_callback_query(call.id, text)

    def show(chat_id, message_id, text, keyboard):
        try:
            bot.edit_message_text(
                text, chat_id, message_id, reply_markup=keyboard, parse_mode="HTML",
            )
        except ApiException as exc:
            if "message is not modified" not in str(exc).casefold():
                raise

    def prompt(call, action, offset, page):
        token = uuid4().hex
        keyboard = InlineKeyboardMarkup().add(button("Отмена", "cancel", offset, token))
        message = bot.send_message(call.message.chat.id, PROMPTS[action], reply_markup=keyboard)
        tg.set_state(call.message.chat.id, message.message_id, call.from_user.id, STATE, {
            "action": action, "offset": offset, "page": page, "token": token,
            "kind": SEARCH_LISTS.get(action), "menu_id": call.message.message_id,
        })

    def confirm_or_cancel(call, action, token):
        state = state_for(call.message, call.from_user.id)
        if not state or state["mid"] != call.message.message_id or state["data"]["token"] != token:
            return "Это действие уже закрыто."
        data = state["data"]
        if action == "delete":
            if data["action"] != "confirm":
                return "Подтверждение устарело."
            entries = load_json(data["path"])
            name = data["name"]
            if name not in entries or entries[name] != data["entry"]:
                tg.clear_state(call.message.chat.id, call.from_user.id, True)
                return "Запись уже изменилась. Повторите поиск."
            del entries[name]
            save_json(data["path"], entries)
        tg.clear_state(call.message.chat.id, call.from_user.id)
        offset = data["offset"]
        kind = data.get("kind")
        if kind:
            text, keyboard = list_page(kind, offset, data["page"])
        else:
            text, keyboard = settings_page(offset)
        if action == "delete":
            text = "✅ Запись удалена.\n\n" + text
        show(call.message.chat.id, call.message.message_id, text, keyboard)
        return None

    def route(call):
        msg = call.message
        feedback = None
        try:
            if not msg or msg.chat.type != "private" or call.from_user.id not in tg.authorized_users:
                feedback = "Откройте настройки в личном чате с ботом."
                return
            if UUID not in cardinal.plugins:
                feedback = "Плагин удалён."
                return
            parts = call.data.split(":")
            if call.data in LEGACY_CALLBACKS:
                action, raw_offset, value = LEGACY_CALLBACKS[call.data], "0", "0"
            elif parts[0] == CALLBACK_PREFIX[:-1]:
                _, action, raw_offset, value = parts
            else:
                action, value = "settings", "0"
                raw_offset = parts[2] if len(parts) == 3 else "0"
            offset = int(raw_offset)
            if offset < 0:
                raise ValueError("Некорректная позиция списка")

            with DATA_LOCK:
                if action in ("cancel", "delete"):
                    feedback = confirm_or_cancel(call, action, value)
                    return
                page = max(0, int(value))
                existing = tg.get_state(msg.chat.id, call.from_user.id)
                if action in PROMPTS:
                    if existing and existing["state"] != STATE:
                        feedback = "Завершите или отмените открытое действие в Cardinal."
                        return
                    if existing:
                        tg.clear_state(msg.chat.id, call.from_user.id, True)
                    prompt(call, action, offset, page)
                    return
                if existing and existing["state"] == STATE:
                    tg.clear_state(msg.chat.id, call.from_user.id, existing["mid"] != msg.message_id)
                if action == "toggle":
                    update_setting("enabled", not SETTINGS["enabled"])
                elif action == "auto":
                    update_setting("auto_send_on_purchase", not SETTINGS["auto_send_on_purchase"])
                elif action == "mode":
                    update_setting("mode", "count" if SETTINGS["mode"] == "time" else "time")
                elif action in ("accounts", "buyers"):
                    show(msg.chat.id, msg.message_id, *list_page(action, offset, page))
                    return
                elif action != "settings":
                    feedback = "Откройте настройки заново."
                    return
                show(msg.chat.id, msg.message_id, *settings_page(offset))
        except (OSError, ValueError, ApiException):
            logger.exception("Не удалось обработать кнопку Steam Guard")
            feedback = "Не удалось выполнить действие. Проверьте журнал Cardinal."
        finally:
            answer(call, feedback)

    def open_confirmation(message, data, user_id):
        kind = data["kind"]
        path = BUYERS_FILE if kind == "buyers" else ACCOUNTS_FILE
        entries = load_json(path)
        name = find_name(entries, message.text.strip())
        if name is None:
            raise InputError("Запись не найдена. Введите точное имя.")
        text = f"Удалить <code>{safe_label(name)}</code>?"
        if kind == "buyers":
            status, _ = access_status(entries[name], load_json(ACCOUNTS_FILE))
            text += f"\nАккаунт: <code>{safe_label(entries[name].get('account_name', '—'))}</code>\n{status}"
        else:
            text += "\nПокупатели потеряют доступ к кодам этого аккаунта."
        token = data["token"]
        offset = data["offset"]
        keyboard = InlineKeyboardMarkup().row(
            button("Удалить", "delete", offset, token), button("Отмена", "cancel", offset, token),
        )
        confirmation = bot.send_message(message.chat.id, text, reply_markup=keyboard)
        tg.clear_state(message.chat.id, user_id, True)
        tg.set_state(message.chat.id, confirmation.message_id, user_id, STATE, {
            **data, "action": "confirm", "path": path, "name": name, "entry": entries[name],
        })

    def add_account(value: str) -> str:
        name, divider, secret = value.partition("|")
        name = name.strip()
        secret = secret.strip()
        if not divider or not 1 <= len(name) <= 64 or not name.isprintable():
            raise InputError("Введите название до 64 символов и shared_secret через |.")
        try:
            decode_secret(secret)
        except ValueError:
            raise InputError("Некорректный shared_secret. Проверьте значение из maFile.") from None
        accounts = load_json(ACCOUNTS_FILE)
        existing = find_name(accounts, name)
        if existing is not None:
            accounts[existing] = secret
            save_json(ACCOUNTS_FILE, accounts)
            return f"✅ Ключ аккаунта <code>{safe_label(existing)}</code> обновлён."
        if any(existing.casefold() == name.casefold() for existing in accounts):
            raise InputError("Найдено несколько аккаунтов. Введите название с точным регистром.")
        buyers = load_json(BUYERS_FILE)
        # Доступы удалённого аккаунта не должны перейти к новому с тем же именем.
        kept = {
            username: buyer for username, buyer in buyers.items()
            if str(buyer.get("account_name", "")).casefold() != name.casefold()
        }
        if len(kept) != len(buyers):
            save_json(BUYERS_FILE, kept)
        accounts[name] = secret
        save_json(ACCOUNTS_FILE, accounts)
        return f"✅ Аккаунт <code>{safe_label(name)}</code> добавлен."

    def apply_input(action, text):
        if action == "add":
            return add_account(text)
        if action == "command":
            command = normalize_command(text)
            if ENTITY_RE.search(command):
                raise InputError("Команда содержит служебную подстановку Cardinal. Уберите её.")
            if not valid_command(command):
                raise InputError(f"Введите от 1 до {MAX_COMMAND_LENGTH} символов в одной строке.")
            update_setting("command", command)
            return f"✅ Команда сохранена: <code>{escape(command)}</code>."
        else:
            minimum, maximum = (0, MAX_ACCESS_DAYS) if action == "days" else (1, MAX_CODES)
            if not re.fullmatch(r"[0-9]{1,7}", text) or not minimum <= int(text) <= maximum:
                raise InputError(f"Введите целое число от {minimum} до {maximum}.")
            key = "access_days" if action == "days" else "codes_limit"
            update_setting(key, int(text))
        return "✅ Настройка сохранена."

    def input_state(message):
        if (
            message.chat.type != "private" or message.from_user is None
            or message.from_user.id not in tg.authorized_users or UUID not in cardinal.plugins
        ):
            return None
        state = state_for(message, message.from_user.id)
        reply = message.reply_to_message
        if (
            state and state["data"]["action"] in PROMPTS and message.message_id > state["mid"]
            and (reply is None or reply.message_id == state["mid"])
        ):
            return state
        return None

    def on_input(message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        with DATA_LOCK:
            state = input_state(message)
            if state is None:
                return
            data = state["data"]
            action = data["action"]
            value = message.text.strip()
            offset = data["offset"]
            kind = data["kind"]
            if value == "-":
                tg.clear_state(chat_id, user_id, True)
                keyboard = InlineKeyboardMarkup().add(button("◀️ Назад", kind or "settings", offset, data["page"]))
                bot.send_message(chat_id, "Действие отменено.", reply_markup=keyboard)
                return
            if action == "add":
                try:
                    bot.delete_message(chat_id, message.message_id)
                except ApiException:
                    logger.warning("Telegram не удалил сообщение с данными аккаунта")
            try:
                if kind:
                    open_confirmation(message, data, user_id)
                    return
                result = apply_input(action, value)
            except InputError as exc:
                bot.send_message(chat_id, f"❌ {escape(str(exc))} Попробуйте ещё раз.")
                return
            except (OSError, ValueError):
                logger.exception("Не удалось обработать данные Steam Guard")
                bot.send_message(chat_id, "Не удалось обработать данные. Проверьте журнал Cardinal.")
                return
            tg.clear_state(chat_id, user_id, True)
            text, keyboard = settings_page(offset)
            try:
                show(chat_id, data["menu_id"], text, keyboard)
            except ApiException:
                bot.send_message(chat_id, f"{result}\n\n{text}", reply_markup=keyboard)
            else:
                bot.send_message(chat_id, result)

    def is_callback(call):
        data = call.data or ""
        return (
            data.startswith(CALLBACK_PREFIX) or data in LEGACY_CALLBACKS or data == settings_callback
            or data.startswith(settings_callback + ":")
        )

    def clear_before_back(call):
        message = call.message
        if message and message.chat.type == "private" and call.from_user.id in tg.authorized_users:
            with DATA_LOCK:
                state = state_for(message, call.from_user.id)
                if state:
                    tg.clear_state(message.chat.id, call.from_user.id, state["mid"] != message.message_id)
        return ContinueHandling()

    bot.register_callback_query_handler(
        clear_before_back, func=lambda call: (call.data or "").startswith(back_callback),
    )
    tg.cbq_handler(route, is_callback)
    tg.msg_handler(on_input, func=lambda message: input_state(message) is not None, content_types=["text"])
    # Пока открыта форма, текст вроде /menu должен попасть в неё раньше команд Cardinal.
    bot.message_handlers.insert(0, bot.message_handlers.pop())


BIND_TO_PRE_INIT = [init]
BIND_TO_NEW_ORDER = [new_order_handler]
BIND_TO_ORDER_STATUS_CHANGED = [order_status_handler]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [last_chat_message_handler]
BIND_TO_DELETE = None
