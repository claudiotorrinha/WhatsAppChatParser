from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional


def write_report(
    *,
    path: Path,
    chat_file: str,
    export_folder: Path,
    out_dir: Path,
    resume: bool,
    tz: str,
    workers: dict,
    participants: list[str],
    me: list[str],
    them: list[str],
    date_range: tuple[Optional[datetime], Optional[datetime]],
    outputs: dict,
    stats: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    min_dt, max_dt = date_range

    with path.open("w", encoding="utf-8") as rf:
        rf.write("# WhatsAppChatProcessor Report\n\n")
        rf.write(f"- Chat file: `{chat_file}`\n")
        rf.write(f"- Export folder: `{export_folder}`\n")
        rf.write(f"- Output folder: `{out_dir}`\n")
        rf.write(f"- Resume: `{resume}`\n")
        rf.write(f"- TZ: `{tz}`\n")
        rf.write(f"- Workers: `{workers}`\n\n")

        if min_dt and max_dt:
            rf.write(f"- Date range: `{min_dt.isoformat()}` → `{max_dt.isoformat()}`\n")
        rf.write(f"- Participants detected: {', '.join(participants) if participants else '(none)'}\n")
        rf.write(f"- ME mapping: {me if me else '(none)'}\n")
        rf.write(f"- THEM mapping: {them if them else '(none)'}\n\n")

        rf.write("## Outputs\n\n")
        for k, v in outputs.items():
            rf.write(f"- {k}: `{v}`\n")
        rf.write("\n")

        rf.write("## Summary\n\n")
        for k in sorted(stats.keys()):
            rf.write(f"- {k}: {stats[k]}\n")
