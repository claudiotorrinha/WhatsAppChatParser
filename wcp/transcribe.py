from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Optional


SUPPORTED_MODELS = {
    "medium": "openai/whisper-medium",
    "large-v3-turbo": "openai/whisper-large-v3-turbo",
}
LOW_VRAM_GB_THRESHOLD = 4.0
STREAMING_AUDIO_THRESHOLD_SECONDS = 300


def _cuda_hardware_info() -> dict[str, Any]:
    try:
        import torch  # type: ignore
    except Exception:
        return {"available": False}

    try:
        if not torch.cuda.is_available():
            return {"available": False}
        props = torch.cuda.get_device_properties(0)
        vram_gb = float(props.total_memory) / float(1024 ** 3)
        return {
            "available": True,
            "name": str(props.name),
            "vram_gb": vram_gb,
        }
    except Exception:
        return {"available": True}


def resolve_transcribe_runtime(
    requested_model: str,
    *,
    force_cpu: bool,
    speed_preset: str = "auto",
) -> tuple[str, Optional[str], dict[str, Any]]:
    decision: dict[str, Any] = {
        "requested_model": requested_model,
        "speed_preset": speed_preset,
        "force_cpu": force_cpu,
        "effective_model": requested_model,
        "device": ("cpu" if force_cpu else None),
        "preset_applied": False,
        "reason": "explicit_settings",
    }

    if force_cpu:
        decision["reason"] = "force_cpu"
        return requested_model, "cpu", decision

    if speed_preset != "auto":
        return requested_model, None, decision

    hw = _cuda_hardware_info()
    decision["gpu"] = hw

    # Speed preset: bias to medium and prefer CUDA when available.
    effective_model = "medium"
    if hw.get("available"):
        decision["effective_model"] = effective_model
        decision["device"] = "cuda"
        decision["preset_applied"] = True
        vram_gb = hw.get("vram_gb")
        if isinstance(vram_gb, (int, float)) and float(vram_gb) < LOW_VRAM_GB_THRESHOLD:
            decision["reason"] = "auto_speed_low_vram_gpu"
        else:
            decision["reason"] = "auto_speed_cuda"
        return effective_model, "cuda", decision

    decision["effective_model"] = effective_model
    decision["device"] = "cpu"
    decision["preset_applied"] = True
    decision["reason"] = "auto_speed_cpu_fallback"
    return effective_model, "cpu", decision


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

    def _wav_info(self, wav_path: Path, *, sample_rate: int = 16000) -> tuple[int, int]:
        import wave

        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            nframes = wf.getnframes()

        if channels != 1:
            raise RuntimeError(f"Expected mono WAV, got {channels} channels: {wav_path}")
        if rate != sample_rate:
            raise RuntimeError(f"Expected {sample_rate}Hz WAV, got {rate}Hz: {wav_path}")
        if sampwidth != 2:
            raise RuntimeError(f"Expected 16-bit PCM WAV, got {sampwidth * 8}-bit: {wav_path}")
        return rate, nframes

    def _read_wav_mono_16k(self, wav_path: Path, *, sample_rate: int = 16000):
        import wave

        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            nframes = wf.getnframes()
            frames = wf.readframes(nframes)

        if channels != 1:
            raise RuntimeError(f"Expected mono WAV, got {channels} channels: {wav_path}")
        if rate != sample_rate:
            raise RuntimeError(f"Expected {sample_rate}Hz WAV, got {rate}Hz: {wav_path}")
        if sampwidth != 2:
            raise RuntimeError(f"Expected 16-bit PCM WAV, got {sampwidth * 8}-bit: {wav_path}")

        scale = 1.0 / 32768.0
        try:
            import numpy as np  # type: ignore

            pcm_np = np.frombuffer(frames, dtype="<i2")
            return pcm_np.astype(np.float32) * scale
        except Exception:
            import array

            pcm = array.array("h")
            pcm.frombytes(frames)
            return [float(x) * scale for x in pcm]

    def _iter_audio_chunks(
        self, audio, *, sample_rate: int = 16000, chunk_seconds: int = 30
    ):
        chunk_size = sample_rate * chunk_seconds
        if chunk_size <= 0:
            yield audio
            return
        for i in range(0, len(audio), chunk_size):
            yield audio[i : i + chunk_size]

    def _iter_wav_mono_16k_chunks(
        self, wav_path: Path, *, sample_rate: int = 16000, chunk_seconds: int = 30
    ):
        """Stream fixed-size float chunks from WAV to avoid whole-file memory spikes."""
        import wave

        try:
            import numpy as np  # type: ignore
        except Exception:
            np = None

        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            if channels != 1:
                raise RuntimeError(f"Expected mono WAV, got {channels} channels: {wav_path}")
            if rate != sample_rate:
                raise RuntimeError(f"Expected {sample_rate}Hz WAV, got {rate}Hz: {wav_path}")
            if sampwidth != 2:
                raise RuntimeError(f"Expected 16-bit PCM WAV, got {sampwidth * 8}-bit: {wav_path}")

            frames_per_chunk = sample_rate * chunk_seconds if chunk_seconds > 0 else max(1, wf.getnframes())
            scale = 1.0 / 32768.0
            while True:
                frames = wf.readframes(frames_per_chunk)
                if not frames:
                    break
                if np is not None:
                    pcm_np = np.frombuffer(frames, dtype="<i2")
                    if pcm_np.size == 0:
                        continue
                    yield (pcm_np.astype(np.float32) * scale)
                else:
                    import array

                    pcm = array.array("h")
                    pcm.frombytes(frames)
                    if not pcm:
                        continue
                    yield [float(x) * scale for x in pcm]

    def _init_backend(self):
        attempted_errors: list[str] = []
        try:
            import torch  # type: ignore
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor  # type: ignore
            from transformers.utils import logging as hf_logging  # type: ignore

            # Keep UI/CLI logs focused on actionable errors.
            hf_logging.set_verbosity_error()

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

    def transcribe_wav(
        self,
        wav_path: Path,
        language: Optional[str] = "pt",
        *,
        quality_retry: bool = False,
    ) -> str:
        if not self.backend:
            raise RuntimeError(self._unavailable_message())

        processor, asr_model, device = self.backend
        try:
            import torch  # type: ignore

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
            if quality_retry:
                # Retry profile: discourage repetitive loops.
                gen_kwargs["no_repeat_ngram_size"] = 3
                gen_kwargs["repetition_penalty"] = 1.05

            chunk_texts: list[str] = []
            with torch.inference_mode():
                rate, nframes = self._wav_info(wav_path, sample_rate=16000)
                duration_seconds = (float(nframes) / float(rate)) if rate > 0 else 0.0
                if duration_seconds > STREAMING_AUDIO_THRESHOLD_SECONDS:
                    chunk_iter = self._iter_wav_mono_16k_chunks(wav_path, sample_rate=16000, chunk_seconds=30)
                else:
                    audio = self._read_wav_mono_16k(wav_path, sample_rate=16000)
                    chunk_iter = self._iter_audio_chunks(audio, sample_rate=16000, chunk_seconds=30)

                for chunk in chunk_iter:
                    if chunk is None:
                        continue
                    if len(chunk) == 0:
                        continue
                    inputs = processor(
                        chunk,
                        sampling_rate=16000,
                        return_tensors="pt",
                        return_attention_mask=True,
                    )
                    if "attention_mask" not in inputs and "input_features" in inputs:
                        feats = inputs["input_features"]
                        inputs["attention_mask"] = torch.ones((feats.shape[0], feats.shape[-1]), dtype=torch.long)
                    inputs = {k: v.to(device) for k, v in inputs.items()}
                    if model_dtype is not None and "input_features" in inputs:
                        inputs["input_features"] = inputs["input_features"].to(dtype=model_dtype)
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            message="The attention mask is not set and cannot be inferred from input.*",
                        )
                        warnings.filterwarnings(
                            "ignore",
                            message="A custom logits processor of type .*SuppressTokensLogitsProcessor.*",
                        )
                        warnings.filterwarnings(
                            "ignore",
                            message="A custom logits processor of type .*SuppressTokensAtBeginLogitsProcessor.*",
                        )
                        predicted_ids = asr_model.generate(**inputs, **gen_kwargs)
                    chunk_text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
                    if chunk_text:
                        chunk_texts.append(chunk_text)
            return " ".join(chunk_texts).strip()
        except Exception as e:
            raise RuntimeError(f"HF transcription failed: {e}") from e
