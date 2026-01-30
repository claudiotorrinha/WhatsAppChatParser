import unittest
from pathlib import Path
from unittest import mock

from wcp import media


class TestMediaHelpers(unittest.TestCase):
    def test_tmp_path_preserves_extension(self):
        dst = Path("out/converted/PTT-1.wav")
        tmp = media._tmp_path_for(dst)
        self.assertTrue(tmp.name.endswith(".tmp.wav"))

        dst2 = Path("out/converted/PTT-2.mp3")
        tmp2 = media._tmp_path_for(dst2)
        self.assertTrue(tmp2.name.endswith(".tmp.mp3"))

    def test_ffmpeg_uses_tmp_with_extension(self):
        dst = Path("out/converted/PTT-3.wav")
        cmd = ["ffmpeg", "-y", "-i", "in.opus", str(dst)]
        with mock.patch("wcp.media.subprocess.run") as run_mock, \
             mock.patch("wcp.media.os.replace") as replace_mock:
            run_mock.return_value = mock.Mock(returncode=0, stderr="")
            media.ffmpeg_to_tmp_then_replace(cmd, dst)
            called_cmd = run_mock.call_args[0][0]
            self.assertTrue(str(called_cmd[-1]).endswith(".tmp.wav"))
            replace_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
