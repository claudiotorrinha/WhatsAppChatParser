from __future__ import annotations

from pathlib import Path
from typing import Optional


class Transcriber:
    """Whisper wrapper.

    We default to OpenAI Whisper (reference) to avoid any perceived quality regressions.
    Faster-whisper can be enabled explicitly via backend="faster".
    HuggingFace/Transformers models can be used via backend="hf" (e.g. openai/whisper-large-v3-turbo).
    """

    def __init__(self, model: str = "small", backend: str = "openai"):
        self.model = model
        self.backend_name = backend
        self.backend = None
        self._init_backend()

    def _hf_repo_id(self, model: str) -> str:
        # Allow short names in the UI/CLI while still supporting arbitrary HF repo ids.
        if "/" in model:
            return model
        if model == "large-v3-turbo":
            return "openai/whisper-large-v3-turbo"
        if model in ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3"):
            return f"openai/whisper-{model}"
        return model

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

    def _init_backend(self):
        # Explicit choice
        if self.backend_name == "openai":
            try:
                import whisper  # type: ignore
                self.backend = ("openai", whisper.load_model(self.model))
            except Exception:
                self.backend = None
            return

        if self.backend_name == "faster":
            try:
                from faster_whisper import WhisperModel  # type: ignore
                self.backend = ("faster", WhisperModel(self.model, device="cpu", compute_type="int8"))
            except Exception:
                self.backend = None
            return

        if self.backend_name in ("hf", "transformers"):
            try:
                import torch  # type: ignore
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor  # type: ignore

                repo_id = self._hf_repo_id(self.model)
                device = "cuda" if torch.cuda.is_available() else "cpu"
                dtype = torch.float16 if device == "cuda" else torch.float32

                processor = AutoProcessor.from_pretrained(repo_id)
                model = AutoModelForSpeechSeq2Seq.from_pretrained(repo_id, torch_dtype=dtype)
                model.to(device)
                model.eval()
                self.backend = ("hf", (processor, model, device))
            except Exception:
                self.backend = None
            return

        # auto: try openai first, then hf, then faster
        try:
            import whisper  # type: ignore
            self.backend = ("openai", whisper.load_model(self.model))
            return
        except Exception:
            pass

        try:
            import torch  # type: ignore
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor  # type: ignore

            repo_id = self._hf_repo_id(self.model)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if device == "cuda" else torch.float32

            processor = AutoProcessor.from_pretrained(repo_id)
            model = AutoModelForSpeechSeq2Seq.from_pretrained(repo_id, torch_dtype=dtype)
            model.to(device)
            model.eval()
            self.backend = ("hf", (processor, model, device))
            return
        except Exception:
            pass

        try:
            from faster_whisper import WhisperModel  # type: ignore
            self.backend = ("faster", WhisperModel(self.model, device="cpu", compute_type="int8"))
            return
        except Exception:
            self.backend = None

    def available(self) -> bool:
        return self.backend is not None

    def transcribe_wav(self, wav_path: Path, language: Optional[str] = "pt") -> str:
        if not self.backend:
            raise RuntimeError("No transcription backend installed.")

        kind, model = self.backend
        if kind == "faster":
            segments, info = model.transcribe(str(wav_path), language=language)
            text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
            return " ".join(text_parts).strip()

        if kind == "hf":
            processor, asr_model, device = model
            try:
                import torch  # type: ignore

                audio = self._read_wav_mono_16k(wav_path)
                inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}

                # If the model is in fp16 (common on CUDA), the input features must match dtype.
                model_dtype = getattr(asr_model, "dtype", None)
                if model_dtype is None:
                    try:
                        model_dtype = next(asr_model.parameters()).dtype
                    except Exception:
                        model_dtype = None
                if model_dtype is not None and "input_features" in inputs:
                    inputs["input_features"] = inputs["input_features"].to(dtype=model_dtype)

                gen_kwargs = {}
                if language:
                    # Transformers whisper uses 2-letter codes like "pt", "en", etc.
                    if hasattr(processor, "get_decoder_prompt_ids"):
                        try:
                            gen_kwargs["forced_decoder_ids"] = processor.get_decoder_prompt_ids(language=language, task="transcribe")
                        except Exception:
                            pass

                with torch.inference_mode():
                    predicted_ids = asr_model.generate(**inputs, **gen_kwargs)
                text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
                return (text or "").strip()
            except Exception as e:
                raise RuntimeError(f"HF transcription failed: {e}") from e

        result = model.transcribe(str(wav_path), language=language)
        return (result.get("text") or "").strip()
