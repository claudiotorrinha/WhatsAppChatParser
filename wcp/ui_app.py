from __future__ import annotations

import threading
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .main import run as run_pipeline


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"
DEFAULT_OUT_DIR = BASE_DIR / "out"
UPLOAD_DIR = DEFAULT_OUT_DIR / "_uploads"


class JobInfo:
    def __init__(self, job_id: str, log_path: Path):
        self.job_id = job_id
        self.log_path = log_path
        self.status = "queued"
        self.exit_code: Optional[int] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None


app = FastAPI(title="WhatsApp Export UI")
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

_jobs: dict[str, JobInfo] = {}
_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _resolve_out_dir(out_dir: str) -> Path:
    p = Path(out_dir).expanduser()
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    return p


def _read_tail(path: Path, max_bytes: int = 20000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        start = max(0, end - max_bytes)
        f.seek(start)
        data = f.read()
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _parse_bool(val: Optional[str]) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(val: Optional[str]) -> Optional[int]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(val)
    except Exception:
        return None


def _parse_float(val: Optional[str]) -> Optional[float]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except Exception:
        return None


def _add_multi(args: list[str], flag: str, value: Optional[str]) -> None:
    if not value:
        return
    for item in value.split(","):
        item = item.strip()
        if item:
            args.extend([flag, item])


def _run_job(job: JobInfo, args: list[str]) -> None:
    job.status = "running"
    job.started_at = _now_iso()
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    with job.log_path.open("w", encoding="utf-8") as logf:
        try:
            with redirect_stdout(logf), redirect_stderr(logf):
                code = run_pipeline(args)
            job.exit_code = code
            job.status = "done" if code == 0 else "error"
        except Exception as e:
            logf.write(f"ERROR: {e}\n")
            job.exit_code = 1
            job.status = "error"
        finally:
            job.finished_at = _now_iso()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.post("/api/run")
async def run_job(request: Request, zip: UploadFile = File(...)) -> JSONResponse:
    form = await request.form()

    out_dir_str = str(form.get("out") or "out")
    out_dir = _resolve_out_dir(out_dir_str)
    uploads_dir = out_dir / "_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    orig_name = Path(zip.filename or "export.zip").name
    safe_name = f"{uuid.uuid4().hex}_{orig_name}"
    zip_path = uploads_dir / safe_name

    with zip_path.open("wb") as f:
        f.write(await zip.read())

    args = ["whatsapp_export_to_jsonl.py", str(zip_path)]
    tz = str(form.get("tz") or "+00:00")
    args += ["--tz", tz]
    args += ["--out", out_dir_str]

    fmt = str(form.get("format") or "auto")
    date_order = str(form.get("date_order") or "auto")
    args += ["--format", fmt, "--date-order", date_order]

    if _parse_bool(form.get("quiet")):
        args.append("--quiet")

    progress_every = _parse_int(form.get("progress_every"))
    if progress_every is not None:
        args += ["--progress-every", str(progress_every)]

    if _parse_bool(form.get("no_resume")):
        args.append("--no-resume")
    if _parse_bool(form.get("no_manifest")):
        args.append("--no-manifest")
    if _parse_bool(form.get("no_report")):
        args.append("--no-report")
    if _parse_bool(form.get("no_md")):
        args.append("--no-md")
    if _parse_bool(form.get("no_by_month")):
        args.append("--no-by-month")

    audio_workers = _parse_int(form.get("audio_workers"))
    if audio_workers is not None:
        args += ["--audio-workers", str(audio_workers)]
    ocr_workers = _parse_int(form.get("ocr_workers"))
    if ocr_workers is not None:
        args += ["--ocr-workers", str(ocr_workers)]

    if _parse_bool(form.get("hash_media")):
        args.append("--hash-media")

    _add_multi(args, "--me", str(form.get("me") or "").strip() or None)
    _add_multi(args, "--them", str(form.get("them") or "").strip() or None)

    convert_audio = str(form.get("convert_audio") or "mp3")
    args += ["--convert-audio", convert_audio]

    if _parse_bool(form.get("no_transcribe")):
        args.append("--no-transcribe")
    whisper_model = str(form.get("whisper_model") or "small")
    args += ["--whisper-model", whisper_model]
    lang = str(form.get("lang") or "pt")
    args += ["--lang", lang]
    transcribe_backend = str(form.get("transcribe_backend") or "openai")
    args += ["--transcribe-backend", transcribe_backend]

    if _parse_bool(form.get("no_ocr")):
        args.append("--no-ocr")
    ocr_lang = str(form.get("ocr_lang") or "por")
    args += ["--ocr-lang", ocr_lang]
    ocr_mode = str(form.get("ocr_mode") or "all")
    args += ["--ocr-mode", ocr_mode]
    ocr_max = _parse_int(form.get("ocr_max"))
    if ocr_max is not None:
        args += ["--ocr-max", str(ocr_max)]
    ocr_edge_threshold = _parse_float(form.get("ocr_edge_threshold"))
    if ocr_edge_threshold is not None:
        args += ["--ocr-edge-threshold", str(ocr_edge_threshold)]
    ocr_downscale = _parse_int(form.get("ocr_downscale"))
    if ocr_downscale is not None:
        args += ["--ocr-downscale", str(ocr_downscale)]

    if _parse_bool(form.get("only_transcribe")):
        args.append("--only-transcribe")
    if _parse_bool(form.get("only_ocr")):
        args.append("--only-ocr")

    job_id = uuid.uuid4().hex[:10]
    log_dir = out_dir / "ui_logs"
    log_path = log_dir / f"{job_id}.log"
    job = JobInfo(job_id=job_id, log_path=log_path)

    with _jobs_lock:
        _jobs[job_id] = job

    t = threading.Thread(target=_run_job, args=(job, args), daemon=True)
    t.start()

    return JSONResponse({"job_id": job_id})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "job_not_found"}, status_code=404)
    return JSONResponse({
        "job_id": job.job_id,
        "status": job.status,
        "exit_code": job.exit_code,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    })


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str) -> PlainTextResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return PlainTextResponse("job_not_found", status_code=404)
    return PlainTextResponse(_read_tail(job.log_path))


def main() -> None:
    import uvicorn

    uvicorn.run("wcp.ui_app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
