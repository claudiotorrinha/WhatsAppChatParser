from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MediaMeta:
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class MediaRef:
    file: str
    kind: str  # audio|image|video|document|unknown
    converted_file: Optional[str] = None
    meta: Optional[MediaMeta] = None


@dataclass
class Enrichment:
    ocr_text_file: Optional[str] = None
    ocr_text: Optional[str] = None
    transcript_file: Optional[str] = None
    transcript_text: Optional[str] = None


@dataclass
class Message:
    ts: str               # ISO-8601
    sender: Optional[str] # None for system messages
    type: str             # text|audio|image|video|document|system|unknown
    text: Optional[str]
    media: list[MediaRef]
    enrichment: Optional[Enrichment] = None
    source_line: Optional[str] = None
