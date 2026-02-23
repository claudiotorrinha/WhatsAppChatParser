import unittest

from wcp.main import _effective_progress


class TestMainProgress(unittest.TestCase):
    def test_pending_transcribe_is_counted_within_same_total(self):
        done, pct = _effective_progress(1204, 1204, 702)
        self.assertEqual(done, 502)
        self.assertAlmostEqual(pct, (502 / 1204) * 100.0, places=4)

    def test_combines_media_done_and_transcribe_remaining(self):
        done, pct = _effective_progress(1204, 1000, 200)
        self.assertEqual(done, 800)
        self.assertAlmostEqual(pct, (800 / 1204) * 100.0, places=4)

    def test_zero_total_is_complete(self):
        done, pct = _effective_progress(0, 0, 0)
        self.assertEqual(done, 0)
        self.assertEqual(pct, 100.0)

    def test_clamps_invalid_inputs(self):
        done, pct = _effective_progress(10, -5, 100)
        self.assertEqual(done, 0)
        self.assertEqual(pct, 0.0)


if __name__ == "__main__":
    unittest.main()
