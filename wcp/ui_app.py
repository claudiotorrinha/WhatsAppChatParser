from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .run_config import RunConfig, SUPPORTED_WHISPER_MODELS


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"
DEFAULT_OUT_DIR = BASE_DIR / "out"
JOB_DIR_NAME = "ui_jobs"
LOG_MAX_BYTES = 5_000_000
LOG_TRIM_BYTES = 2_000_000
STATE_INDEX_PATH = DEFAULT_OUT_DIR / "ui_state_index.json"


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


def _job_state_dir() -> Path:
    # Legacy location (kept for backward compatibility only).
    return DEFAULT_OUT_DIR / JOB_DIR_NAME


def _job_state_path(job_id: str) -> Path:
    return _job_state_dir() / f"{job_id}.json"


def _load_state_index() -> dict:
    if not STATE_INDEX_PATH.exists():
        return {"jobs": {}}
    try:
        obj = json.loads(STATE_INDEX_PATH.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {"jobs": {}}
        obj.setdefault("jobs", {})
        if not isinstance(obj["jobs"], dict):
            obj["jobs"] = {}
        return obj
    except Exception:
        return {"jobs": {}}


def _save_state_index(index: dict) -> None:
    STATE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_INDEX_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
    tmp.replace(STATE_INDEX_PATH)


def _index_job(job_id: str, *, out_dir: Path, state_path: Path, log_path: Path) -> None:
    idx = _load_state_index()
    idx["jobs"][job_id] = {
        "out_dir": str(out_dir),
        "state_path": str(state_path),
        "log_path": str(log_path),
        "updated_at": _now_iso(),
    }
    _save_state_index(idx)


def _resolve_job_state_path(job_id: str) -> Optional[Path]:
    idx = _load_state_index()
    rec = idx.get("jobs", {}).get(job_id)
    if isinstance(rec, dict):
        p = rec.get("state_path")
        if isinstance(p, str) and p:
            return Path(p)
    legacy = _job_state_path(job_id)
    return legacy if legacy.exists() else None


def _job_public_state(state: dict) -> dict:
    return {
        "job_id": state.get("job_id"),
        "status": state.get("status"),
        "exit_code": state.get("exit_code"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
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
    # Store state alongside the chosen output folder for coherence.
    path = job.out_dir / JOB_DIR_NAME / f"{job.job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(_job_state_dict(job), f, indent=2, sort_keys=True)
    tmp.replace(path)
    _index_job(job.job_id, out_dir=job.out_dir, state_path=path, log_path=job.log_path)


def _load_job_state(job_id: str) -> Optional[dict]:
    path = _resolve_job_state_path(job_id)
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _persist_loaded_state(job_id: str, state: dict) -> None:
    path = _resolve_job_state_path(job_id)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)

    out_dir_raw = state.get("out_dir")
    log_path_raw = state.get("log_path")
    if isinstance(out_dir_raw, str) and out_dir_raw and isinstance(log_path_raw, str) and log_path_raw:
        try:
            _index_job(job_id, out_dir=Path(out_dir_raw), state_path=path, log_path=Path(log_path_raw))
        except Exception:
            pass


def _is_pid_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip()
            if not out:
                return False
            if "No tasks are running" in out:
                return False
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _reconcile_loaded_state(job_id: str, state: dict) -> dict:
    if state.get("status") != "running":
        return state
    if _is_pid_running(state.get("pid")):
        return state
    fixed = dict(state)
    fixed["status"] = "error"
    fixed["exit_code"] = 1 if fixed.get("exit_code") is None else fixed.get("exit_code")
    if not fixed.get("finished_at"):
        fixed["finished_at"] = _now_iso()
    fixed["updated_at"] = _now_iso()
    _persist_loaded_state(job_id, fixed)
    return fixed


def _find_active_job() -> Optional[str]:
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.process and job.process.poll() is None:
                return job_id

    idx = _load_state_index()
    jobs = idx.get("jobs", {})
    if not isinstance(jobs, dict):
        return None
    for job_id, rec in jobs.items():
        if not isinstance(rec, dict):
            continue
        p = rec.get("state_path")
        if not isinstance(p, str) or not p:
            continue
        state_path = Path(p)
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        state = _reconcile_loaded_state(str(job_id), state)
        if state.get("status") == "running":
            return str(job_id)
    return None


def _append_stop_manifest_if_open(out_dir: Path, *, reason: str) -> None:
    manifest_path = out_dir / "manifest.jsonl"
    if not manifest_path.exists():
        return
    try:
        last_start = -1
        last_end = -1
        with manifest_path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type")
                if t == "run_start":
                    last_start = i
                elif t == "run_end":
                    last_end = i
        if last_start <= last_end:
            return

        now_iso = _now_iso()
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "type": "run_stopped",
                        "reason": reason,
                        "event_ts": now_iso,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "run_end",
                        "elapsed_seconds": 0.0,
                        "stats": {},
                        "status": "stopped",
                        "event_ts": now_iso,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


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


def _build_run_config(form, zip_path: Path, out_dir_str: str) -> RunConfig:
    defaults = RunConfig(folder=str(zip_path))
    force_cpu = _parse_bool(form.get("force_cpu"))
    no_transcribe = _parse_bool(form.get("no_transcribe"))
    no_ocr = _parse_bool(form.get("no_ocr"))

    return RunConfig(
        folder=str(zip_path),
        out=out_dir_str or defaults.out,
        quiet=False,
        force_cpu=force_cpu,
        no_transcribe=no_transcribe,
        whisper_model=str(form.get("whisper_model") or defaults.whisper_model),
        speed_preset=str(form.get("speed_preset") or defaults.speed_preset),
        no_ocr=no_ocr,
    )


def _check_transcription_runtime(cfg: RunConfig) -> Optional[str]:
    if cfg.no_transcribe:
        return None
    info = _runtime_info()
    if not info.get("transformers_available"):
        return "Transformers (HF) is not installed."
    return None


def _system_dependency_hints() -> dict[str, str]:
    if os.name == "nt":
        return {
            "ffmpeg": "Install ffmpeg: winget install --id Gyan.FFmpeg -e",
            "tesseract": "Install tesseract: winget install --id UB-Mannheim.TesseractOCR -e",
        }
    if shutil.which("apt-get"):
        return {
            "ffmpeg": "Install ffmpeg: sudo apt-get update && sudo apt-get install -y ffmpeg",
            "tesseract": "Install tesseract: sudo apt-get update && sudo apt-get install -y tesseract-ocr",
        }
    if shutil.which("brew"):
        return {
            "ffmpeg": "Install ffmpeg: brew install ffmpeg",
            "tesseract": "Install tesseract: brew install tesseract",
        }
    return {
        "ffmpeg": "Install ffmpeg using your system package manager.",
        "tesseract": "Install tesseract using your system package manager.",
    }


def _check_runtime_requirements(cfg: RunConfig) -> list[str]:
    info = _runtime_info()
    hints = info.get("install_hints", {})
    errors: list[str] = []

    if not cfg.no_transcribe:
        if not info.get("transformers_available"):
            errors.append("Missing Python package: transformers.")
        if not info.get("torch_available"):
            errors.append("Missing Python package: torch.")
        if not info.get("ffmpeg_available"):
            msg = "Missing system dependency: ffmpeg."
            hint = hints.get("ffmpeg")
            errors.append(f"{msg} {hint}" if hint else msg)

    if not cfg.no_ocr:
        if not info.get("tesseract_available"):
            msg = "Missing system dependency: tesseract."
            hint = hints.get("tesseract")
            errors.append(f"{msg} {hint}" if hint else msg)

    return errors


def _check_audio_test_requirements(info: dict) -> list[str]:
    hints = info.get("install_hints", {})
    errors: list[str] = []
    if not info.get("transformers_available"):
        errors.append("Missing Python package: transformers.")
    if not info.get("torch_available"):
        errors.append("Missing Python package: torch.")
    if not info.get("ffmpeg_available"):
        msg = "Missing system dependency: ffmpeg."
        hint = hints.get("ffmpeg")
        errors.append(f"{msg} {hint}" if hint else msg)
    return errors


def _transcribe_audio_sample(src_audio: Path, *, model: str, force_cpu: bool) -> tuple[str, float]:
    from .media import convert_to_wav
    from .transcribe import Transcriber

    wav_path = src_audio.parent / "sample_input_16k.wav"
    started = time.perf_counter()
    convert_to_wav(src_audio, wav_path)

    transcriber = Transcriber(model, device=("cpu" if force_cpu else None))
    if not transcriber.available():
        detail = transcriber.backend_error()
        if detail:
            raise RuntimeError(f"HF transcription backend is unavailable. {detail}")
        raise RuntimeError("HF transcription backend is unavailable.")
    text = transcriber.transcribe_wav(wav_path, language=None)
    elapsed = time.perf_counter() - started
    return text, elapsed


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


async def _save_upload_file(upload: UploadFile, dst: Path, chunk_size: int = 1024 * 1024) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
    try:
        await upload.close()
    except Exception:
        pass


def _runtime_info() -> dict:
    cuda_available: Optional[bool] = None
    torch_version: Optional[str] = None
    transformers_available = False
    torch_available = False
    ffmpeg_available = bool(shutil.which("ffmpeg"))
    tesseract_available = bool(shutil.which("tesseract"))

    try:
        import torch  # type: ignore

        torch_version = getattr(torch, "__version__", None)
        cuda_available = bool(torch.cuda.is_available())
        torch_available = True
    except Exception:
        cuda_available = None
        torch_version = None
        torch_available = False

    try:
        import transformers  # type: ignore

        transformers_available = True
    except Exception:
        transformers_available = False

    return {
        "cuda_available": cuda_available,
        "torch_available": torch_available,
        "torch_version": torch_version,
        "transformers_available": transformers_available,
        "ffmpeg_available": ffmpeg_available,
        "tesseract_available": tesseract_available,
        "install_hints": _system_dependency_hints(),
        "supported_transcription_backend": "hf",
        "supported_transcription_models": list(SUPPORTED_WHISPER_MODELS),
        "supported_speed_presets": ["auto", "off"],
    }


def _request_stop_pid(pid: object, *, timeout_seconds: float = 8.0) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return True
    try:
        if os.name == "nt":
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    return False
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        return False

    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.2)
    return not _is_pid_running(pid)


def _force_stop_pid(pid: object) -> None:
    if not isinstance(pid, int) or pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


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

        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        job.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            **popen_kwargs,
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.post("/api/run")
async def run_job(request: Request, zip: UploadFile = File(...)) -> JSONResponse:
    form = await request.form()

    out_dir_str = str(form.get("out") or "out")
    out_dir = _resolve_out_dir(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    active_job_id = _find_active_job()
    if active_job_id:
        return JSONResponse({"error": "job_already_running", "job_id": active_job_id}, status_code=409)
    uploads_dir = out_dir / "_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    orig_name = Path(zip.filename or "export.zip").name
    safe_name = f"{uuid.uuid4().hex}_{orig_name}"
    zip_path = uploads_dir / safe_name
    await _save_upload_file(zip, zip_path)

    cfg = _build_run_config(form, zip_path, out_dir_str)
    errors = cfg.validate()
    if errors:
        return JSONResponse({"error": "invalid_config", "details": errors}, status_code=400)

    backend_error = _check_transcription_runtime(cfg)
    if backend_error:
        return JSONResponse({"error": "backend_unavailable", "details": backend_error}, status_code=400)
    req_errors = _check_runtime_requirements(cfg)
    if req_errors:
        return JSONResponse({"error": "missing_requirements", "details": req_errors}, status_code=400)

    argv = cfg.to_argv(include_prog=False)
    job_id = uuid.uuid4().hex[:10]
    log_dir = out_dir / "ui_logs"
    log_path = log_dir / f"{job_id}.log"
    job = JobInfo(job_id=job_id, log_path=log_path, out_dir=out_dir, cfg=cfg, argv=argv)
    job.force_cpu = cfg.force_cpu

    with _jobs_lock:
        _jobs[job_id] = job
    _save_job_state(job)

    t = threading.Thread(target=_run_job, args=(job, argv), daemon=True)
    t.start()

    return JSONResponse({"job_id": job_id})


@app.post("/api/transcribe-test")
async def transcribe_test(request: Request, audio: UploadFile = File(...)) -> JSONResponse:
    form = await request.form()
    model = str(form.get("whisper_model") or "medium")
    force_cpu = _parse_bool(form.get("force_cpu"))

    cfg = RunConfig(
        folder=".",
        out="out",
        quiet=True,
        force_cpu=force_cpu,
        no_transcribe=False,
        whisper_model=model,
        no_ocr=True,
    )
    errors = cfg.validate()
    if errors:
        return JSONResponse({"error": "invalid_config", "details": errors}, status_code=400)

    info = _runtime_info()
    req_errors = _check_audio_test_requirements(info)
    if req_errors:
        return JSONResponse({"error": "missing_requirements", "details": req_errors}, status_code=400)

    orig_name = Path(audio.filename or "sample_audio").name
    try:
        with tempfile.TemporaryDirectory(prefix="wcp-audio-test-") as td:
            sample_path = Path(td) / orig_name
            await _save_upload_file(audio, sample_path)
            text, elapsed = _transcribe_audio_sample(sample_path, model=model, force_cpu=force_cpu)
    except Exception as e:
        return JSONResponse({"error": "transcription_failed", "details": str(e)}, status_code=500)

    return JSONResponse(
        {
            "model": model,
            "audio_file": orig_name,
            "force_cpu": force_cpu,
            "elapsed_seconds": round(elapsed, 3),
            "text": text,
        }
    )


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        state = _load_job_state(job_id)
        if not state:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        state = _reconcile_loaded_state(job_id, state)
        return JSONResponse(_job_public_state(state))
    if job.status == "running" and job.process and job.process.poll() is not None:
        code = job.process.poll()
        job.exit_code = int(code) if code is not None else 1
        job.status = "done" if job.exit_code == 0 else "error"
        if not job.finished_at:
            job.finished_at = _now_iso()
        _save_job_state(job)
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
        state = _reconcile_loaded_state(job_id, state)
        if state.get("status") != "running":
            return JSONResponse({"status": state.get("status"), "error": "job_not_running"}, status_code=409)
        pid = state.get("pid")
        if not _request_stop_pid(pid):
            _force_stop_pid(pid)
        state["status"] = "stopped"
        state["finished_at"] = _now_iso()
        state["updated_at"] = _now_iso()
        if state.get("exit_code") is None:
            state["exit_code"] = 130
        _persist_loaded_state(job_id, state)
        out_dir_raw = state.get("out_dir")
        if isinstance(out_dir_raw, str) and out_dir_raw:
            _append_stop_manifest_if_open(Path(out_dir_raw), reason="stopped_via_api")
        return JSONResponse({"status": state["status"]})

    if job.process and job.process.poll() is None:
        try:
            pid = job.process.pid
            if not _request_stop_pid(pid):
                _force_stop_pid(pid)
            job.status = "stopped"
            job.finished_at = _now_iso()
            if job.exit_code is None:
                job.exit_code = 130
            _save_job_state(job)
            _append_stop_manifest_if_open(job.out_dir, reason="stopped_via_api")
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"status": job.status})


def main() -> None:
    import uvicorn

    uvicorn.run("wcp.ui_app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
