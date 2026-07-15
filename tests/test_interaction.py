import unittest
from decimal import Decimal

from xatu_electricity.interaction import (
    format_balance,
    parse_dormitory_query,
    parse_statistics_query,
)


class InteractionTests(unittest.TestCase):
    def test_parse_dormitory_query(self) -> None:
        query = parse_dormitory_query("查询电费 3-128")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.building_id, "3")
        self.assertEqual(query.room_number, "128")
        self.assertEqual(query.dormitory_number, "3128")
        self.assertEqual(query.display_building_id, "3")

    def test_parse_dormitory_query_accepts_optional_slash(self) -> None:
        query = parse_dormitory_query("/查询电费 3-001")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.room_number, "001")

    def test_parse_dormitory_query_rejects_invalid_number(self) -> None:
        self.assertIsNone(parse_dormitory_query("查询电费 128"))
        self.assertIsNone(parse_dormitory_query("查询电费 31A8"))

    def test_parse_dormitory_query_maps_display_building_id(self) -> None:
        query = parse_dormitory_query("查询电费 10-128")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.display_building_id, "10")
        self.assertEqual(query.building_id, "64")
        self.assertEqual(query.dormitory_number, "10128")

    def test_parse_statistics_query(self) -> None:
        query = parse_statistics_query("电费统计 3-128")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.building_id, "3")
        self.assertEqual(query.room_number, "128")
        self.assertIsNone(parse_statistics_query("查询电费 3-128"))

    def test_format_balance(self) -> None:
        self.assertEqual(format_balance(Decimal("209.399902343750")), "209.39990234375")
        self.assertEqual(format_balance(Decimal("100.00")), "100")


if __name__ == "__main__":
    unittest.main()
