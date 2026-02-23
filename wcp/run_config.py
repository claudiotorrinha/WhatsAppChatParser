from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_WHISPER_MODELS = (
    "medium",
    "large-v3-turbo",
)
SUPPORTED_SPEED_PRESETS = (
    "auto",
    "off",
)


@dataclass
class RunConfig:
    folder: str
    out: str = "out"
    quiet: bool = False
    force_cpu: bool = False
    no_transcribe: bool = False
    whisper_model: str = "medium"
    speed_preset: str = "auto"
    no_ocr: bool = False

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.speed_preset not in SUPPORTED_SPEED_PRESETS:
            allowed = ", ".join(SUPPORTED_SPEED_PRESETS)
            errors.append(f"speed_preset must be one of: {allowed}")
        if not self.no_transcribe:
            if self.whisper_model not in SUPPORTED_WHISPER_MODELS:
                allowed = ", ".join(SUPPORTED_WHISPER_MODELS)
                errors.append(f"whisper_model must be one of: {allowed}")
        return errors

    @classmethod
    def from_args(cls, args) -> "RunConfig":
        return cls(
            folder=args.folder,
            out=args.out,
            quiet=args.quiet,
            force_cpu=bool(getattr(args, "force_cpu", False)),
            no_transcribe=args.no_transcribe,
            whisper_model=args.whisper_model,
            speed_preset=str(getattr(args, "speed_preset", "auto")),
            no_ocr=args.no_ocr,
        )

    def to_argv(self, include_prog: bool = True) -> list[str]:
        argv: list[str] = []
        if include_prog:
            argv.append("whatsapp_export_to_jsonl.py")
        argv.append(self.folder)

        if self.out != "out":
            argv += ["--out", self.out]
        if self.quiet:
            argv.append("--quiet")
        if self.force_cpu:
            argv.append("--force-cpu")
        if self.no_transcribe:
            argv.append("--no-transcribe")
        if self.whisper_model != "medium":
            argv += ["--whisper-model", self.whisper_model]
        if self.speed_preset != "auto":
            argv += ["--speed-preset", self.speed_preset]
        if self.no_ocr:
            argv.append("--no-ocr")
        return argv
