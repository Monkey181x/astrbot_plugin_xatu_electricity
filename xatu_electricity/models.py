from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Room:
    room_id: str
    room_name: str

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "Room":
        return cls(
            room_id=str(value["roomid"]),
            room_name=str(value["roomname"]).strip(),
        )


@dataclass(frozen=True, slots=True)
class ElectricityBalance:
    building_id: str
    room_id: str
    room_name: str
    oddl: Decimal
    suml: Decimal
    balance: Decimal
    fetched_at: datetime

    @classmethod
    def from_api(
        cls,
        *,
        building_id: str,
        room: Room,
        value: dict[str, Any],
    ) -> "ElectricityBalance":
        oddl = Decimal(str(value["oddl"]))
        suml = Decimal(str(value["suml"]))
        return cls(
            building_id=building_id,
            room_id=room.room_id,
            room_name=room.room_name,
            oddl=oddl,
            suml=suml,
            balance=suml - oddl,
            fetched_at=datetime.now(timezone.utc),
        )
