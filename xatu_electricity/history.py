from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

HISTORY_RETENTION_DAYS = 30
MIN_RECORD_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class BalanceHistoryRecord:
    timestamp: datetime
    balance: Decimal
    display_building_id: str
    building_id: str
    room_number: str
    dormitory_number: str
    room_id: str
    room_name: str

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "BalanceHistoryRecord":
        timestamp = _parse_timestamp(str(value["timestamp"]))
        return cls(
            timestamp=timestamp,
            balance=Decimal(str(value["balance"])),
            display_building_id=str(value.get("display_building_id", "")),
            building_id=str(value.get("building_id", "")),
            room_number=str(value.get("room_number", "")),
            dormitory_number=str(value.get("dormitory_number", "")),
            room_id=str(value.get("room_id", "")),
            room_name=str(value.get("room_name", "")),
        )

    def to_json(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "balance": format(self.balance, "f"),
            "display_building_id": self.display_building_id,
            "building_id": self.building_id,
            "room_number": self.room_number,
            "dormitory_number": self.dormitory_number,
            "room_id": self.room_id,
            "room_name": self.room_name,
        }


def history_key(display_building_id: str, room_number: str) -> str:
    return f"{display_building_id}-{room_number}"


def load_history(value: Any) -> dict[str, list[BalanceHistoryRecord]]:
    if not isinstance(value, dict):
        return {}

    history: dict[str, list[BalanceHistoryRecord]] = {}
    for key, raw_records in value.items():
        if not isinstance(key, str) or not isinstance(raw_records, list):
            continue
        records: list[BalanceHistoryRecord] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                continue
            try:
                records.append(BalanceHistoryRecord.from_json(raw_record))
            except (KeyError, ValueError, InvalidOperation):
                continue
        if records:
            history[key] = sorted(records, key=lambda record: record.timestamp)
    return history


def dump_history(
    history: dict[str, list[BalanceHistoryRecord]],
) -> dict[str, list[dict[str, str]]]:
    return {
        key: [record.to_json() for record in records]
        for key, records in sorted(history.items())
    }


def prune_history(
    history: dict[str, list[BalanceHistoryRecord]],
    *,
    now: datetime | None = None,
) -> dict[str, list[BalanceHistoryRecord]]:
    current_time = _ensure_utc(now or datetime.now(timezone.utc))
    cutoff = current_time - timedelta(days=HISTORY_RETENTION_DAYS)
    pruned: dict[str, list[BalanceHistoryRecord]] = {}
    for key, records in history.items():
        kept = [record for record in records if record.timestamp >= cutoff]
        if kept:
            pruned[key] = sorted(kept, key=lambda record: record.timestamp)
    return pruned


def make_balance_record(
    *,
    display_building_id: str,
    building_id: str,
    room_number: str,
    dormitory_number: str,
    room_id: str,
    room_name: str,
    balance: Decimal,
    timestamp: datetime,
) -> BalanceHistoryRecord:
    return BalanceHistoryRecord(
        timestamp=_ensure_utc(timestamp),
        balance=balance,
        display_building_id=display_building_id,
        building_id=building_id,
        room_number=room_number,
        dormitory_number=dormitory_number,
        room_id=room_id,
        room_name=room_name,
    )


def should_append_record(
    records: list[BalanceHistoryRecord],
    timestamp: datetime,
) -> bool:
    if not records:
        return True
    latest = max(records, key=lambda record: record.timestamp)
    return _ensure_utc(timestamp) - latest.timestamp >= MIN_RECORD_INTERVAL


def render_balance_chart(
    records: list[BalanceHistoryRecord],
    output_path: Path,
    *,
    dormitory_label: str,
) -> None:
    if not records:
        raise ValueError("records must not be empty")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda record: record.timestamp)
    width, height = 900, 520
    pixels = bytearray([255] * width * height * 3)

    def put_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            index = (y * width + x) * 3
            pixels[index : index + 3] = bytes(color)

    def draw_line(
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        *,
        thickness: int = 1,
    ) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            for ox in range(-(thickness // 2), thickness // 2 + 1):
                for oy in range(-(thickness // 2), thickness // 2 + 1):
                    put_pixel(x0 + ox, y0 + oy, color)
            if x0 == x1 and y0 == y1:
                break
            twice_error = 2 * error
            if twice_error >= dy:
                error += dy
                x0 += sx
            if twice_error <= dx:
                error += dx
                y0 += sy

    def draw_rect(
        x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]
    ) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                put_pixel(x, y, color)

    def draw_text(
        x: int,
        y: int,
        text: str,
        color: tuple[int, int, int],
        *,
        scale: int = 3,
    ) -> None:
        cursor = x
        for character in text.upper():
            pattern = _FONT.get(character)
            if pattern is None:
                cursor += 4 * scale
                continue
            for row_index, row in enumerate(pattern):
                for col_index, enabled in enumerate(row):
                    if enabled == "1":
                        draw_rect(
                            cursor + col_index * scale,
                            y + row_index * scale,
                            cursor + (col_index + 1) * scale - 1,
                            y + (row_index + 1) * scale - 1,
                            color,
                        )
            cursor += (len(pattern[0]) + 1) * scale

    plot_left, plot_right = 82, width - 32
    plot_top, plot_bottom = 54, height - 74
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    balances = [float(record.balance) for record in records]
    minimum = min(balances)
    maximum = max(balances)
    if math.isclose(minimum, maximum):
        padding = max(abs(minimum) * 0.05, 1.0)
    else:
        padding = (maximum - minimum) * 0.12
    minimum -= padding
    maximum += padding

    # 将横轴映射到真实查询时间：两条记录相隔越久，图上的间距越大。
    # 正常记录会按小时去重；若导入了时间戳完全相同的旧数据，则退回等距，
    # 以避免除零且仍能清晰展示全部记录点。
    first_timestamp = records[0].timestamp.timestamp()
    last_timestamp = records[-1].timestamp.timestamp()
    time_span = last_timestamp - first_timestamp

    def x_for(index: int) -> int:
        if len(records) == 1:
            return plot_left + plot_width // 2
        if time_span <= 0:
            return int(plot_left + index * plot_width / (len(records) - 1))
        timestamp = records[index].timestamp.timestamp()
        ratio = (timestamp - first_timestamp) / time_span
        return int(plot_left + ratio * plot_width)

    def y_for(value: float) -> int:
        ratio = (maximum - value) / (maximum - minimum)
        return int(plot_top + ratio * plot_height)

    grid_color = (224, 229, 236)
    axis_color = (80, 89, 104)
    line_color = (37, 99, 235)
    point_color = (220, 38, 38)
    text_color = (31, 41, 55)

    draw_text(82, 20, f"BALANCE {dormitory_label}", text_color, scale=3)

    ticks = 5
    for tick in range(ticks + 1):
        value = minimum + (maximum - minimum) * tick / ticks
        y = y_for(value)
        draw_line(plot_left, y, plot_right, y, grid_color)
        label = _format_tick(value)
        draw_text(8, y - 7, label, text_color, scale=2)

    draw_line(plot_left, plot_top, plot_left, plot_bottom, axis_color, thickness=2)
    draw_line(plot_left, plot_bottom, plot_right, plot_bottom, axis_color, thickness=2)

    selected_indexes = {0, len(records) - 1}
    if len(records) > 2:
        selected_indexes.add(len(records) // 2)
    for index in sorted(selected_indexes):
        x = x_for(index)
        draw_line(x, plot_bottom, x, plot_bottom + 7, axis_color)
        label = records[index].timestamp.astimezone(timezone.utc).strftime("%m-%d")
        draw_text(x - 24, plot_bottom + 18, label, text_color, scale=2)

    points = [(x_for(index), y_for(value)) for index, value in enumerate(balances)]
    for first, second in zip(points, points[1:]):
        draw_line(*first, *second, line_color, thickness=3)

    for x, y in points:
        draw_rect(x - 4, y - 4, x + 4, y + 4, point_color)

    _write_png(output_path, width, height, pixels)


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _ensure_utc(timestamp)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_tick(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    rows = []
    stride = width * 3
    for y in range(height):
        start = y * stride
        rows.append(b"\x00" + bytes(pixels[start : start + stride]))

    data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"".join(rows), level=9)),
            chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(data)


_FONT = {
    " ": ["000", "000", "000", "000", "000"],
    "-": ["000", "000", "111", "000", "000"],
    ".": ["0", "0", "0", "0", "1"],
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
    "A": ["111", "101", "111", "101", "101"],
    "B": ["110", "101", "110", "101", "110"],
    "C": ["111", "100", "100", "100", "111"],
    "E": ["111", "100", "110", "100", "111"],
    "L": ["100", "100", "100", "100", "111"],
    "N": ["101", "111", "111", "111", "101"],
}
