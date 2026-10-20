import unittest

from rangering import format_range


class FormatRangeInMeters(unittest.TestCase):
    def test_whole_meters(self):
        self.assertEqual(format_range(500, "m"), "500 m")

    def test_rounds_to_the_nearest_meter(self):
        self.assertEqual(format_range(1234.6, "m"), "1235 m")

    def test_zero(self):
        self.assertEqual(format_range(0, "m"), "0 m")


if __name__ == "__main__":
    unittest.main()
