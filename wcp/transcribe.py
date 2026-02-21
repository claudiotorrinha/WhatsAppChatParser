from __future__ import annotations

from pathlib import Path
from typing import Optional


SUPPORTED_MODELS = {
    "medium": "openai/whisper-medium",
    "large-v3-turbo": "openai/whisper-large-v3-turbo",
}


class Transcriber:
    """Whisper transcription wrapper using HF Transformers."""

    def __init__(self, model: str = "medium", device: Optional[str] = None):
        if model not in SUPPORTED_MODELS:
            supported = ", ".join(sorted(SUPPORTED_MODELS))
            raise ValueError(f"Unsupported transcription model '{model}'. Supported models: {supported}.")
        if device not in (None, "cpu", "cuda"):
            raise ValueError("device must be None, 'cpu', or 'cuda'.")
        self.model = model
        self.device_preference = device
        self.backend = None
        self.init_error: Optional[str] = None
        self._init_backend()

    def _read_wav_mono_16k(self, wav_path: Path) -> list[float]:
        # Media conversion uses: -ac 1 -ar 16000, so this should be 16k mono PCM.
        import wave

        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            nframes = wf.getnframes()
            frames = wf.readframes(nframes)

        if channels != 1:
            raise RuntimeError(f"Expected mono WAV, got {channels} channels: {wav_path}")
        if rate != 16000:
            raise RuntimeError(f"Expected 16kHz WAV, got {rate}Hz: {wav_path}")
        if sampwidth != 2:
            raise RuntimeError(f"Expected 16-bit PCM WAV, got {sampwidth * 8}-bit: {wav_path}")

        # Convert little-endian int16 PCM to float32 in [-1, 1].
        import array

        pcm = array.array("h")
        pcm.frombytes(frames)
        scale = 1.0 / 32768.0
        return [float(x) * scale for x in pcm]

    def _iter_audio_chunks(
        self, audio: list[float], *, sample_rate: int = 16000, chunk_seconds: int = 30
    ):
        """Yield fixed-size audio chunks to avoid Whisper truncation on long files."""
        chunk_size = sample_rate * chunk_seconds
        if chunk_size <= 0:
            yield audio
            return
        for i in range(0, len(audio), chunk_size):
            yield audio[i : i + chunk_size]

    def _init_backend(self):
        attempted_errors: list[str] = []
        try:
            import torch  # type: ignore
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor  # type: ignore

            if self.device_preference == "cuda":
                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA requested but not available.")
                devices = ["cuda"]
            elif self.device_preference == "cpu":
                devices = ["cpu"]
            else:
                devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]

            for device in devices:
                try:
                    dtype = torch.float16 if device == "cuda" else torch.float32
                    repo_id = SUPPORTED_MODELS[self.model]
                    processor = AutoProcessor.from_pretrained(repo_id)
                    # Newer Transformers prefers `dtype`; keep a fallback for older releases.
                    try:
                        model = AutoModelForSpeechSeq2Seq.from_pretrained(repo_id, dtype=dtype)
                    except TypeError:
                        model = AutoModelForSpeechSeq2Seq.from_pretrained(repo_id, torch_dtype=dtype)
                    model.to(device)
                    model.eval()
                    self.backend = (processor, model, device)
                    if attempted_errors:
                        self.init_error = f"Recovered after fallback. Earlier errors: {' | '.join(attempted_errors)}"
                    else:
                        self.init_error = None
                    return
                except Exception as e:
                    attempted_errors.append(f"{device}: {e}")
        except Exception as e:
            attempted_errors.append(str(e))

        if attempted_errors:
            self.init_error = " ; ".join(attempted_errors)
        else:
            self.init_error = "Unknown initialization error."
        self.backend = None

    def backend_error(self) -> Optional[str]:
        return self.init_error

    def _unavailable_message(self) -> str:
        msg = "HF Transformers backend is not installed or failed to initialize."
        if self.init_error:
            return f"{msg} Details: {self.init_error}"
        return msg

    def available(self) -> bool:
        return self.backend is not None

    def transcribe_wav(self, wav_path: Path, language: Optional[str] = "pt") -> str:
        if not self.backend:
            raise RuntimeError(self._unavailable_message())

        processor, asr_model, device = self.backend
        try:
            import torch  # type: ignore

            audio = self._read_wav_mono_16k(wav_path)
            model_dtype = getattr(asr_model, "dtype", None)
            if model_dtype is None:
                try:
                    model_dtype = next(asr_model.parameters()).dtype
                except Exception:
                    model_dtype = None

            gen_kwargs = {}
            if language and hasattr(processor, "get_decoder_prompt_ids"):
                try:
                    gen_kwargs["forced_decoder_ids"] = processor.get_decoder_prompt_ids(language=language, task="transcribe")
                except Exception:
                    pass

            chunk_texts: list[str] = []
            with torch.inference_mode():
                for chunk in self._iter_audio_chunks(audio, sample_rate=16000, chunk_seconds=30):
                    if not chunk:
                        continue
                    inputs = processor(chunk, sampling_rate=16000, return_tensors="pt")
                    inputs = {k: v.to(device) for k, v in inputs.items()}
                    if model_dtype is not None and "input_features" in inputs:
                        inputs["input_features"] = inputs["input_features"].to(dtype=model_dtype)
                    predicted_ids = asr_model.generate(**inputs, **gen_kwargs)
                    chunk_text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
                    if chunk_text:
                        chunk_texts.append(chunk_text)
            return " ".join(chunk_texts).strip()
        except Exception as e:
            raise RuntimeError(f"HF transcription failed: {e}") from e
