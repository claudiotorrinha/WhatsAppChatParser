import unittest
from unittest import mock

from wcp.transcribe import resolve_transcribe_runtime


class TestTranscribeRuntime(unittest.TestCase):
    def test_force_cpu_keeps_requested_model(self):
        model, device, decision = resolve_transcribe_runtime(
            "large-v3-turbo",
            force_cpu=True,
            speed_preset="auto",
        )
        self.assertEqual(model, "large-v3-turbo")
        self.assertEqual(device, "cpu")
        self.assertEqual(decision.get("reason"), "force_cpu")

    def test_auto_prefers_medium_on_cuda(self):
        with mock.patch("wcp.transcribe._cuda_hardware_info", return_value={"available": True, "vram_gb": 8.0}):
            model, device, decision = resolve_transcribe_runtime(
                "large-v3-turbo",
                force_cpu=False,
                speed_preset="auto",
            )
        self.assertEqual(model, "medium")
        self.assertEqual(device, "cuda")
        self.assertEqual(decision.get("reason"), "auto_speed_cuda")

    def test_auto_marks_low_vram_reason(self):
        with mock.patch("wcp.transcribe._cuda_hardware_info", return_value={"available": True, "vram_gb": 2.0}):
            model, device, decision = resolve_transcribe_runtime(
                "medium",
                force_cpu=False,
                speed_preset="auto",
            )
        self.assertEqual(model, "medium")
        self.assertEqual(device, "cuda")
        self.assertEqual(decision.get("reason"), "auto_speed_low_vram_gpu")

    def test_auto_falls_back_to_cpu_without_cuda(self):
        with mock.patch("wcp.transcribe._cuda_hardware_info", return_value={"available": False}):
            model, device, decision = resolve_transcribe_runtime(
                "large-v3-turbo",
                force_cpu=False,
                speed_preset="auto",
            )
        self.assertEqual(model, "medium")
        self.assertEqual(device, "cpu")
        self.assertEqual(decision.get("reason"), "auto_speed_cpu_fallback")

    def test_speed_preset_off_keeps_requested_model(self):
        model, device, decision = resolve_transcribe_runtime(
            "large-v3-turbo",
            force_cpu=False,
            speed_preset="off",
        )
        self.assertEqual(model, "large-v3-turbo")
        self.assertIsNone(device)
        self.assertEqual(decision.get("reason"), "explicit_settings")


if __name__ == "__main__":
    unittest.main()
