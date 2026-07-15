from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_BUILDING_ID_MAP = {
    "7": "43",
    "8": "44",
    "9": "57",
    "10": "64",
    "11": "71",
    "12": "77",
    "13": "83",
    "14": "96",
    "15": "110",
}

_QUERY_PATTERN = re.compile(r"^/?查询电费\s*(\d+)\s*-\s*(\d+)$")
_STATISTICS_PATTERN = re.compile(r"^/?电费统计\s*(\d+)\s*-\s*(\d+)$")
_SET_ALERT_PATTERN = re.compile(r"^/?设置预警\s*(\d+)\s*-\s*(\d+)$")
_CANCEL_ALERT_PATTERN = re.compile(r"^/?取消预警\s*(\d+)\s*-\s*(\d+)$")


@dataclass(frozen=True, slots=True)
class DormitoryQuery:
    dormitory_number: str
    building_id: str
    room_number: str
    display_building_id: str


def parse_dormitory_query(message: str) -> DormitoryQuery | None:
    return _parse_query(message, _QUERY_PATTERN)


def parse_statistics_query(message: str) -> DormitoryQuery | None:
    return _parse_query(message, _STATISTICS_PATTERN)


def parse_set_alert_query(message: str) -> DormitoryQuery | None:
    return _parse_query(message, _SET_ALERT_PATTERN)


def parse_cancel_alert_query(message: str) -> DormitoryQuery | None:
    return _parse_query(message, _CANCEL_ALERT_PATTERN)


def _parse_query(message: str, pattern: re.Pattern[str]) -> DormitoryQuery | None:
    match = pattern.fullmatch(message.strip())
    if match is None:
        return None

    display_building_id = match.group(1)
    room_number = match.group(2)

    building_id = _BUILDING_ID_MAP.get(display_building_id, display_building_id)

    return DormitoryQuery(
        dormitory_number=f"{display_building_id}{room_number}",
        building_id=building_id,
        room_number=room_number,
        display_building_id=display_building_id,
    )


def format_balance(balance: Decimal) -> str:
    text = format(balance, "f")
    if "." not in text:
        return text

    whole, fraction = text.split(".", 1)
    fraction = fraction.rstrip("0")
    return whole if not fraction else f"{whole}.{fraction}"
