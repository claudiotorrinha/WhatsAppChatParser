from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Message, MediaRef


PT_LINE_RE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{2}),\s+(?P<hour>\d{1,2}):(?P<min>\d{2})\s+da\s+(?P<period>madrugada|manhã|tarde|noite)\s+-\s+(?P<rest>.*)$",
    re.IGNORECASE,
)

ANDROID_LINE_RE = re.compile(
    r"^\u200e?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s+(?P<time>[^-]+?)\s+-\s+(?P<rest>.*)$",
    re.IGNORECASE,
)

IOS_LINE_RE = re.compile(
    r"^\u200e?\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s+(?P<time>[^\]]+)\]\s+(?P<rest>.*)$",
    re.IGNORECASE,
)

SENDER_RE = re.compile(r"^(?P<sender>[^:]+?):\s+(?P<body>.*)$")

ATTACHMENT_RE = re.compile(
    r"^\u200e?(?P<file>[^\s].*?)\s+\((?P<label>ficheiro\s+anexado|arquivo\s+anexado|archivo\s+adjunto|file\s+attached)\)$",
    re.IGNORECASE,
)
TIME_AMPM_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2})(:(?P<s>\d{2}))?(?P<ampm>am|pm)$",
    re.IGNORECASE,
)
TIME_24H_RE = re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})(:(?P<s>\d{2}))?$")


@dataclass(frozen=True)
class FormatSpec:
    style: str  # "pt" | "android" | "ios"
    date_order: str  # "dmy" | "mdy"


def guess_kind(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in {"opus", "ogg", "m4a", "mp3", "wav", "aac"}:
        return "audio"
    if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "image"
    if ext in {"mp4", "mov", "mkv", "webm", "3gp"}:
        return "video"
    if ext in {"pdf", "doc", "docx", "txt"}:
        return "document"
    return "unknown"


def resolve_tz_offset_str(tz_offset: Optional[str]) -> str:
    if not tz_offset or str(tz_offset).lower() == "auto":
        local = datetime.now().astimezone()
        offset = local.utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hours, minutes = divmod(total_minutes, 60)
        return f"{sign}{hours:02d}:{minutes:02d}"
    return str(tz_offset)


def _parse_tz_offset(tz_offset: str) -> timezone:
    normalized = resolve_tz_offset_str(tz_offset)
    sign = 1 if normalized.startswith("+") else -1
    th, tm = map(int, normalized[1:].split(":"))
    return timezone(sign * timedelta(hours=th, minutes=tm))


def _parse_date(date_str: str, order: str) -> tuple[int, int, int]:
    a, b, c = date_str.split("/")
    if order == "mdy":
        month, day = int(a), int(b)
    else:
        day, month = int(a), int(b)
    year = int(c)
    if year < 100:
        year = 2000 + year
    return year, month, day


def _parse_time(time_str: str) -> tuple[int, int]:
    t = time_str.strip()
    t_clean = t.replace(" ", "").replace(".", "")
    m = TIME_AMPM_RE.match(t_clean)
    if m:
        hour = int(m.group("h"))
        minute = int(m.group("m"))
        ampm = m.group("ampm").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return hour, minute

    m = TIME_24H_RE.match(t)
    if not m:
        raise ValueError(f"Unrecognized time format: {time_str}")
    return int(m.group("h")), int(m.group("m"))


def _parse_datetime(date_str: str, time_str: str, order: str, tz_offset: str) -> datetime:
    year, month, day = _parse_date(date_str, order)
    hour, minute = _parse_time(time_str)
    tz = _parse_tz_offset(tz_offset)
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def parse_portuguese_timestamp(d: str, hh: str, mm: str, period: str, tz_offset: str) -> datetime:
    day, month, yy = map(int, d.split("/"))
    year = 2000 + yy
    hour = int(hh)
    minute = int(mm)

    p = period.lower()
    if p.startswith("mad") or p.startswith("man"):
        if hour == 12:
            hour = 0
    elif p.startswith("tar") or p.startswith("noi"):
        if hour < 12:
            hour += 12

    tz = _parse_tz_offset(tz_offset)
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _detect_date_order(date_strs: list[str], prefer_mdy: bool) -> str:
    dmy = 0
    mdy = 0
    for ds in date_strs:
        parts = ds.split("/")
        if len(parts) != 3:
            continue
        try:
            a = int(parts[0])
            b = int(parts[1])
        except Exception:
            continue
        if a > 12 and b <= 12:
            dmy += 1
        elif b > 12 and a <= 12:
            mdy += 1
    if dmy or mdy:
        return "dmy" if dmy >= mdy else "mdy"
    return "mdy" if prefer_mdy else "dmy"


def _time_strings_have_ampm(time_strs: list[str]) -> bool:
    for t in time_strs:
        t_clean = t.strip().replace(" ", "").replace(".", "").lower()
        if t_clean.endswith("am") or t_clean.endswith("pm"):
            return True
    return False


def _detect_order_for_style(chat_txt: Path, style: str, sample_lines: int = 2000) -> str:
    if style == "pt":
        return "dmy"

    dates: list[str] = []
    times: list[str] = []
    with chat_txt.open("r", encoding="utf-8", errors="replace") as f:
        for i, raw in enumerate(f):
            if i >= sample_lines:
                break
            line = raw.rstrip("\n")
            if style == "ios":
                m = IOS_LINE_RE.match(line)
            else:
                m = ANDROID_LINE_RE.match(line)
            if not m:
                continue
            dates.append(m.group("date"))
            times.append(m.group("time"))

    prefer_mdy = _time_strings_have_ampm(times)
    return _detect_date_order(dates, prefer_mdy=prefer_mdy)


def detect_format(chat_txt: Path, sample_lines: int = 2000) -> FormatSpec:
    counts = {"pt": 0, "android": 0, "ios": 0}
    dates: dict[str, list[str]] = {"pt": [], "android": [], "ios": []}
    times: dict[str, list[str]] = {"android": [], "ios": []}

    with chat_txt.open("r", encoding="utf-8", errors="replace") as f:
        for i, raw in enumerate(f):
            if i >= sample_lines:
                break
            line = raw.rstrip("\n")

            m = PT_LINE_RE.match(line)
            if m:
                counts["pt"] += 1
                dates["pt"].append(m.group("date"))
                continue

            m = IOS_LINE_RE.match(line)
            if m:
                counts["ios"] += 1
                dates["ios"].append(m.group("date"))
                times["ios"].append(m.group("time"))
                continue

            m = ANDROID_LINE_RE.match(line)
            if m:
                counts["android"] += 1
                dates["android"].append(m.group("date"))
                times["android"].append(m.group("time"))
                continue

    if counts["pt"] > 0:
        return FormatSpec(style="pt", date_order="dmy")
    if counts["ios"] >= counts["android"] and counts["ios"] > 0:
        prefer_mdy = _time_strings_have_ampm(times["ios"])
        order = _detect_date_order(dates["ios"], prefer_mdy=prefer_mdy)
        return FormatSpec(style="ios", date_order=order)
    if counts["android"] > 0:
        prefer_mdy = _time_strings_have_ampm(times["android"])
        order = _detect_date_order(dates["android"], prefer_mdy=prefer_mdy)
        return FormatSpec(style="android", date_order=order)

    # Fallback (best effort)
    return FormatSpec(style="android", date_order="dmy")


def resolve_format(
    chat_txt: Path,
    format_override: Optional[str] = None,
    date_order_override: Optional[str] = None,
) -> FormatSpec:
    fmt = (format_override or "auto").lower()
    date_order = (date_order_override or "auto").lower()

    if fmt in {"pt", "android", "ios"}:
        if fmt == "pt":
            return FormatSpec(style="pt", date_order="dmy")
        if date_order in {"dmy", "mdy"}:
            return FormatSpec(style=fmt, date_order=date_order)
        order = _detect_order_for_style(chat_txt, fmt)
        return FormatSpec(style=fmt, date_order=order)

    detected = detect_format(chat_txt)
    if detected.style in {"android", "ios"} and date_order in {"dmy", "mdy"}:
        return FormatSpec(style=detected.style, date_order=date_order)
    return detected


def _match_line(line: str, fmt: FormatSpec):
    if fmt.style == "pt":
        return PT_LINE_RE.match(line)
    if fmt.style == "ios":
        return IOS_LINE_RE.match(line)
    return ANDROID_LINE_RE.match(line)


def find_chat_txt(folder: Path) -> Path:
    txts = sorted(folder.glob("*.txt"))
    if not txts:
        raise FileNotFoundError("No .txt chat export found in folder.")
    if len(txts) == 1:
        return txts[0]
    for t in txts:
        if "whatsapp" in t.name.lower() and "chat" in t.name.lower():
            return t
    return txts[0]


def count_total_messages(
    chat_txt: Path,
    format_override: Optional[str] = None,
    date_order_override: Optional[str] = None,
) -> int:
    fmt = resolve_format(chat_txt, format_override, date_order_override)
    total = 0
    with chat_txt.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if _match_line(line, fmt):
                total += 1
    return total


def iter_messages(
    chat_txt: Path,
    tz_offset: str,
    format_override: Optional[str] = None,
    date_order_override: Optional[str] = None,
) -> Iterable[Message]:
    fmt = resolve_format(chat_txt, format_override, date_order_override)
    current: Optional[Message] = None
    with chat_txt.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = _match_line(line, fmt)
            if m:
                if current:
                    yield current

                if fmt.style == "pt":
                    dt = parse_portuguese_timestamp(
                        m.group("date"), m.group("hour"), m.group("min"), m.group("period"), tz_offset
                    )
                    rest = m.group("rest")
                else:
                    dt = _parse_datetime(m.group("date"), m.group("time"), fmt.date_order, tz_offset)
                    rest = m.group("rest")

                sender = None
                body = rest
                sm = SENDER_RE.match(rest)
                if sm:
                    sender = sm.group("sender").strip()
                    body = sm.group("body")
                    msg_type = "text"
                else:
                    msg_type = "system"

                media: list[MediaRef] = []
                text: Optional[str] = body

                am = ATTACHMENT_RE.match(body)
                if am:
                    fn = am.group("file").strip()
                    kind = guess_kind(fn)
                    media = [MediaRef(file=fn, kind=kind)]
                    msg_type = kind if kind in {"audio", "image", "video", "document"} else "unknown"
                    text = None

                current = Message(
                    ts=dt.isoformat(),
                    sender=sender,
                    type=msg_type,
                    text=text,
                    media=media,
                    enrichment=None,
                    source_line=line,
                )
            else:
                if current is None:
                    continue
                if current.text is None:
                    current.text = line
                else:
                    current.text += "\n" + line

    if current:
        yield current
