import array
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp.transcribe import Transcriber


class TestTranscribeChunking(unittest.TestCase):
    def _write_wav(self, path: Path, *, sample_rate: int, frames: int) -> None:
        pcm = array.array("h", [0] * frames).tobytes()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)

    def test_iter_wav_chunks_streams_expected_sizes(self):
        with TemporaryDirectory() as td:
            wav_path = Path(td) / "sample.wav"
            self._write_wav(wav_path, sample_rate=16000, frames=40000)  # 2.5s
            t = Transcriber.__new__(Transcriber)
            chunks = list(t._iter_wav_mono_16k_chunks(wav_path, sample_rate=16000, chunk_seconds=1))
            self.assertEqual(len(chunks), 3)
            self.assertEqual(len(chunks[0]), 16000)
            self.assertEqual(len(chunks[1]), 16000)
            self.assertEqual(len(chunks[2]), 8000)

    def test_iter_wav_chunks_rejects_wrong_sample_rate(self):
        with TemporaryDirectory() as td:
            wav_path = Path(td) / "sample.wav"
            self._write_wav(wav_path, sample_rate=8000, frames=8000)
            t = Transcriber.__new__(Transcriber)
            with self.assertRaises(RuntimeError):
                list(t._iter_wav_mono_16k_chunks(wav_path, sample_rate=16000, chunk_seconds=1))


if __name__ == "__main__":
    unittest.main()
