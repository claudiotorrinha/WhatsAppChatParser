from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .benchmark import BenchmarkRequest, run_benchmark
from .run_config import RunConfig
from .ziputil import safe_extract_zip, find_export_root


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"
DEFAULT_OUT_DIR = BASE_DIR / "out"
JOB_DIR_NAME = "ui_jobs"
LOG_MAX_BYTES = 5_000_000
LOG_TRIM_BYTES = 2_000_000
BENCH_STATE_DIR_NAME = "ui_benchmarks"


class JobInfo:
    def __init__(self, job_id: str, log_path: Path, out_dir: Path, cfg: RunConfig, argv: list[str]):
        self.job_id = job_id
        self.log_path = log_path
        self.out_dir = out_dir
        self.cfg = cfg
        self.argv = argv
        self.status = "queued"
        self.exit_code: Optional[int] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        self.force_cpu = False


class BenchInfo:
    def __init__(self, bench_id: str, log_path: Path, result_path: Path, out_dir: Path):
        self.bench_id = bench_id
        self.log_path = log_path
        self.result_path = result_path
        self.out_dir = out_dir
        self.status = "queued"
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.error: Optional[str] = None
        self.stop_event = threading.Event()


app = FastAPI(title="WhatsApp Export UI")
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

_jobs: dict[str, JobInfo] = {}
_jobs_lock = threading.Lock()
_benchmarks: dict[str, BenchInfo] = {}
_benchmarks_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _resolve_out_dir(out_dir: str) -> Path:
    p = Path(out_dir).expanduser()
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    return p


def _job_state_dir() -> Path:
    return DEFAULT_OUT_DIR / JOB_DIR_NAME


def _job_state_path(job_id: str) -> Path:
    return _job_state_dir() / f"{job_id}.json"


def _bench_state_dir() -> Path:
    return DEFAULT_OUT_DIR / BENCH_STATE_DIR_NAME


def _bench_state_path(bench_id: str) -> Path:
    return _bench_state_dir() / f"{bench_id}.json"


def _job_public_state(state: dict) -> dict:
    return {
        "job_id": state.get("job_id"),
        "status": state.get("status"),
        "exit_code": state.get("exit_code"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
    }


def _bench_state_dict(bench: BenchInfo) -> dict:
    return {
        "bench_id": bench.bench_id,
        "status": bench.status,
        "started_at": bench.started_at,
        "finished_at": bench.finished_at,
        "log_path": str(bench.log_path),
        "result_path": str(bench.result_path),
        "out_dir": str(bench.out_dir),
        "error": bench.error,
        "updated_at": _now_iso(),
    }


def _bench_public_state(state: dict) -> dict:
    return {
        "bench_id": state.get("bench_id"),
        "status": state.get("status"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
    }


def _job_state_dict(job: JobInfo) -> dict:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "exit_code": job.exit_code,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "log_path": str(job.log_path),
        "out_dir": str(job.out_dir),
        "force_cpu": job.force_cpu,
        "argv": job.argv,
        "config": asdict(job.cfg),
        "pid": job.process.pid if job.process else None,
        "updated_at": _now_iso(),
    }


def _save_job_state(job: JobInfo) -> None:
    path = _job_state_path(job.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(_job_state_dict(job), f, indent=2, sort_keys=True)
    tmp.replace(path)


def _save_bench_state(bench: BenchInfo) -> None:
    path = _bench_state_path(bench.bench_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(_bench_state_dict(bench), f, indent=2, sort_keys=True)
    tmp.replace(path)


def _load_job_state(job_id: str) -> Optional[dict]:
    path = _job_state_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_bench_state(bench_id: str) -> Optional[dict]:
    path = _bench_state_path(bench_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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


def _parse_csv(val: Optional[str]) -> list[str]:
    if not val:
        return []
    items = []
    for raw in str(val).split(","):
        item = raw.strip()
        if item:
            items.append(item)
    return items


def _default_workers() -> int:
    return min(2, os.cpu_count() or 4)


def _build_run_config(form, zip_path: Path, out_dir_str: str) -> RunConfig:
    defaults = RunConfig(folder=str(zip_path))

    progress_every = _parse_int(form.get("progress_every"))
    if progress_every is None:
        progress_every = defaults.progress_every

    md_max_chars = _parse_int(form.get("md_max_chars"))
    if md_max_chars is None:
        md_max_chars = defaults.md_max_chars

    audio_workers = _parse_int(form.get("audio_workers"))
    if audio_workers is None:
        audio_workers = _default_workers()

    ocr_workers = _parse_int(form.get("ocr_workers"))
    if ocr_workers is None:
        ocr_workers = _default_workers()

    ocr_max = _parse_int(form.get("ocr_max"))
    if ocr_max is None:
        ocr_max = defaults.ocr_max

    ocr_edge_threshold = _parse_float(form.get("ocr_edge_threshold"))
    if ocr_edge_threshold is None:
        ocr_edge_threshold = defaults.ocr_edge_threshold

    ocr_downscale = _parse_int(form.get("ocr_downscale"))
    if ocr_downscale is None:
        ocr_downscale = defaults.ocr_downscale

    return RunConfig(
        folder=str(zip_path),
        tz=str(form.get("tz") or defaults.tz),
        out=out_dir_str or defaults.out,
        quiet=_parse_bool(form.get("quiet")),
        progress_every=progress_every,
        format=str(form.get("format") or defaults.format),
        date_order=str(form.get("date_order") or defaults.date_order),
        no_resume=_parse_bool(form.get("no_resume")),
        no_manifest=_parse_bool(form.get("no_manifest")),
        no_report=_parse_bool(form.get("no_report")),
        no_md=_parse_bool(form.get("no_md")),
        md_max_chars=md_max_chars,
        no_by_month=_parse_bool(form.get("no_by_month")),
        audio_workers=audio_workers,
        ocr_workers=ocr_workers,
        hash_media=_parse_bool(form.get("hash_media")),
        me=_parse_csv(form.get("me")),
        them=_parse_csv(form.get("them")),
        convert_audio=str(form.get("convert_audio") or defaults.convert_audio),
        no_transcribe=_parse_bool(form.get("no_transcribe")),
        whisper_model=str(form.get("whisper_model") or defaults.whisper_model),
        lang=str(form.get("lang") or defaults.lang),
        transcribe_backend=str(form.get("transcribe_backend") or defaults.transcribe_backend),
        no_ocr=_parse_bool(form.get("no_ocr")),
        ocr_lang=str(form.get("ocr_lang") or defaults.ocr_lang),
        ocr_mode=str(form.get("ocr_mode") or defaults.ocr_mode),
        ocr_max=ocr_max,
        ocr_edge_threshold=ocr_edge_threshold,
        ocr_downscale=ocr_downscale,
        only_transcribe=_parse_bool(form.get("only_transcribe")),
        only_ocr=_parse_bool(form.get("only_ocr")),
    )


def _check_transcribe_backend(cfg: RunConfig) -> Optional[str]:
    if cfg.no_transcribe or cfg.only_ocr:
        return None
    info = _runtime_info()
    openai_ok = bool(info.get("openai_whisper_available") or info.get("whisper_available"))
    faster_ok = bool(info.get("faster_whisper_available"))
    backend = cfg.transcribe_backend
    if backend == "openai" and not openai_ok:
        return "OpenAI Whisper is not installed."
    if backend == "faster" and not faster_ok:
        return "Faster Whisper is not installed."
    if backend == "auto" and not (openai_ok or faster_ok):
        return "No transcription backend is installed."
    return None


def _check_bench_backend(backend: str) -> Optional[str]:
    info = _runtime_info()
    openai_ok = bool(info.get("openai_whisper_available") or info.get("whisper_available"))
    faster_ok = bool(info.get("faster_whisper_available"))
    if backend == "openai" and not openai_ok:
        return "OpenAI Whisper is not installed."
    if backend == "faster" and not faster_ok:
        return "Faster Whisper is not installed."
    if backend == "auto" and not (openai_ok or faster_ok):
        return "No transcription backend is installed."
    return None


def _trim_log(path: Path) -> None:
    if not path.exists():
        return
    size = path.stat().st_size
    if size <= LOG_MAX_BYTES:
        return
    with path.open("rb") as f:
        if size > LOG_TRIM_BYTES:
            f.seek(-LOG_TRIM_BYTES, 2)
        data = f.read()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(b"... log trimmed ...\n")
        f.write(data)
    tmp.replace(path)


class _LogWriter:
    def __init__(self, path: Path):
        self.path = path
        self.handle = path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        self.handle.write(text)
        self.handle.flush()
        if self.path.stat().st_size > LOG_MAX_BYTES:
            self.handle.close()
            _trim_log(self.path)
            self.handle = self.path.open("a", encoding="utf-8")

    def close(self) -> None:
        try:
            self.handle.close()
        except Exception:
            pass


def _runtime_info() -> dict:
    cuda_available: Optional[bool] = None
    torch_version: Optional[str] = None
    whisper_available = False
    faster_whisper_available = False

    try:
        import torch  # type: ignore
        torch_version = getattr(torch, "__version__", None)
        cuda_available = bool(torch.cuda.is_available())
    except Exception:
        cuda_available = None
        torch_version = None

    try:
        import whisper  # type: ignore
        whisper_available = True
    except Exception:
        whisper_available = False

    try:
        import faster_whisper  # type: ignore
        faster_whisper_available = True
    except Exception:
        faster_whisper_available = False

    return {
        "cuda_available": cuda_available,
        "torch_version": torch_version,
        "whisper_available": whisper_available,
        "openai_whisper_available": whisper_available,
        "faster_whisper_available": faster_whisper_available,
    }


def _run_job(job: JobInfo, argv: list[str]) -> None:
    job.status = "running"
    job.started_at = _now_iso()
    _save_job_state(job)
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    log_writer = _LogWriter(job.log_path)
    try:
        cmd = [str(Path.cwd() / ".venv" / "Scripts" / "python.exe")]
        if not Path(cmd[0]).exists():
            cmd = [str(Path(sys.executable))]
        cmd += [str(BASE_DIR / "whatsapp_export_to_jsonl.py")] + argv
        env = os.environ.copy()
        if job.force_cpu:
            env["CUDA_VISIBLE_DEVICES"] = ""

        log_writer.write(f"Command: {' '.join(cmd)}\n")

        job.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        _save_job_state(job)

        if job.process.stdout:
            for line in job.process.stdout:
                log_writer.write(line)

        code = job.process.wait()
        job.exit_code = code
        if job.status != "stopped":
            job.status = "done" if code == 0 else "error"
    except Exception as e:
        log_writer.write(f"ERROR: {e}\n")
        job.exit_code = 1
        job.status = "error"
    finally:
        job.finished_at = _now_iso()
        _save_job_state(job)
        log_writer.close()


def _run_benchmark_job(bench: BenchInfo, req: BenchmarkRequest) -> None:
    bench.status = "running"
    bench.started_at = _now_iso()
    _save_bench_state(bench)
    bench.log_path.parent.mkdir(parents=True, exist_ok=True)
    log_writer = _LogWriter(bench.log_path)

    def log(msg: str) -> None:
        log_writer.write(msg + "\n")

    try:
        result = run_benchmark(req, log, bench.stop_event)
        bench.result_path.parent.mkdir(parents=True, exist_ok=True)
        bench.result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        if bench.stop_event.is_set():
            bench.status = "stopped"
        else:
            bench.status = "done"
    except Exception as e:
        bench.status = "error"
        bench.error = str(e)
        log(f"ERROR: {e}")
    finally:
        bench.finished_at = _now_iso()
        _save_bench_state(bench)
        log_writer.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.post("/api/run")
async def run_job(request: Request, zip: UploadFile = File(...)) -> JSONResponse:
    form = await request.form()

    out_dir_str = str(form.get("out") or "out")
    out_dir = _resolve_out_dir(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = out_dir / "_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    orig_name = Path(zip.filename or "export.zip").name
    safe_name = f"{uuid.uuid4().hex}_{orig_name}"
    zip_path = uploads_dir / safe_name

    with zip_path.open("wb") as f:
        f.write(await zip.read())

    cfg = _build_run_config(form, zip_path, out_dir_str)
    errors = cfg.validate()
    if errors:
        return JSONResponse({"error": "invalid_config", "details": errors}, status_code=400)

    backend_error = _check_transcribe_backend(cfg)
    if backend_error:
        return JSONResponse({"error": "backend_unavailable", "details": backend_error}, status_code=400)

    argv = cfg.to_argv(include_prog=False)
    job_id = uuid.uuid4().hex[:10]
    log_dir = out_dir / "ui_logs"
    log_path = log_dir / f"{job_id}.log"
    job = JobInfo(job_id=job_id, log_path=log_path, out_dir=out_dir, cfg=cfg, argv=argv)
    job.force_cpu = _parse_bool(form.get("force_cpu"))

    with _jobs_lock:
        _jobs[job_id] = job
    _save_job_state(job)

    t = threading.Thread(target=_run_job, args=(job, argv), daemon=True)
    t.start()

    return JSONResponse({"job_id": job_id})


@app.post("/api/benchmark")
async def run_benchmark_api(request: Request, zip: UploadFile = File(...)) -> JSONResponse:
    form = await request.form()

    out_dir_str = str(form.get("out") or "out")
    out_dir = _resolve_out_dir(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = out_dir / "_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    orig_name = Path(zip.filename or "export.zip").name
    safe_name = f"{uuid.uuid4().hex}_{orig_name}"
    zip_path = uploads_dir / safe_name
    with zip_path.open("wb") as f:
        f.write(await zip.read())

    extract_dir = out_dir / "_bench_extracted" / zip_path.stem
    safe_extract_zip(zip_path, extract_dir)
    folder = find_export_root(extract_dir)

    audio_samples = _parse_int(form.get("bench_audio_samples")) or 8
    image_samples = _parse_int(form.get("bench_image_samples")) or 0
    backend = str(form.get("bench_backend") or "auto")
    lang = str(form.get("bench_lang") or "pt")
    force_cpu = _parse_bool(form.get("bench_force_cpu"))

    models = _parse_csv(form.get("bench_models"))
    if not models:
        models = ["base", "small", "medium"]

    bench_error = _check_bench_backend(backend)
    if bench_error:
        return JSONResponse({"error": "backend_unavailable", "details": bench_error}, status_code=400)

    req = BenchmarkRequest(
        folder=folder,
        out_dir=out_dir,
        audio_samples=audio_samples,
        image_samples=image_samples,
        models=models,
        backend=backend,
        lang=lang,
        include_ocr=_parse_bool(form.get("bench_include_ocr")),
        force_cpu=force_cpu,
    )

    bench_id = uuid.uuid4().hex[:10]
    log_dir = out_dir / "benchmarks"
    log_path = log_dir / f"{bench_id}.log"
    result_path = log_dir / f"{bench_id}.json"
    bench = BenchInfo(bench_id=bench_id, log_path=log_path, result_path=result_path, out_dir=out_dir)

    with _benchmarks_lock:
        _benchmarks[bench_id] = bench
    _save_bench_state(bench)

    t = threading.Thread(target=_run_benchmark_job, args=(bench, req), daemon=True)
    t.start()

    return JSONResponse({"bench_id": bench_id})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        state = _load_job_state(job_id)
        if not state:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return JSONResponse(_job_public_state(state))
    return JSONResponse(_job_public_state(_job_state_dict(job)))


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str) -> PlainTextResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        state = _load_job_state(job_id)
        if not state:
            return PlainTextResponse("job_not_found", status_code=404)
        log_path = Path(state.get("log_path") or "")
        if not log_path:
            return PlainTextResponse("log_not_found", status_code=404)
        return PlainTextResponse(_read_tail(log_path))
    return PlainTextResponse(_read_tail(job.log_path))


@app.get("/api/benchmarks/{bench_id}")
def bench_status(bench_id: str) -> JSONResponse:
    with _benchmarks_lock:
        bench = _benchmarks.get(bench_id)
    if not bench:
        state = _load_bench_state(bench_id)
        if not state:
            return JSONResponse({"error": "bench_not_found"}, status_code=404)
        return JSONResponse(_bench_public_state(state))
    return JSONResponse(_bench_public_state(_bench_state_dict(bench)))


@app.get("/api/benchmarks/{bench_id}/log")
def bench_log(bench_id: str) -> PlainTextResponse:
    with _benchmarks_lock:
        bench = _benchmarks.get(bench_id)
    if not bench:
        state = _load_bench_state(bench_id)
        if not state:
            return PlainTextResponse("bench_not_found", status_code=404)
        log_path = Path(state.get("log_path") or "")
        if not log_path:
            return PlainTextResponse("log_not_found", status_code=404)
        return PlainTextResponse(_read_tail(log_path))
    return PlainTextResponse(_read_tail(bench.log_path))


@app.get("/api/benchmarks/{bench_id}/result")
def bench_result(bench_id: str) -> JSONResponse:
    with _benchmarks_lock:
        bench = _benchmarks.get(bench_id)
    if not bench:
        state = _load_bench_state(bench_id)
        if not state:
            return JSONResponse({"error": "bench_not_found"}, status_code=404)
        result_path = Path(state.get("result_path") or "")
        if not result_path.exists():
            return JSONResponse({"error": "result_not_ready"}, status_code=404)
        return JSONResponse(json.loads(result_path.read_text(encoding="utf-8")))
    if not bench.result_path.exists():
        return JSONResponse({"error": "result_not_ready"}, status_code=404)
    return JSONResponse(json.loads(bench.result_path.read_text(encoding="utf-8")))


@app.get("/api/runtime")
def runtime_info() -> JSONResponse:
    return JSONResponse(_runtime_info())


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        state = _load_job_state(job_id)
        if not state:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return JSONResponse({"status": state.get("status"), "error": "job_not_running"}, status_code=409)

    if job.process and job.process.poll() is None:
        try:
            job.process.terminate()
            job.status = "stopped"
            job.finished_at = _now_iso()
            _save_job_state(job)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"status": job.status})


@app.post("/api/benchmarks/{bench_id}/stop")
def stop_bench(bench_id: str) -> JSONResponse:
    with _benchmarks_lock:
        bench = _benchmarks.get(bench_id)
    if not bench:
        state = _load_bench_state(bench_id)
        if not state:
            return JSONResponse({"error": "bench_not_found"}, status_code=404)
        return JSONResponse({"status": state.get("status"), "error": "bench_not_running"}, status_code=409)
    bench.stop_event.set()
    bench.status = "stopped"
    bench.finished_at = _now_iso()
    _save_bench_state(bench)
    return JSONResponse({"status": bench.status})


def main() -> None:
    import uvicorn

    uvicorn.run("wcp.ui_app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
