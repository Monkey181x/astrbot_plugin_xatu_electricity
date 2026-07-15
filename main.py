from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionFilter,
    session_waiter,
)

from .xatu_electricity import ClientConfig, ElectricityBalance, TokenStore
from .xatu_electricity.alerts import (
    DEFAULT_ALERT_HOUR,
    DEFAULT_SUMMER_VACATION,
    DEFAULT_WINTER_VACATION,
    SHANGHAI_TIMEZONE,
    AlertSubscription,
    alert_key,
    crossed_threshold,
    dump_alerts,
    is_vacation_date,
    load_alerts,
    next_daily_run,
    parse_month_day,
)
from .xatu_electricity.client import XatuElectricityClient
from .xatu_electricity.exceptions import AuthenticationError, RoomNotFoundError
from .xatu_electricity.history import (
    BalanceHistoryRecord,
    dump_history,
    history_key,
    load_history,
    make_balance_record,
    prune_history,
    render_balance_chart,
    should_append_record,
)
from .xatu_electricity.interaction import (
    DormitoryQuery,
    format_balance,
    parse_cancel_alert_query,
    parse_dormitory_query,
    parse_set_alert_query,
    parse_statistics_query,
)

BALANCE_HISTORY_KEY = "balance_history_v1"
ELECTRICITY_ALERTS_KEY = "electricity_alerts_v1"


class SenderFilter(SessionFilter):
    """Limit credential setup replies to the initiating administrator."""

    def filter(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())


class AstrBotTokenStore(TokenStore):
    """Persist X-Token in plugin config JSON, with KV compatibility."""

    KEY = "x_token"

    def __init__(self, plugin: Star, config: AstrBotConfig) -> None:
        self._plugin = plugin
        self._config = config

    async def load(self) -> str | None:
        value = self._config.get(self.KEY, "")
        if isinstance(value, str) and value:
            return value

        legacy_value = await self._plugin.get_kv_data(self.KEY, None)
        if isinstance(legacy_value, str) and legacy_value:
            self._config[self.KEY] = legacy_value
            try:
                self._config.save_config()
            except Exception:
                logger.exception("Failed to migrate XATU X-Token from KV to config")
            return legacy_value
        return None

    async def save(self, token: str) -> None:
        previous = self._config.get(self.KEY, "")
        self._config[self.KEY] = token
        try:
            self._config.save_config()
        except Exception:
            self._config[self.KEY] = previous
            raise
        try:
            await self._plugin.put_kv_data(self.KEY, token)
        except Exception:
            logger.exception("Failed to mirror XATU X-Token to plugin KV")

    async def clear(self) -> None:
        previous = self._config.get(self.KEY, "")
        self._config[self.KEY] = ""
        try:
            self._config.save_config()
        except Exception:
            self._config[self.KEY] = previous
            raise
        try:
            await self._plugin.delete_kv_data(self.KEY)
        except Exception:
            logger.exception("Failed to clear mirrored XATU X-Token from plugin KV")


@register(
    "astrbot_plugin_xatu_electricity",
    "Monke",
    "XATU electricity query backend",
    "0.4.0",
)
class XatuElectricityPlugin(Star):
    """Electricity query, history, chart, and scheduled alert plugin."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.backend: XatuElectricityClient | None = None
        self._token_store: AstrBotTokenStore | None = None
        self._backend_lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self._alert_lock = asyncio.Lock()
        self._alert_task: asyncio.Task[None] | None = None
        self._chart_dir: Path | None = None

    async def initialize(self) -> None:
        initial_token_env = str(self.config.get("initial_token_env", "XATU_X_TOKEN"))

        self._token_store = AstrBotTokenStore(self, self.config)
        initial_token = os.getenv(initial_token_env, "")
        if initial_token and not await self._token_store.load():
            await self._token_store.save(initial_token)

        has_saved_token = bool(await self._token_store.load())
        logger.info(
            "XATU electricity authentication state: credentials=%s, x_token=%s",
            "configured"
            if self.config.get("username") and self.config.get("password")
            else "missing",
            "saved" if has_saved_token else "missing",
        )

        self.backend = XatuElectricityClient(
            self._build_client_config(), self._token_store
        )

        if not self.backend.config.username or not self.backend.config.password:
            logger.warning(
                "XATU credentials are not configured. Queries can use a "
                "cached token, but CAS re-login will fail."
            )

        if bool(self.config.get("prewarm_auth", False)):
            await self.backend.ensure_token()
            logger.info("XATU electricity backend authentication is ready.")

        self._alert_task = asyncio.create_task(
            self._alert_scheduler(),
            name="xatu-electricity-alert-scheduler",
        )

    def _build_client_config(
        self, username: str | None = None, password: str | None = None
    ) -> ClientConfig:
        return ClientConfig(
            username=(
                str(self.config.get("username", "")) if username is None else username
            ),
            password=(
                str(self.config.get("password", "")) if password is None else password
            ),
            project_id=str(
                self.config.get("project_id", "88827410e9214c81a886f6e1dcb20dcc")
            ),
            area_id=str(self.config.get("area_id", "1")),
            request_timeout_seconds=float(
                self.config.get("request_timeout_seconds", 20.0)
            ),
            verify_tls=bool(self.config.get("verify_tls", True)),
        )

    async def query_balance(
        self, building_id: str, room_name: str
    ) -> ElectricityBalance:
        async with self._backend_lock:
            if self.backend is None:
                raise RuntimeError("XATU electricity backend is not initialized")
            return await self.backend.get_balance(building_id, room_name)

    async def _load_alerts(self) -> dict[str, AlertSubscription]:
        raw_alerts = await self.get_kv_data(ELECTRICITY_ALERTS_KEY, {})
        return load_alerts(raw_alerts)

    async def _save_alerts(self, alerts: dict[str, AlertSubscription]) -> None:
        await self.put_kv_data(ELECTRICITY_ALERTS_KEY, dump_alerts(alerts))

    def _get_alert_hour(self) -> int:
        try:
            hour = int(self.config.get("alert_check_hour", DEFAULT_ALERT_HOUR))
        except (TypeError, ValueError):
            return DEFAULT_ALERT_HOUR
        return hour if 0 <= hour <= 23 else DEFAULT_ALERT_HOUR

    def _get_vacation_windows(
        self,
    ) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
        winter_start, winter_end = DEFAULT_WINTER_VACATION
        summer_start, summer_end = DEFAULT_SUMMER_VACATION
        return (
            (
                parse_month_day(
                    self.config.get("winter_vacation_start", "02-01"),
                    winter_start,
                ),
                parse_month_day(
                    self.config.get("winter_vacation_end", "03-01"),
                    winter_end,
                ),
            ),
            (
                parse_month_day(
                    self.config.get("summer_vacation_start", "07-10"),
                    summer_start,
                ),
                parse_month_day(
                    self.config.get("summer_vacation_end", "08-31"),
                    summer_end,
                ),
            ),
        )

    async def _alert_scheduler(self) -> None:
        while True:
            now = datetime.now(SHANGHAI_TIMEZONE)
            next_run = next_daily_run(now, self._get_alert_hour())
            delay = max((next_run - now).total_seconds(), 0.0)
            logger.info(
                "Next XATU electricity alert check: %s",
                next_run.strftime("%Y-%m-%d %H:%M:%S %Z"),
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

            try:
                await self.run_scheduled_alert_check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected failure in electricity alert scheduler")

    async def run_scheduled_alert_check(self, now: datetime | None = None) -> None:
        local_now = now or datetime.now(SHANGHAI_TIMEZONE)
        if local_now.tzinfo is None:
            local_now = local_now.replace(tzinfo=SHANGHAI_TIMEZONE)
        else:
            local_now = local_now.astimezone(SHANGHAI_TIMEZONE)

        if is_vacation_date(local_now.date(), self._get_vacation_windows()):
            logger.info(
                "Skipping XATU electricity alert check during vacation: %s",
                local_now.date().isoformat(),
            )
            return

        async with self._alert_lock:
            alerts = await self._load_alerts()
        if not alerts:
            return

        grouped_alerts: dict[tuple[str, str], list[str]] = {}
        for key, subscription in alerts.items():
            room_key = (subscription.building_id, subscription.dormitory_number)
            grouped_alerts.setdefault(room_key, []).append(key)

        for subscription_keys in grouped_alerts.values():
            subscription = alerts[subscription_keys[0]]
            query = DormitoryQuery(
                dormitory_number=subscription.dormitory_number,
                building_id=subscription.building_id,
                room_number=subscription.room_number,
                display_building_id=subscription.display_building_id,
            )
            try:
                result = await self.query_balance(
                    query.building_id, query.dormitory_number
                )
            except Exception:
                logger.exception(
                    "Failed scheduled electricity query for building %s room %s",
                    query.building_id,
                    query.dormitory_number,
                )
                continue

            try:
                await self.record_balance_query(query, result)
            except Exception:
                logger.exception(
                    "Failed to record scheduled electricity history for %s-%s",
                    query.display_building_id,
                    query.room_number,
                )

            try:
                notifications = await self._update_alert_balances(
                    subscription_keys, result.balance
                )
            except Exception:
                logger.exception(
                    "Failed to update electricity alert state for %s-%s",
                    query.display_building_id,
                    query.room_number,
                )
                continue

            for current_subscription, threshold in notifications:
                await self._send_alert_notification(
                    current_subscription,
                    threshold,
                    result.balance,
                )

    async def _update_alert_balances(
        self,
        subscription_keys: list[str],
        balance: Decimal,
    ) -> list[tuple[AlertSubscription, Decimal]]:
        notifications: list[tuple[AlertSubscription, Decimal]] = []
        async with self._alert_lock:
            alerts = await self._load_alerts()
            changed = False
            for key in subscription_keys:
                subscription = alerts.get(key)
                if subscription is None:
                    continue
                threshold = crossed_threshold(subscription.last_balance, balance)
                updated = subscription.with_balance(balance)
                alerts[key] = updated
                changed = True
                if threshold is not None:
                    notifications.append((updated, threshold))
            if changed:
                await self._save_alerts(alerts)
        return notifications

    async def _send_alert_notification(
        self,
        subscription: AlertSubscription,
        threshold: Decimal,
        balance: Decimal,
    ) -> None:
        message = (
            f"电费预警：{subscription.display_building_id}号公寓"
            f"{subscription.room_number}宿舍当前余额为"
            f"{format_balance(balance)}，已达到{format_balance(threshold)}元"
            "预警线，请及时充值。"
        )
        try:
            sent = await self.context.send_message(
                subscription.private_umo,
                MessageChain().message(message),
            )
            if sent:
                return
            logger.warning(
                "No platform matched private alert session for user %s",
                subscription.user_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send private electricity alert to user %s: %s",
                subscription.user_id,
                exc,
            )

        if not subscription.fallback_group_umo:
            logger.warning(
                "Electricity alert has no fallback group for user %s",
                subscription.user_id,
            )
            return

        fallback_chain = MessageChain(
            [
                Comp.At(qq=subscription.user_id),
                Comp.Plain(
                    f" {message} 私聊发送失败，请添加机器人为好友，"
                    "以便后续接收私聊预警。"
                ),
            ]
        )
        try:
            sent = await self.context.send_message(
                subscription.fallback_group_umo,
                fallback_chain,
            )
            if not sent:
                logger.warning(
                    "No platform matched fallback alert session for user %s",
                    subscription.user_id,
                )
        except Exception:
            logger.exception(
                "Failed to send fallback electricity alert to user %s",
                subscription.user_id,
            )

    async def _load_balance_history(
        self,
    ) -> dict[str, list[BalanceHistoryRecord]]:
        raw_history = await self.get_kv_data(BALANCE_HISTORY_KEY, {})
        return prune_history(load_history(raw_history))

    async def _save_balance_history(
        self, history: dict[str, list[BalanceHistoryRecord]]
    ) -> None:
        await self.put_kv_data(
            BALANCE_HISTORY_KEY, dump_history(prune_history(history))
        )

    async def record_balance_query(
        self,
        query,
        result: ElectricityBalance,
    ) -> None:
        record = make_balance_record(
            display_building_id=query.display_building_id,
            building_id=query.building_id,
            room_number=query.room_number,
            dormitory_number=query.dormitory_number,
            room_id=result.room_id,
            room_name=result.room_name,
            balance=result.balance,
            timestamp=result.fetched_at,
        )
        key = history_key(query.display_building_id, query.room_number)
        async with self._history_lock:
            history = await self._load_balance_history()
            records = history.setdefault(key, [])
            if should_append_record(records, record.timestamp):
                records.append(record)
            await self._save_balance_history(history)

    async def get_balance_records(self, query) -> list[BalanceHistoryRecord]:
        key = history_key(query.display_building_id, query.room_number)
        async with self._history_lock:
            history = await self._load_balance_history()
            await self._save_balance_history(history)
            return list(history.get(key, []))

    def _get_chart_dir(self) -> Path:
        if self._chart_dir is not None:
            return self._chart_dir
        self._chart_dir = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_xatu_electricity"
            / "charts"
        )
        return self._chart_dir

    def _build_chart_path(self, query) -> Path:
        chart_dir = self._get_chart_dir()
        chart_dir.mkdir(parents=True, exist_ok=True)
        safe_key = f"{query.display_building_id}-{query.room_number}"
        return chart_dir / f"balance_{safe_key}_{time.time_ns()}.png"

    async def apply_credentials(self, username: str, password: str) -> None:
        """Validate credentials, persist them, and activate the new client."""

        if self._token_store is None:
            raise RuntimeError("XATU electricity backend is not initialized")

        old_token = await self._token_store.load()
        old_username = str(self.config.get("username", ""))
        old_password = str(self.config.get("password", ""))
        candidate = XatuElectricityClient(
            self._build_client_config(username, password), self._token_store
        )
        promoted = False
        old_backend: XatuElectricityClient | None = None
        try:
            await candidate.ensure_token(force_login=True)
            self.config["username"] = username
            self.config["password"] = password
            try:
                self.config.save_config()
            except Exception:
                self.config["username"] = old_username
                self.config["password"] = old_password
                if old_token:
                    await self._token_store.save(old_token)
                else:
                    await self._token_store.clear()
                raise

            async with self._backend_lock:
                old_backend = self.backend
                self.backend = candidate
                promoted = True
        finally:
            if not promoted:
                await candidate.aclose()

        if old_backend is not None:
            try:
                await old_backend.aclose()
            except Exception:
                logger.exception("Failed to close the replaced XATU backend")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.regex(r"^/?设置账密\s*$")
    async def setup_credentials(self, event: AstrMessageEvent):
        """管理员交互式设置统一身份认证账号和密码。"""

        state: dict[str, str | None] = {"username": None}
        yield event.plain_result("请输入账号")

        @session_waiter(timeout=180, record_history_chains=False)
        async def credential_waiter(
            controller: SessionController, reply_event: AstrMessageEvent
        ) -> None:
            reply_event.stop_event()
            if state["username"] is None:
                state["username"] = reply_event.message_str.strip()
                await reply_event.send(reply_event.plain_result("请输入密码"))
                controller.keep(timeout=180, reset_timeout=True)
                return

            username = state["username"]
            password = reply_event.message_str
            try:
                assert username is not None
                await self.apply_credentials(username, password)
            except Exception as exc:
                logger.exception("Failed to validate and save XATU credentials")
                await reply_event.send(
                    reply_event.plain_result(f"账密设置失败，无法获取Token：{exc}")
                )
            else:
                await reply_event.send(
                    reply_event.plain_result("账密设置成功！Token已保存")
                )
            finally:
                controller.stop()

        try:
            await credential_waiter(event, session_filter=SenderFilter())
        except TimeoutError:
            yield event.plain_result("账密设置已超时，请重新发送“设置账密”。")
        finally:
            event.stop_event()

    @filter.regex(r"^/?查询电费.*$")
    async def query_electricity(self, event: AstrMessageEvent):
        """根据四位宿舍号查询当前剩余电费。"""

        event.stop_event()
        query = parse_dormitory_query(event.message_str)
        if query is None:
            yield event.plain_result(
                "格式错误，请使用：查询电费 公寓号-宿舍号（例如：查询电费 3-128）"
            )
            return

        try:
            result = await self.query_balance(query.building_id, query.dormitory_number)
        except RoomNotFoundError:
            yield event.plain_result(
                f"呜~小助手没有找到{query.display_building_id}号公寓{query.room_number}宿舍的电费信息TAT"
            )
        except AuthenticationError as exc:
            logger.exception(
                "Failed to authenticate electricity query for building %s room %s",
                query.building_id,
                query.dormitory_number,
            )
            yield event.plain_result(f"电费查询认证失败：{exc}")
        except Exception:
            logger.exception(
                "Failed to query electricity balance for building %s room %s",
                query.building_id,
                query.dormitory_number,
            )
            yield event.plain_result("电费查询失败，请稍后重试。")
        else:
            try:
                await self.record_balance_query(query, result)
            except Exception:
                logger.exception(
                    "Failed to record electricity balance history for %s-%s",
                    query.display_building_id,
                    query.room_number,
                )
            yield event.plain_result(
                f"查询到{query.display_building_id}号公寓"
                f"{query.room_number}宿舍电费余额为："
                f"{format_balance(result.balance)}"
            )

    @filter.regex(r"^/?设置预警.*$")
    async def set_electricity_alert(self, event: AstrMessageEvent):
        """为当前用户设置指定宿舍的每日余额预警。"""

        event.stop_event()
        query = parse_set_alert_query(event.message_str)
        if query is None:
            yield event.plain_result(
                "格式错误，请使用：设置预警 公寓号-宿舍号（例如：设置预警 3-128）"
            )
            return

        platform_id = str(event.get_platform_id())
        user_id = str(event.get_sender_id())
        key = alert_key(
            platform_id,
            user_id,
            query.display_building_id,
            query.room_number,
        )
        fallback_group_umo = event.unified_msg_origin if event.get_group_id() else None
        async with self._alert_lock:
            alerts = await self._load_alerts()
            previous = alerts.get(key)
            subscription = AlertSubscription(
                platform_id=platform_id,
                user_id=user_id,
                private_umo=f"{platform_id}:FriendMessage:{user_id}",
                fallback_group_umo=(
                    fallback_group_umo
                    or (previous.fallback_group_umo if previous else None)
                ),
                display_building_id=query.display_building_id,
                building_id=query.building_id,
                room_number=query.room_number,
                dormitory_number=query.dormitory_number,
                last_balance=previous.last_balance if previous else None,
            )
            alerts[key] = subscription
            await self._save_alerts(alerts)

        yield event.plain_result(
            f"已设置{query.display_building_id}号公寓{query.room_number}宿舍电费预警。"
            f"每天{self._get_alert_hour():02d}:00自动查询，寒暑假期间暂停。"
        )

    @filter.regex(r"^/?取消预警.*$")
    async def cancel_electricity_alert(self, event: AstrMessageEvent):
        """取消当前用户指定宿舍的余额预警。"""

        event.stop_event()
        query = parse_cancel_alert_query(event.message_str)
        if query is None:
            yield event.plain_result(
                "格式错误，请使用：取消预警 公寓号-宿舍号（例如：取消预警 3-128）"
            )
            return

        key = alert_key(
            str(event.get_platform_id()),
            str(event.get_sender_id()),
            query.display_building_id,
            query.room_number,
        )
        async with self._alert_lock:
            alerts = await self._load_alerts()
            removed = alerts.pop(key, None)
            if removed is not None:
                await self._save_alerts(alerts)

        if removed is None:
            yield event.plain_result(
                f"你尚未设置{query.display_building_id}号公寓"
                f"{query.room_number}宿舍的电费预警。"
            )
            return
        yield event.plain_result(
            f"已取消{query.display_building_id}号公寓{query.room_number}宿舍电费预警。"
        )

    @filter.regex(r"^/?电费统计.*$")
    async def electricity_statistics(self, event: AstrMessageEvent):
        """根据本地 30 天查询记录生成电费余额折线图。"""

        event.stop_event()
        query = parse_statistics_query(event.message_str)
        if query is None:
            yield event.plain_result(
                "格式错误，请使用：电费统计 公寓号-宿舍号（例如：电费统计 3-128）"
            )
            return

        try:
            records = await self.get_balance_records(query)
        except Exception:
            logger.exception(
                "Failed to load electricity history for building %s room %s",
                query.building_id,
                query.dormitory_number,
            )
            yield event.plain_result("电费统计读取失败，请稍后重试。")
            return

        if not records:
            yield event.plain_result(
                f"还没有{query.display_building_id}号公寓{query.room_number}宿舍的本地记录，"
                "请先使用“查询电费”。"
            )
            return

        chart_path = self._build_chart_path(query)
        try:
            render_balance_chart(
                records,
                chart_path,
                dormitory_label=f"{query.display_building_id}-{query.room_number}",
            )
        except Exception:
            logger.exception(
                "Failed to render electricity history chart for building %s room %s",
                query.building_id,
                query.dormitory_number,
            )
            yield event.plain_result("电费统计图片生成失败，请稍后重试。")
            return

        first = records[0].timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        last = records[-1].timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        summary = (
            f"{query.display_building_id}号公寓{query.room_number}宿舍"
            f"近30天电费记录：{len(records)}条，范围：{first} 至 {last}"
        )
        yield event.chain_result(
            [
                Comp.Plain(summary),
                Comp.Image.fromFileSystem(str(chart_path)),
            ]
        )

    async def terminate(self) -> None:
        alert_task = self._alert_task
        self._alert_task = None
        if alert_task is not None:
            alert_task.cancel()
            try:
                await alert_task
            except asyncio.CancelledError:
                pass

        async with self._backend_lock:
            backend = self.backend
            self.backend = None
        if backend is not None:
            await backend.aclose()
