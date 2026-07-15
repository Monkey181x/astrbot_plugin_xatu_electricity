from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal


def _load_module(name: str, relative_path: str):
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ALERTS = _load_module(
    "_xatu_active_alerts_test",
    "xatu_electricity/alerts.py",
)
INTERACTION = _load_module(
    "_xatu_active_interaction_test",
    "xatu_electricity/interaction.py",
)


class AlertThresholdTests(unittest.TestCase):
    def test_crosses_each_threshold_without_repeating_same_band(self) -> None:
        self.assertEqual(
            ALERTS.crossed_threshold(Decimal("31"), Decimal("30")),
            Decimal("30"),
        )
        self.assertIsNone(ALERTS.crossed_threshold(Decimal("30"), Decimal("30")))
        self.assertIsNone(ALERTS.crossed_threshold(Decimal("30"), Decimal("29")))
        self.assertEqual(
            ALERTS.crossed_threshold(Decimal("30"), Decimal("15")),
            Decimal("15"),
        )

    def test_skipped_bands_produce_one_lowest_reached_alert(self) -> None:
        self.assertEqual(
            ALERTS.crossed_threshold(Decimal("40"), Decimal("4")),
            Decimal("5"),
        )
        self.assertEqual(
            ALERTS.crossed_threshold(Decimal("40"), Decimal("-1")),
            Decimal("0"),
        )

    def test_first_check_alerts_current_band_and_recharge_rearms(self) -> None:
        self.assertEqual(
            ALERTS.crossed_threshold(None, Decimal("20")),
            Decimal("30"),
        )
        self.assertIsNone(ALERTS.crossed_threshold(Decimal("20"), Decimal("50")))
        self.assertEqual(
            ALERTS.crossed_threshold(Decimal("50"), Decimal("29")),
            Decimal("30"),
        )


class AlertScheduleTests(unittest.TestCase):
    def test_default_vacation_windows_are_inclusive(self) -> None:
        windows = (
            ALERTS.DEFAULT_WINTER_VACATION,
            ALERTS.DEFAULT_SUMMER_VACATION,
        )
        self.assertTrue(ALERTS.is_vacation_date(date(2026, 2, 1), windows))
        self.assertTrue(ALERTS.is_vacation_date(date(2026, 3, 1), windows))
        self.assertFalse(ALERTS.is_vacation_date(date(2026, 3, 2), windows))
        self.assertTrue(ALERTS.is_vacation_date(date(2026, 7, 10), windows))
        self.assertTrue(ALERTS.is_vacation_date(date(2026, 8, 31), windows))
        self.assertFalse(ALERTS.is_vacation_date(date(2026, 9, 1), windows))

    def test_next_run_is_today_before_one_and_tomorrow_after_one(self) -> None:
        before = ALERTS.next_daily_run(datetime(2026, 6, 18, 12, 59))
        after = ALERTS.next_daily_run(datetime(2026, 6, 18, 13, 0))
        self.assertEqual(before.isoformat(), "2026-06-18T13:00:00+08:00")
        self.assertEqual(after.isoformat(), "2026-06-19T13:00:00+08:00")


class AlertStorageAndParsingTests(unittest.TestCase):
    def test_subscription_round_trip_preserves_last_balance(self) -> None:
        subscription = ALERTS.AlertSubscription(
            platform_id="default",
            user_id="123",
            private_umo="default:FriendMessage:123",
            fallback_group_umo="default:GroupMessage:456",
            display_building_id="3",
            building_id="3",
            room_number="128",
            dormitory_number="3128",
            last_balance=Decimal("30.00"),
        )
        restored = ALERTS.load_alerts(
            ALERTS.dump_alerts({subscription.key: subscription})
        )
        self.assertEqual(restored[subscription.key], subscription)

    def test_alert_commands_use_building_mapping(self) -> None:
        setting = INTERACTION.parse_set_alert_query("设置预警 10-128")
        cancelling = INTERACTION.parse_cancel_alert_query("/取消预警 10-128")
        self.assertIsNotNone(setting)
        self.assertIsNotNone(cancelling)
        assert setting is not None and cancelling is not None
        self.assertEqual(setting.building_id, "64")
        self.assertEqual(setting.dormitory_number, "10128")
        self.assertEqual(cancelling, setting)


if __name__ == "__main__":
    unittest.main()
