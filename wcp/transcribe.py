from __future__ import annotations

from pathlib import Path
from typing import Optional


class Transcriber:
    """Whisper wrapper.

    We default to OpenAI Whisper (reference) to avoid any perceived quality regressions.
    Faster-whisper can be enabled explicitly via backend="faster".
    """

    def __init__(self, model: str = "small", backend: str = "openai"):
        self.model = model
        self.backend_name = backend
        self.backend = None
        self._init_backend()

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

        # auto: try openai first, then faster
        try:
            import whisper  # type: ignore
            self.backend = ("openai", whisper.load_model(self.model))
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

        result = model.transcribe(str(wav_path), language=language)
        return (result.get("text") or "").strip()
