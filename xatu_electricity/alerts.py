from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ALERT_THRESHOLDS = (
    Decimal("30"),
    Decimal("15"),
    Decimal("5"),
    Decimal("0"),
)
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_ALERT_HOUR = 13
DEFAULT_WINTER_VACATION = ((2, 1), (3, 1))
DEFAULT_SUMMER_VACATION = ((7, 10), (8, 31))


@dataclass(frozen=True, slots=True)
class AlertSubscription:
    platform_id: str
    user_id: str
    private_umo: str
    fallback_group_umo: str | None
    display_building_id: str
    building_id: str
    room_number: str
    dormitory_number: str
    last_balance: Decimal | None = None

    @property
    def key(self) -> str:
        return alert_key(
            self.platform_id,
            self.user_id,
            self.display_building_id,
            self.room_number,
        )

    def with_balance(self, balance: Decimal) -> "AlertSubscription":
        return replace(self, last_balance=balance)

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "AlertSubscription":
        raw_last_balance = value.get("last_balance")
        last_balance = (
            None if raw_last_balance in (None, "") else Decimal(str(raw_last_balance))
        )
        fallback_group_umo = value.get("fallback_group_umo")
        return cls(
            platform_id=str(value["platform_id"]),
            user_id=str(value["user_id"]),
            private_umo=str(value["private_umo"]),
            fallback_group_umo=(
                str(fallback_group_umo) if fallback_group_umo else None
            ),
            display_building_id=str(value["display_building_id"]),
            building_id=str(value["building_id"]),
            room_number=str(value["room_number"]),
            dormitory_number=str(value["dormitory_number"]),
            last_balance=last_balance,
        )

    def to_json(self) -> dict[str, str | None]:
        return {
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "private_umo": self.private_umo,
            "fallback_group_umo": self.fallback_group_umo,
            "display_building_id": self.display_building_id,
            "building_id": self.building_id,
            "room_number": self.room_number,
            "dormitory_number": self.dormitory_number,
            "last_balance": (
                format(self.last_balance, "f")
                if self.last_balance is not None
                else None
            ),
        }


def alert_key(
    platform_id: str,
    user_id: str,
    display_building_id: str,
    room_number: str,
) -> str:
    return f"{platform_id}:{user_id}:{display_building_id}-{room_number}"


def load_alerts(value: Any) -> dict[str, AlertSubscription]:
    if not isinstance(value, dict):
        return {}

    alerts: dict[str, AlertSubscription] = {}
    for raw_subscription in value.values():
        if not isinstance(raw_subscription, dict):
            continue
        try:
            subscription = AlertSubscription.from_json(raw_subscription)
        except (KeyError, ValueError, InvalidOperation):
            continue
        alerts[subscription.key] = subscription
    return alerts


def dump_alerts(
    alerts: dict[str, AlertSubscription],
) -> dict[str, dict[str, str | None]]:
    return {key: subscription.to_json() for key, subscription in sorted(alerts.items())}


def crossed_threshold(
    previous_balance: Decimal | None,
    current_balance: Decimal,
    *,
    thresholds: Iterable[Decimal] = ALERT_THRESHOLDS,
) -> Decimal | None:
    crossed = [
        threshold
        for threshold in thresholds
        if current_balance <= threshold
        and (previous_balance is None or previous_balance > threshold)
    ]
    return min(crossed) if crossed else None


def parse_month_day(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    try:
        month_text, day_text = str(value).strip().split("-", 1)
        parsed = (int(month_text), int(day_text))
        date(2000, *parsed)
    except (TypeError, ValueError):
        return default
    return parsed


def is_date_in_window(
    current_date: date,
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    current_value = (current_date.month, current_date.day)
    if start <= end:
        return start <= current_value <= end
    return current_value >= start or current_value <= end


def is_vacation_date(
    current_date: date,
    windows: Iterable[tuple[tuple[int, int], tuple[int, int]]],
) -> bool:
    return any(is_date_in_window(current_date, start, end) for start, end in windows)


def next_daily_run(
    now: datetime,
    hour: int = DEFAULT_ALERT_HOUR,
) -> datetime:
    if not 0 <= hour <= 23:
        hour = DEFAULT_ALERT_HOUR
    local_now = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    candidate = datetime.combine(
        local_now.date(),
        time(hour=hour, tzinfo=SHANGHAI_TIMEZONE),
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate
