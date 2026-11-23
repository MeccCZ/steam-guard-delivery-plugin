"""Регрессии плагина без подключения к FunPay и Telegram."""
import base64
import importlib.util
import logging
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class OrderStatuses(Enum):
    PAID = "paid"
    CLOSED = "closed"
    REFUNDED = "refunded"
    UNPAID = "unpaid"


class ApiException(Exception):
    pass


class ContinueHandling:
    pass


class Button:
    def __init__(self, text, *, callback_data):
        if not 1 <= len(callback_data.encode()) <= 64:
            raise ValueError("callback_data size")
        self.text = text
        self.callback_data = callback_data


class Keyboard:
    def __init__(self):
        self.keyboard = []

    def add(self, *buttons):
        self.keyboard.append(buttons)
        return self

    row = add


CBT = SimpleNamespace(EDIT_PLUGIN="45", PLUGIN_SETTINGS="47")
MODULES = {
    "FunPayAPI.common.enums": SimpleNamespace(OrderStatuses=OrderStatuses),
    "Utils.cardinal_tools": SimpleNamespace(ENTITY_RE=re.compile(r"\$photo=\d+|\$new|\$sleep=(\d+\.\d+|\d+)")),
    "telebot.apihelper": SimpleNamespace(ApiException=ApiException),
    "telebot.handler_backends": SimpleNamespace(ContinueHandling=ContinueHandling),
    "telebot.types": SimpleNamespace(InlineKeyboardButton=Button, InlineKeyboardMarkup=Keyboard),
    "tg_bot": SimpleNamespace(CBT=CBT),
}
PLUGIN_PATH = Path(__file__).resolve().parents[1] / "steam_guard_delivery.py"
spec = importlib.util.spec_from_file_location("steam_guard_delivery", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, MODULES):
    spec.loader.exec_module(plugin)

SECRET = base64.b64encode(b"local test fixture").decode()


class Telegram:
    def __init__(self):
        self.bot = self
        self.authorized_users = {7: {}}
        self.user_states = {}
        self.messages = []
        self.answers = []
        self.deleted = []
        self.edited = []
        self.edit_error = None
        self.next_id = 100
        self.callback_handlers = []
        self.native_commands = []
        self.message_handlers = [{
            "function": lambda msg: self.native_commands.append(msg.text),
            "filters": {"func": lambda msg: msg.text.split(maxsplit=1)[0:1] == ["/menu"]},
        }]

    def cbq_handler(self, handler, func):
        self.register_callback_query_handler(handler, func=func)

    def register_callback_query_handler(self, handler, *, func):
        self.callback_handlers.append((handler, func))

    def msg_handler(self, handler, func, content_types):
        assert content_types == ["text"]
        self.message_handlers.append({"function": handler, "filters": {"func": func}})

    def get_state(self, chat_id, user_id):
        return self.user_states.get((chat_id, user_id))

    def set_state(self, chat_id, message_id, user_id, state, data):
        self.user_states[chat_id, user_id] = {"mid": message_id, "state": state, "data": data}

    def clear_state(self, chat_id, user_id, del_msg=False):
        state = self.user_states.pop((chat_id, user_id), None)
        if state and del_msg:
            self.delete_message(chat_id, state["mid"])

    def send_message(self, chat_id, text, reply_markup=None):
        self.next_id += 1
        self.messages.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id, type="private"), message_id=self.next_id)

    def edit_message_text(self, text, chat_id, message_id, *, reply_markup, parse_mode):
        self.edited.append((text, reply_markup))
        self.last_edit_id = message_id
        if self.edit_error:
            raise self.edit_error

    def answer_callback_query(self, call_id, text=None):
        self.answers.append((call_id, text))

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    def click(self, data, *, message_id=99, chat_type="private", user_id=7):
        call = SimpleNamespace(
            id=str(len(self.answers) + 1), data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat=SimpleNamespace(id=7, type=chat_type), message_id=message_id),
        )
        for handler, matches in self.callback_handlers:
            if matches(call) and not isinstance(handler(call), ContinueHandling):
                break
        else:
            raise AssertionError(f"Unregistered callback: {data}")
        if not self.answers or self.answers[-1][0] != call.id:
            raise AssertionError(f"Unanswered callback: {data}")

    def enter(self, text, *, reply_to=None, allow_unhandled=False):
        self.next_id += 1
        message = SimpleNamespace(
            chat=SimpleNamespace(id=7, type="private"), from_user=SimpleNamespace(id=7),
            message_id=self.next_id, text=text,
            reply_to_message=SimpleNamespace(message_id=reply_to) if reply_to is not None else None,
        )
        for handler in self.message_handlers:
            if handler["filters"]["func"](message):
                handler["function"](message)
                break
        else:
            if allow_unhandled:
                return message.message_id
            raise AssertionError("No input handler")
        return message.message_id

    @staticmethod
    def find_button(keyboard, text):
        return next(button.callback_data for row in keyboard.keyboard for button in row if text in button.text)


class PluginTest(unittest.TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        paths = {name: Path(storage.name) / f"{name.split('_')[0].lower()}.json"
                 for name in ("SETTINGS_FILE", "ACCOUNTS_FILE", "BUYERS_FILE")}
        patcher = patch.multiple(plugin, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tg = Telegram()
        self.sent = []
        self.send_ok = True
        self.order = SimpleNamespace(
            id="ORDER1", status=OrderStatuses.PAID, buyer_id=24, buyer_username="Buyer",
            description="Steam аккаунт MyAccount, 1 шт.", chat_id=123, date=datetime.now(),
        )
        self.details = SimpleNamespace(
            status=OrderStatuses.PAID, seller_id=99, buyer_id=24, buyer_username="Buyer",
            chat_id="users-24-99", short_description="Steam аккаунт MyAccount",
        )
        self.account = SimpleNamespace(id=99, get_order=lambda uid: self.details)
        self.cardinal = SimpleNamespace(
            telegram=self.tg, plugins={plugin.UUID: SimpleNamespace(enabled=True)}, account=self.account,
            send_message=self.send, old_mode_enabled=False,
        )
        plugin.init(self.cardinal)
        self.tg.register_callback_query_handler(
            lambda call: self.tg.answer_callback_query(call.id),
            func=lambda call: call.data.startswith(f"{CBT.EDIT_PLUGIN}:{plugin.UUID}:"),
        )

    def send(self, chat_id, text, name, *, interlocutor_id, watermark):
        self.sent.append((chat_id, text, interlocutor_id))
        return [object()] if self.send_ok else []

    def prepare_accounts(self, **extra):
        plugin.save_json(plugin.ACCOUNTS_FILE, {"MyAccount": SECRET, **extra})

    def purchase(self):
        plugin.new_order_handler(self.cardinal, SimpleNamespace(order=self.order))

    def command(self, *, buyer_id=24, private=True, text="!code", username="Buyer"):
        message = SimpleNamespace(
            id=300, author_id=buyer_id, author=username, text=text,
            chat_id=123, chat_name=username, interlocutor_id=buyer_id if private else None,
        )
        plugin.new_message_handler(self.cardinal, SimpleNamespace(message=message))
        return message

    def test_settings_navigation_and_invalid_input_retry(self):
        self.tg.click(f"{CBT.PLUGIN_SETTINGS}:{plugin.UUID}:40")
        keyboard = self.tg.edited[-1][1]
        self.assertEqual(self.tg.find_button(keyboard, "Назад"), f"{CBT.EDIT_PLUGIN}:{plugin.UUID}:40")
        self.tg.click(self.tg.find_button(keyboard, "Срок доступа"))
        self.tg.enter("-1")
        self.assertEqual(plugin.SETTINGS["access_days"], 30)
        self.tg.enter("0")
        self.assertEqual(plugin.SETTINGS["access_days"], 0)
        self.tg.click(self.tg.find_button(self.tg.edited[-1][1], "Режим"))
        self.tg.click(self.tg.find_button(self.tg.edited[-1][1], "Лимит кодов"))
        self.tg.enter("0")
        self.tg.enter("2")
        self.assertEqual(plugin.SETTINGS["codes_limit"], 2)
        self.tg.click(plugin.callback("command", 40))
        self.tg.enter("несколько\nстрок")
        self.assertEqual(plugin.SETTINGS["command"], "!code")
        self.tg.enter("!мой-код")
        self.assertEqual(plugin.SETTINGS["command"], "!мой-код")
        self.tg.click(plugin.callback("auto", 40))
        self.tg.click(plugin.callback("toggle", 40))
        plugin.load_settings()
        self.assertFalse(plugin.SETTINGS["auto_send_on_purchase"])
        self.assertFalse(plugin.SETTINGS["enabled"])

    def test_custom_command_persists_and_only_exact_message_triggers_delivery(self):
        self.prepare_accounts()
        self.purchase()
        self.tg.click(f"{CBT.PLUGIN_SETTINGS}:{plugin.UUID}:40")
        self.tg.click(self.tg.find_button(self.tg.edited[-1][1], "Команда"), message_id=93)
        self.tg.enter("<Дай код>")
        self.assertIn("&lt;Дай код&gt;", self.tg.messages[-1][0])
        self.assertEqual(self.tg.last_edit_id, 93)
        self.assertEqual(self.tg.find_button(self.tg.edited[-1][1], "<Дай код>"), plugin.callback("command", 40))
        plugin.load_settings()
        self.assertEqual(plugin.SETTINGS["command"], "<Дай код>")
        before = len(self.sent)
        self.command(text="!code")
        self.command(text="<Дай код> пожалуйста")
        self.assertEqual(len(self.sent), before)
        self.command(text="  <дай КОД>\u200b ")
        self.assertEqual(len(self.sent), before + 1)

    def test_slash_command_is_captured_only_while_editing(self):
        self.tg.enter("/menu")
        self.tg.click(plugin.callback("command", 0))
        self.tg.enter("/menu")
        self.assertEqual(plugin.SETTINGS["command"], "/menu")
        self.assertEqual(self.tg.native_commands, ["/menu"])
        self.tg.enter("/menu")
        self.assertEqual(self.tg.native_commands, ["/menu", "/menu"])

    def test_invalid_command_keeps_prompt_and_rejects_cardinal_entities(self):
        self.tg.click(plugin.callback("command", 0))
        for command in ("?" * 65, "$sleep=5", "$photo=123", "before$newafter"):
            self.tg.enter(command)
            self.assertEqual(plugin.SETTINGS["command"], "!code")
            self.assertIsNotNone(self.tg.get_state(7, 7))
        self.tg.enter("код")
        self.assertEqual(plugin.SETTINGS["command"], "код")

    def test_saved_command_has_new_panel_if_original_message_was_removed(self):
        self.tg.click(plugin.callback("command", 20))
        self.tg.edit_error = ApiException("Bad Request: message to edit not found")
        self.tg.enter("/code")
        result, keyboard = self.tg.messages[-1]
        self.assertIn("Команда сохранена", result)
        self.assertEqual(self.tg.find_button(keyboard, "/code"), plugin.callback("command", 20))
        self.assertEqual(plugin.load_json(plugin.SETTINGS_FILE)["command"], "/code")

    def test_native_back_clears_only_own_prompt(self):
        self.tg.click(f"{CBT.PLUGIN_SETTINGS}:{plugin.UUID}:20")
        keyboard = self.tg.edited[-1][1]
        self.tg.click(self.tg.find_button(keyboard, "Команда"))
        active = self.tg.get_state(7, 7)
        self.tg.enter("не менять", reply_to=active["mid"] - 1, allow_unhandled=True)
        self.assertEqual(plugin.SETTINGS["command"], "!code")
        back = self.tg.find_button(keyboard, "Назад")
        self.tg.click(back)
        self.assertIsNone(self.tg.get_state(7, 7))
        self.tg.enter("/menu")
        self.assertEqual(self.tg.native_commands, ["/menu"])
        self.tg.set_state(7, 90, 7, "cardinal-action", {})
        self.tg.click(self.tg.find_button(keyboard, "Команда"))
        self.assertIn("отмените", self.tg.answers[-1][1])
        self.tg.click(back)
        self.assertEqual(self.tg.get_state(7, 7)["state"], "cardinal-action")

    def test_legacy_buttons_and_cancel(self):
        self.tg.click("SteamGuard_Settings:command")
        msg_id = self.tg.next_id
        cancel = self.tg.find_button(self.tg.messages[-1][1], "Отмена")
        self.tg.click(cancel, message_id=msg_id)
        self.assertIsNone(self.tg.get_state(7, 7))
        self.tg.click(cancel, message_id=msg_id)
        self.assertIn("закрыто", self.tg.answers[-1][1])
        self.tg.edit_error = ApiException("Bad Request: message is not modified")
        self.tg.click(f"{CBT.PLUGIN_SETTINGS}:{plugin.UUID}")
        self.assertIsNone(self.tg.answers[-1][1])

    def test_accounts_pagination_search_cancel_delete_and_readd(self):
        self.prepare_accounts(**{f"Account{idx}": SECRET for idx in range(12)})
        plugin.save_json(plugin.BUYERS_FILE, {"Buyer": {"account_name": "MyAccount", "expires_at": None}})
        self.tg.click(plugin.callback("accounts", 20))
        text, keyboard = self.tg.edited[-1]
        self.assertIn("Страница 1 из 3", text)
        self.tg.click(self.tg.find_button(keyboard, "➡"))
        text, keyboard = self.tg.edited[-1]
        self.assertIn("Страница 2 из 3", text)
        self.tg.click(self.tg.find_button(keyboard, "⬅"))
        text, keyboard = self.tg.edited[-1]
        self.assertIn("Страница 1 из 3", text)
        self.tg.click(self.tg.find_button(keyboard, "Поиск"))
        self.tg.enter("Unknown")
        self.tg.enter("myaccount")
        text, keyboard = self.tg.messages[-1]
        self.assertIn("MyAccount", text)
        self.tg.click(self.tg.find_button(keyboard, "Отмена"), message_id=self.tg.next_id)
        self.assertIn("MyAccount", plugin.load_json(plugin.ACCOUNTS_FILE))
        self.tg.click(plugin.callback("find_account", 20))
        self.tg.enter("MyAccount")
        text, keyboard = self.tg.messages[-1]
        delete = self.tg.find_button(keyboard, "Удалить")
        self.tg.click(delete, message_id=self.tg.next_id)
        self.assertNotIn("MyAccount", plugin.load_json(plugin.ACCOUNTS_FILE))
        self.tg.click(plugin.callback("add", 20))
        self.tg.enter("MyAccount|****")
        message_id = self.tg.enter(f"MyAccount|{SECRET}")
        self.assertIn((7, message_id), self.tg.deleted)
        self.assertNotIn(SECRET, str([text for text, _ in self.tg.messages]))
        self.assertEqual(plugin.load_json(plugin.ACCOUNTS_FILE)["MyAccount"], SECRET)
        self.assertNotIn("Buyer", plugin.load_json(plugin.BUYERS_FILE))

    def test_list_back_and_legacy_search_button(self):
        self.prepare_accounts()
        self.tg.click(f"{CBT.PLUGIN_SETTINGS}:{plugin.UUID}:35")
        settings = self.tg.edited[-1][1]
        self.tg.click(self.tg.find_button(settings, "Список покупателей"))
        text, keyboard = self.tg.edited[-1]
        self.assertIn("Список пуст", text)
        self.tg.click(self.tg.find_button(keyboard, "К настройкам"))
        settings = self.tg.edited[-1][1]
        self.assertEqual(self.tg.find_button(settings, "Назад"), f"{CBT.EDIT_PLUGIN}:{plugin.UUID}:35")
        self.tg.click("SteamGuard_RemoveAccount")
        self.assertIn("название Steam аккаунта", self.tg.messages[-1][0])
        self.tg.enter("-")
        self.assertIsNone(self.tg.get_state(7, 7))
        self.tg.click(self.tg.find_button(self.tg.messages[-1][1], "Назад"))
        self.assertIn("Steam аккаунты", self.tg.edited[-1][0])

    def test_buyer_confirm_cannot_delete_updated_record(self):
        self.prepare_accounts()
        self.purchase()
        self.tg.click(plugin.callback("buyers", 75))
        self.tg.click(self.tg.find_button(self.tg.edited[-1][1], "Поиск"))
        self.tg.enter("buyer")
        self.command()
        delete = self.tg.find_button(self.tg.messages[-1][1], "Удалить")
        self.tg.click(delete, message_id=self.tg.next_id)
        self.assertIn("изменилась", self.tg.answers[-1][1])
        self.tg.click(plugin.callback("find_buyer", 75))
        self.tg.enter("Buyer")
        delete = self.tg.find_button(self.tg.messages[-1][1], "Удалить")
        self.tg.click(delete, message_id=self.tg.next_id)
        self.assertNotIn("Buyer", plugin.load_json(plugin.BUYERS_FILE))

    def test_group_and_unauthorized_callbacks_cannot_change_state(self):
        self.tg.click(plugin.callback("toggle", 0), chat_type="group")
        self.tg.click(plugin.callback("toggle", 0), user_id=56)
        self.assertTrue(plugin.SETTINGS["enabled"])

    def test_count_limit_from_order_and_concurrent_requests(self):
        self.prepare_accounts()
        plugin.SETTINGS.update({"mode": "count", "codes_limit": 3})
        self.purchase()
        self.purchase()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "users-24-99")
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _: self.command(text=" !CODE\u200b "), range(4)))
        buyer = plugin.load_json(plugin.BUYERS_FILE)["Buyer"]
        self.assertEqual(buyer["codes_used"], 3)
        self.assertEqual(sum("Steam Guard код:" in message for _, message, _ in self.sent), 3)
        self.assertEqual(sum("исчерпан" in message for _, message, _ in self.sent), 2)

    def test_failed_delivery_does_not_consume_quota(self):
        self.prepare_accounts()
        plugin.SETTINGS.update({"mode": "count", "codes_limit": 1})
        self.send_ok = False
        with self.assertLogs(plugin.logger, level=logging.WARNING):
            self.purchase()
        self.assertEqual(plugin.load_json(plugin.BUYERS_FILE)["Buyer"]["codes_used"], 0)
        self.send_ok = True
        self.purchase()
        buyer = plugin.load_json(plugin.BUYERS_FILE)["Buyer"]
        self.assertEqual(buyer["codes_used"], 1)
        self.assertTrue(buyer["auto_sent"])

    def test_order_requires_unique_account_in_verified_lot_title(self):
        self.prepare_accounts(MyAccount2=SECRET)
        self.details.short_description = "Посторонний товар"
        self.purchase()
        self.details.short_description = "MyAccount MyAccount2"
        with self.assertLogs(plugin.logger, level=logging.WARNING):
            self.purchase()
        self.assertEqual(self.sent, [])
        self.details.short_description = "Steam MyAccount2"
        self.order.description = "Steam MyAccount2"
        self.purchase()
        self.assertEqual(plugin.load_json(plugin.BUYERS_FILE)["Buyer"]["account_name"], "MyAccount2")

    def test_older_order_does_not_restore_previous_access(self):
        self.prepare_accounts()
        self.purchase()
        self.order.id = "ORDER2"
        self.order.date += timedelta(minutes=1)
        self.purchase()
        self.order.id = "ORDER1"
        self.order.date -= timedelta(minutes=1)
        self.purchase()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(plugin.load_json(plugin.BUYERS_FILE)["Buyer"]["order_id"], "ORDER2")

    def test_legacy_access_uses_buyer_id_after_first_request(self):
        self.prepare_accounts()
        plugin.save_json(plugin.BUYERS_FILE, {
            "Buyer": {"account_name": "MyAccount", "expires_at": (datetime.now() + timedelta(days=2)).isoformat()},
        })
        self.command(private=False)
        self.assertEqual(self.sent, [])
        self.command()
        self.command(buyer_id=25)
        self.command(username="Renamed")
        self.assertIn("код:", self.sent[0][1])
        self.assertEqual(self.sent[1][1], plugin.NO_ACCESS)
        self.assertIn("код:", self.sent[2][1])
        self.assertEqual(plugin.load_json(plugin.BUYERS_FILE)["Buyer"]["buyer_id"], 24)

    def test_manual_access_when_automatic_delivery_is_off_and_refund(self):
        self.prepare_accounts()
        plugin.SETTINGS.update({"auto_send_on_purchase": False, "access_days": 0})
        self.purchase()
        self.assertEqual(self.sent, [])
        self.command()
        self.assertIn("Бессрочно", self.sent[-1][1])
        self.order.status = OrderStatuses.REFUNDED
        plugin.order_status_handler(self.cardinal, SimpleNamespace(order=self.order))
        self.command()
        self.assertEqual(self.sent[-1][1], plugin.NO_ACCESS)

    def test_old_message_mode_checks_exact_history_message(self):
        self.prepare_accounts()
        self.purchase()
        message = self.command()
        self.sent.clear()
        self.cardinal.old_mode_enabled = True
        self.command()
        self.assertEqual(self.sent, [])
        self.account.get_chat_history = lambda chat_id, **kwargs: [message]
        chat = SimpleNamespace(
            id=123, name="Buyer", node_msg_id=301,
            last_message_text="!code", last_by_bot=False, last_by_vertex=False,
        )
        plugin.last_chat_message_handler(self.cardinal, SimpleNamespace(chat=chat))
        self.assertEqual(self.sent, [])
        chat.node_msg_id = 300
        plugin.last_chat_message_handler(self.cardinal, SimpleNamespace(chat=chat))
        self.assertIn("код:", self.sent[-1][1])

    def test_headless_init_loads_settings_and_expired_access(self):
        self.prepare_accounts()
        plugin.save_json(plugin.SETTINGS_FILE, {**plugin.DEFAULT_SETTINGS, "command": "!guard"})
        self.cardinal.telegram = None
        plugin.init(self.cardinal)
        plugin.save_json(plugin.BUYERS_FILE, {
            "Buyer": {"account_name": "MyAccount", "expires_at": (datetime.now() - timedelta(days=1)).isoformat()},
        })
        self.command(text="!guard")
        self.assertIn("истёк", self.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
