import unittest

from wcp.benchmark import _pick_evenly


class TestBenchmark(unittest.TestCase):
    def test_pick_evenly_empty(self):
        self.assertEqual(_pick_evenly([], 3), [])

    def test_pick_evenly_count(self):
        items = [str(i) for i in range(10)]
        picked = _pick_evenly(items, 4)
        self.assertEqual(len(picked), 4)
        self.assertEqual(picked[0], "0")
        self.assertEqual(picked[-1], "7")

    def test_pick_evenly_all(self):
        items = ["a", "b"]
        self.assertEqual(_pick_evenly(items, 5), items)


if __name__ == "__main__":
    unittest.main()
