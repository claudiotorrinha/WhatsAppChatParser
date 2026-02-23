import unittest
from unittest import mock

from wcp import ui_app


class TestUiApp(unittest.TestCase):
    def test_resolve_out_dir(self):
        out = ui_app._resolve_out_dir("out")
        self.assertTrue(str(out).endswith("out"))

    def test_parse_helpers(self):
        self.assertTrue(ui_app._parse_bool("true"))
        self.assertFalse(ui_app._parse_bool("false"))

    def test_runtime_info_keys(self):
        info = ui_app._runtime_info()
        self.assertIn("cuda_available", info)
        self.assertIn("torch_version", info)
        self.assertIn("transformers_available", info)
        self.assertIn("supported_transcription_backend", info)
        self.assertIn("supported_transcription_models", info)
        self.assertIn("supported_speed_presets", info)

    def test_audio_test_requirements_ok(self):
        info = {
            "transformers_available": True,
            "torch_available": True,
            "ffmpeg_available": True,
            "install_hints": {},
        }
        self.assertEqual(ui_app._check_audio_test_requirements(info), [])

    def test_audio_test_requirements_missing(self):
        info = {
            "transformers_available": False,
            "torch_available": False,
            "ffmpeg_available": False,
            "install_hints": {"ffmpeg": "install ffmpeg"},
        }
        errors = ui_app._check_audio_test_requirements(info)
        self.assertTrue(any("transformers" in e for e in errors))
        self.assertTrue(any("torch" in e for e in errors))
        self.assertTrue(any("ffmpeg" in e for e in errors))

    @mock.patch("wcp.ui_app._persist_loaded_state")
    @mock.patch("wcp.ui_app._is_pid_running", return_value=False)
    def test_reconcile_loaded_state_marks_dead_running_as_error(self, _pid_mock, persist_mock):
        state = {
            "job_id": "abc",
            "status": "running",
            "pid": 1234,
            "exit_code": None,
            "finished_at": None,
        }
        fixed = ui_app._reconcile_loaded_state("abc", state)
        self.assertEqual(fixed["status"], "error")
        self.assertEqual(fixed["exit_code"], 1)
        self.assertTrue(isinstance(fixed.get("finished_at"), str))
        persist_mock.assert_called_once()

    @mock.patch("wcp.ui_app._persist_loaded_state")
    @mock.patch("wcp.ui_app._is_pid_running", return_value=True)
    def test_reconcile_loaded_state_keeps_alive_running(self, _pid_mock, persist_mock):
        state = {
            "job_id": "abc",
            "status": "running",
            "pid": 1234,
            "exit_code": None,
            "finished_at": None,
        }
        fixed = ui_app._reconcile_loaded_state("abc", state)
        self.assertEqual(fixed["status"], "running")
        persist_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
