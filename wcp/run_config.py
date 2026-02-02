from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class RunConfig:
    folder: str
    tz: str = "+00:00"
    out: str = "out"
    quiet: bool = False
    progress_every: int = 50
    format: str = "auto"
    date_order: str = "auto"
    no_resume: bool = False
    no_manifest: bool = False
    no_report: bool = False
    no_md: bool = False
    md_max_chars: int = 4000
    no_by_month: bool = False
    audio_workers: int = 2
    ocr_workers: int = 2
    hash_media: bool = False
    me: list[str] = field(default_factory=list)
    them: list[str] = field(default_factory=list)
    convert_audio: str = "mp3"
    no_transcribe: bool = False
    whisper_model: str = "medium"
    lang: str = "auto"
    transcribe_backend: str = "openai"
    no_ocr: bool = False
    ocr_lang: str = "auto"
    ocr_mode: str = "all"
    ocr_max: int = 0
    ocr_edge_threshold: float = 18.0
    ocr_downscale: int = 512
    only_transcribe: bool = False
    only_ocr: bool = False

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.only_transcribe and self.only_ocr:
            errors.append("only_transcribe and only_ocr are mutually exclusive")
        if self.audio_workers < 1:
            errors.append("audio_workers must be >= 1")
        if self.ocr_workers < 1:
            errors.append("ocr_workers must be >= 1")
        if self.progress_every < 1:
            errors.append("progress_every must be >= 1")
        return errors

    @classmethod
    def from_args(cls, args) -> "RunConfig":
        return cls(
            folder=args.folder,
            tz=args.tz,
            out=args.out,
            quiet=args.quiet,
            progress_every=args.progress_every,
            format=args.format,
            date_order=args.date_order,
            no_resume=args.no_resume,
            no_manifest=args.no_manifest,
            no_report=args.no_report,
            no_md=args.no_md,
            md_max_chars=args.md_max_chars,
            no_by_month=args.no_by_month,
            audio_workers=args.audio_workers,
            ocr_workers=args.ocr_workers,
            hash_media=args.hash_media,
            me=list(args.me or []),
            them=list(args.them or []),
            convert_audio=args.convert_audio,
            no_transcribe=args.no_transcribe,
            whisper_model=args.whisper_model,
            lang=args.lang,
            transcribe_backend=args.transcribe_backend,
            no_ocr=args.no_ocr,
            ocr_lang=args.ocr_lang,
            ocr_mode=args.ocr_mode,
            ocr_max=args.ocr_max,
            ocr_edge_threshold=args.ocr_edge_threshold,
            ocr_downscale=args.ocr_downscale,
            only_transcribe=args.only_transcribe,
            only_ocr=args.only_ocr,
        )

    def _add_multi(self, argv: list[str], flag: str, values: Iterable[str]) -> None:
        for v in values:
            if v:
                argv.extend([flag, v])

    def to_argv(self, include_prog: bool = True) -> list[str]:
        argv: list[str] = []
        if include_prog:
            argv.append("whatsapp_export_to_jsonl.py")
        argv.append(self.folder)

        if self.tz != "+00:00":
            argv += ["--tz", self.tz]
        if self.out != "out":
            argv += ["--out", self.out]
        if self.quiet:
            argv.append("--quiet")
        if self.progress_every != 50:
            argv += ["--progress-every", str(self.progress_every)]
        if self.format != "auto":
            argv += ["--format", self.format]
        if self.date_order != "auto":
            argv += ["--date-order", self.date_order]
        if self.no_resume:
            argv.append("--no-resume")
        if self.no_manifest:
            argv.append("--no-manifest")
        if self.no_report:
            argv.append("--no-report")
        if self.no_md:
            argv.append("--no-md")
        if self.md_max_chars != 4000:
            argv += ["--md-max-chars", str(self.md_max_chars)]
        if self.no_by_month:
            argv.append("--no-by-month")
        if self.audio_workers != 2:
            argv += ["--audio-workers", str(self.audio_workers)]
        if self.ocr_workers != 2:
            argv += ["--ocr-workers", str(self.ocr_workers)]
        if self.hash_media:
            argv.append("--hash-media")

        self._add_multi(argv, "--me", self.me)
        self._add_multi(argv, "--them", self.them)

        if self.convert_audio != "mp3":
            argv += ["--convert-audio", self.convert_audio]
        if self.no_transcribe:
            argv.append("--no-transcribe")
        if self.whisper_model != "medium":
            argv += ["--whisper-model", self.whisper_model]
        if self.lang != "auto":
            argv += ["--lang", self.lang]
        if self.transcribe_backend != "openai":
            argv += ["--transcribe-backend", self.transcribe_backend]

        if self.no_ocr:
            argv.append("--no-ocr")
        if self.ocr_lang != "auto":
            argv += ["--ocr-lang", self.ocr_lang]
        if self.ocr_mode != "all":
            argv += ["--ocr-mode", self.ocr_mode]
        if self.ocr_max != 0:
            argv += ["--ocr-max", str(self.ocr_max)]
        if self.ocr_edge_threshold != 18.0:
            argv += ["--ocr-edge-threshold", str(self.ocr_edge_threshold)]
        if self.ocr_downscale != 512:
            argv += ["--ocr-downscale", str(self.ocr_downscale)]

        if self.only_transcribe:
            argv.append("--only-transcribe")
        if self.only_ocr:
            argv.append("--only-ocr")

        return argv
