from __future__ import annotations

import importlib
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace


class _Logger:
    def debug(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


class _Star:
    def __init__(self, context) -> None:
        self.context = context
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key, default=None):
        return self._kv.get(key, default)

    async def put_kv_data(self, key, value) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key) -> None:
        self._kv.pop(key, None)


class _SessionFilter:
    pass


class _SessionController:
    def __init__(self) -> None:
        self.stopped = False

    def keep(self, timeout=0, reset_timeout=False) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


_SESSION_REPLIES: list["_FakeEvent"] = []


def _session_waiter(timeout=30, record_history_chains=False):
    def decorator(handler):
        async def wrapper(event, session_filter=None):
            controller = _SessionController()
            for reply in list(_SESSION_REPLIES):
                await handler(controller, reply)
                if controller.stopped:
                    break
            if not controller.stopped:
                raise TimeoutError

        return wrapper

    return decorator


def _mark_filter(name, value):
    def decorator(handler):
        setattr(handler, name, value)
        return handler

    return decorator


def _load_plugin_module():
    class _Plain:
        def __init__(self, text: str) -> None:
            self.text = text

    class _At:
        def __init__(self, *, qq: str, **kwargs) -> None:
            self.qq = qq

    class _MessageChain:
        def __init__(self, chain=None) -> None:
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(_Plain(text))
            return self

    class _Image:
        def __init__(self, path: str) -> None:
            self.path = path

        @classmethod
        def fromFileSystem(cls, path: str):
            return cls(path)

    filter_api = SimpleNamespace(
        PermissionType=SimpleNamespace(ADMIN="admin"),
        permission_type=lambda value: _mark_filter("_permission", value),
        regex=lambda value: _mark_filter("_regex", value),
    )

    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.message_components": types.ModuleType(
            "astrbot.api.message_components"
        ),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.utils": types.ModuleType("astrbot.core.utils"),
        "astrbot.core.utils.astrbot_path": types.ModuleType(
            "astrbot.core.utils.astrbot_path"
        ),
        "astrbot.core.utils.session_waiter": types.ModuleType(
            "astrbot.core.utils.session_waiter"
        ),
    }
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = _Logger()
    modules["astrbot.api"].message_components = modules[
        "astrbot.api.message_components"
    ]
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageChain = _MessageChain
    modules["astrbot.api.event"].filter = filter_api
    modules["astrbot.api.message_components"].At = _At
    modules["astrbot.api.message_components"].Plain = _Plain
    modules["astrbot.api.message_components"].Image = _Image
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = _Star
    modules["astrbot.api.star"].register = lambda *args, **kwargs: lambda cls: cls
    modules["astrbot.core.utils.session_waiter"].SessionController = _SessionController
    modules["astrbot.core.utils.session_waiter"].SessionFilter = _SessionFilter
    modules["astrbot.core.utils.session_waiter"].session_waiter = _session_waiter
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: str(
        pathlib.Path(__file__).resolve().parents[1] / ".test_data"
    )
    sys.modules.update(modules)

    package_name = "_xatu_plugin_message_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(pathlib.Path(__file__).resolve().parents[1])]
    sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.main")


PLUGIN = _load_plugin_module()


class _Config(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.save_count = 0
        self.fail_on_save = False

    def save_config(self) -> None:
        self.save_count += 1
        if self.fail_on_save:
            raise RuntimeError("save failed")


class _TokenStore:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    async def load(self) -> str | None:
        return self.token

    async def save(self, token: str) -> None:
        self.token = token

    async def clear(self) -> None:
        self.token = None


class _FakeContext:
    def __init__(self, *, fail_private: bool = False) -> None:
        self.fail_private = fail_private
        self.attempts: list[tuple[str, object]] = []
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, session: str, chain) -> bool:
        self.attempts.append((session, chain))
        if self.fail_private and ":FriendMessage:" in session:
            raise RuntimeError("private message rejected")
        self.sent.append((session, chain))
        return True


class _FakeEvent:
    def __init__(
        self,
        message: str,
        *,
        sender_id: str = "admin",
        origin: str = "default:GroupMessage:1",
        platform_id: str = "default",
        group_id: str = "1",
    ) -> None:
        self.message_str = message
        self._sender_id = sender_id
        self.unified_msg_origin = origin
        self._platform_id = platform_id
        self._group_id = group_id
        self.sent: list[str] = []
        self.stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_platform_id(self) -> str:
        return self._platform_id

    def get_group_id(self) -> str:
        return self._group_id

    def stop_event(self) -> None:
        self.stopped = True

    def plain_result(self, text: str) -> str:
        return text

    def image_result(self, path: str) -> str:
        return f"IMAGE:{path}"

    def chain_result(self, chain):
        return chain

    async def send(self, result: str) -> None:
        self.sent.append(result)


async def _collect(generator) -> list[str]:
    return [item async for item in generator]


class PluginMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_store_persists_to_config_json_and_kv(self) -> None:
        config = _Config(x_token="")
        plugin = PLUGIN.XatuElectricityPlugin(object(), config)
        store = PLUGIN.AstrBotTokenStore(plugin, config)

        await store.save("saved-token")

        self.assertEqual(config["x_token"], "saved-token")
        self.assertEqual(config.save_count, 1)
        self.assertEqual(plugin._kv["x_token"], "saved-token")
        self.assertEqual(await store.load(), "saved-token")

    async def test_token_store_migrates_legacy_kv_to_config_json(self) -> None:
        config = _Config(x_token="")
        plugin = PLUGIN.XatuElectricityPlugin(object(), config)
        plugin._kv["x_token"] = "legacy-token"
        store = PLUGIN.AstrBotTokenStore(plugin, config)

        token = await store.load()

        self.assertEqual(token, "legacy-token")
        self.assertEqual(config["x_token"], "legacy-token")
        self.assertEqual(config.save_count, 1)

    async def test_query_message_returns_requested_format(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())

        class Backend:
            async def get_balance(self, building_id, room_name):
                self.args = (building_id, room_name)
                return SimpleNamespace(
                    balance=Decimal("209.399902343750"),
                    fetched_at=datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
                    room_id="12566",
                    room_name="3128空调",
                )

        backend = Backend()
        plugin.backend = backend
        event = _FakeEvent("查询电费 3-128")

        replies = await _collect(plugin.query_electricity(event))

        self.assertEqual(backend.args, ("3", "3128"))
        self.assertEqual(replies, ["查询到3号公寓128宿舍电费余额为：209.39990234375"])
        self.assertTrue(event.stopped)
        self.assertIn("3-128", plugin._kv[PLUGIN.BALANCE_HISTORY_KEY])

    async def test_statistics_message_renders_local_history_image(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())

        class Backend:
            async def get_balance(self, building_id, room_name):
                return SimpleNamespace(
                    balance=Decimal("209.399902343750"),
                    fetched_at=datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
                    room_id="12566",
                    room_name="3128空调",
                )

        plugin.backend = Backend()
        await _collect(plugin.query_electricity(_FakeEvent("查询电费 3-128")))

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin._chart_dir = pathlib.Path(temp_dir)
            replies = await _collect(
                plugin.electricity_statistics(_FakeEvent("电费统计 3-128"))
            )

            self.assertEqual(len(replies), 1)
            chain = replies[0]
            self.assertIn("3号公寓128宿舍近30天电费记录：1条", chain[0].text)
            self.assertTrue(pathlib.Path(chain[1].path).is_file())

    async def test_statistics_message_reports_missing_history(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())
        event = _FakeEvent("电费统计 3-128")

        replies = await _collect(plugin.electricity_statistics(event))

        self.assertEqual(
            replies,
            ["还没有3号公寓128宿舍的本地记录，请先使用“查询电费”。"],
        )
        self.assertTrue(event.stopped)

    async def test_query_history_skips_records_within_one_hour(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())

        class Backend:
            def __init__(self) -> None:
                self.timestamp = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)

            async def get_balance(self, building_id, room_name):
                return SimpleNamespace(
                    balance=Decimal("209.399902343750"),
                    fetched_at=self.timestamp,
                    room_id="12566",
                    room_name="3128空调",
                )

        backend = Backend()
        plugin.backend = backend

        await _collect(plugin.query_electricity(_FakeEvent("查询电费 3-128")))
        backend.timestamp += timedelta(minutes=30)
        await _collect(plugin.query_electricity(_FakeEvent("查询电费 3-128")))

        records = plugin._kv[PLUGIN.BALANCE_HISTORY_KEY]["3-128"]
        self.assertEqual(len(records), 1)

    async def test_query_history_records_again_after_one_hour(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())

        class Backend:
            def __init__(self) -> None:
                self.timestamp = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)

            async def get_balance(self, building_id, room_name):
                return SimpleNamespace(
                    balance=Decimal("209.399902343750"),
                    fetched_at=self.timestamp,
                    room_id="12566",
                    room_name="3128空调",
                )

        backend = Backend()
        plugin.backend = backend

        await _collect(plugin.query_electricity(_FakeEvent("查询电费 3-128")))
        backend.timestamp += timedelta(hours=1)
        await _collect(plugin.query_electricity(_FakeEvent("查询电费 3-128")))

        records = plugin._kv[PLUGIN.BALANCE_HISTORY_KEY]["3-128"]
        self.assertEqual(len(records), 2)

    async def test_set_and_cancel_electricity_alert(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(_FakeContext(), _Config())
        event = _FakeEvent("设置预警 3-128", sender_id="10001")

        replies = await _collect(plugin.set_electricity_alert(event))

        self.assertEqual(
            replies,
            ["已设置3号公寓128宿舍电费预警。每天13:00自动查询，寒暑假期间暂停。"],
        )
        stored = plugin._kv[PLUGIN.ELECTRICITY_ALERTS_KEY]
        subscription = stored["default:10001:3-128"]
        self.assertEqual(subscription["private_umo"], "default:FriendMessage:10001")
        self.assertEqual(subscription["fallback_group_umo"], "default:GroupMessage:1")

        cancel_event = _FakeEvent("取消预警 3-128", sender_id="10001")
        replies = await _collect(plugin.cancel_electricity_alert(cancel_event))

        self.assertEqual(replies, ["已取消3号公寓128宿舍电费预警。"])
        self.assertEqual(plugin._kv[PLUGIN.ELECTRICITY_ALERTS_KEY], {})

    async def test_scheduled_check_queries_shared_room_once_and_no_repeats(
        self,
    ) -> None:
        context = _FakeContext()
        plugin = PLUGIN.XatuElectricityPlugin(context, _Config())

        class Backend:
            def __init__(self) -> None:
                self.calls = 0

            async def get_balance(self, building_id, room_name):
                self.calls += 1
                return SimpleNamespace(
                    balance=Decimal("20"),
                    fetched_at=datetime(2026, 6, 18, 5, 0, tzinfo=timezone.utc),
                    room_id="12566",
                    room_name="3128空调",
                )

        backend = Backend()
        plugin.backend = backend
        await _collect(
            plugin.set_electricity_alert(
                _FakeEvent("设置预警 3-128", sender_id="10001")
            )
        )
        await _collect(
            plugin.set_electricity_alert(
                _FakeEvent("设置预警 3-128", sender_id="10002")
            )
        )

        await plugin.run_scheduled_alert_check(
            datetime(2026, 6, 18, 13, 0, tzinfo=timezone(timedelta(hours=8)))
        )

        self.assertEqual(backend.calls, 1)
        self.assertEqual(len(context.sent), 2)
        self.assertTrue(
            all(":FriendMessage:" in session for session, _ in context.sent)
        )
        self.assertEqual(len(plugin._kv[PLUGIN.BALANCE_HISTORY_KEY]["3-128"]), 1)
        for subscription in plugin._kv[PLUGIN.ELECTRICITY_ALERTS_KEY].values():
            self.assertEqual(subscription["last_balance"], "20")

        await plugin.run_scheduled_alert_check(
            datetime(2026, 6, 19, 13, 0, tzinfo=timezone(timedelta(hours=8)))
        )

        self.assertEqual(backend.calls, 2)
        self.assertEqual(len(context.sent), 2)

    async def test_private_alert_failure_falls_back_to_setting_group(self) -> None:
        context = _FakeContext(fail_private=True)
        plugin = PLUGIN.XatuElectricityPlugin(context, _Config())

        class Backend:
            async def get_balance(self, building_id, room_name):
                return SimpleNamespace(
                    balance=Decimal("5"),
                    fetched_at=datetime(2026, 6, 18, 5, 0, tzinfo=timezone.utc),
                    room_id="12566",
                    room_name="3128空调",
                )

        plugin.backend = Backend()
        await _collect(
            plugin.set_electricity_alert(
                _FakeEvent("设置预警 3-128", sender_id="10001")
            )
        )

        await plugin.run_scheduled_alert_check(
            datetime(2026, 6, 18, 13, 0, tzinfo=timezone(timedelta(hours=8)))
        )

        self.assertEqual(context.attempts[0][0], "default:FriendMessage:10001")
        self.assertEqual(context.sent[0][0], "default:GroupMessage:1")
        fallback_chain = context.sent[0][1]
        self.assertEqual(fallback_chain.chain[0].qq, "10001")
        self.assertIn("请添加机器人为好友", fallback_chain.chain[1].text)

    async def test_scheduled_check_skips_default_summer_vacation(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(_FakeContext(), _Config())

        class Backend:
            def __init__(self) -> None:
                self.calls = 0

            async def get_balance(self, building_id, room_name):
                self.calls += 1
                raise AssertionError("vacation query must not run")

        backend = Backend()
        plugin.backend = backend
        await _collect(
            plugin.set_electricity_alert(
                _FakeEvent("设置预警 3-128", sender_id="10001")
            )
        )

        await plugin.run_scheduled_alert_check(
            datetime(2026, 7, 10, 13, 0, tzinfo=timezone(timedelta(hours=8)))
        )

        self.assertEqual(backend.calls, 0)

    async def test_admin_credential_flow_consumes_two_messages(self) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())
        observed: list[tuple[str, str]] = []

        async def apply_credentials(username: str, password: str) -> None:
            observed.append((username, password))

        plugin.apply_credentials = apply_credentials
        account_event = _FakeEvent("account")
        password_event = _FakeEvent("password")
        _SESSION_REPLIES[:] = [account_event, password_event]

        initial_event = _FakeEvent("设置账密")
        replies = await _collect(plugin.setup_credentials(initial_event))

        self.assertEqual(replies, ["请输入账号"])
        self.assertEqual(account_event.sent, ["请输入密码"])
        self.assertEqual(password_event.sent, ["账密设置成功！Token已保存"])
        self.assertEqual(observed, [("account", "password")])
        self.assertTrue(account_event.stopped)
        self.assertTrue(password_event.stopped)

    async def test_admin_credential_flow_registers_waiter_after_first_reply(
        self,
    ) -> None:
        plugin = PLUGIN.XatuElectricityPlugin(object(), _Config())
        initial_event = _FakeEvent("设置账密")
        generator = plugin.setup_credentials(initial_event)

        first_reply = await anext(generator)

        self.assertEqual(first_reply, "请输入账号")
        self.assertFalse(initial_event.stopped)
        await generator.aclose()

    async def test_apply_credentials_saves_config_after_validation(self) -> None:
        config = _Config(username="old", password="old")
        plugin = PLUGIN.XatuElectricityPlugin(object(), config)
        plugin._token_store = _TokenStore("old-token")

        class OldBackend:
            def __init__(self) -> None:
                self.closed = False

            async def aclose(self) -> None:
                self.closed = True

        class Candidate:
            def __init__(self, config, token_store) -> None:
                self.config = config
                self.token_store = token_store
                self.force_login = False
                self.closed = False

            async def ensure_token(self, *, force_login=False) -> str:
                self.force_login = force_login
                return "token"

            async def aclose(self) -> None:
                self.closed = True

        old_backend = OldBackend()
        plugin.backend = old_backend
        original_client = PLUGIN.XatuElectricityClient
        PLUGIN.XatuElectricityClient = Candidate
        try:
            await plugin.apply_credentials("new-user", "new-password")
        finally:
            PLUGIN.XatuElectricityClient = original_client

        self.assertEqual(config["username"], "new-user")
        self.assertEqual(config["password"], "new-password")
        self.assertEqual(config.save_count, 1)
        self.assertIsInstance(plugin.backend, Candidate)
        self.assertTrue(plugin.backend.force_login)
        self.assertTrue(old_backend.closed)

    async def test_apply_credentials_rolls_back_when_config_save_fails(self) -> None:
        config = _Config(username="old-user", password="old-password")
        config.fail_on_save = True
        plugin = PLUGIN.XatuElectricityPlugin(object(), config)

        token_store = _TokenStore("old-token")
        plugin._token_store = token_store

        class OldBackend:
            pass

        class Candidate:
            def __init__(self, client_config, candidate_token_store) -> None:
                self.token_store = candidate_token_store
                self.closed = False

            async def ensure_token(self, *, force_login=False) -> str:
                await self.token_store.save("new-token")
                return "new-token"

            async def aclose(self) -> None:
                self.closed = True

        old_backend = OldBackend()
        plugin.backend = old_backend
        original_client = PLUGIN.XatuElectricityClient
        PLUGIN.XatuElectricityClient = Candidate
        try:
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                await plugin.apply_credentials("new-user", "new-password")
        finally:
            PLUGIN.XatuElectricityClient = original_client

        self.assertEqual(config["username"], "old-user")
        self.assertEqual(config["password"], "old-password")
        self.assertEqual(token_store.token, "old-token")
        self.assertIs(plugin.backend, old_backend)


if __name__ == "__main__":
    unittest.main()
