from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .media import convert_to_wav
from .media import ocr_image
from .parser import find_chat_txt, iter_messages
from .util import ffprobe_duration_seconds


LogFn = Callable[[str], None]


@dataclass
class BenchmarkRequest:
    folder: Path
    out_dir: Path
    audio_samples: int
    image_samples: int
    models: list[str]
    backend: str
    lang: str
    include_ocr: bool


def _pick_evenly(items: list[str], count: int) -> list[str]:
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return items[:]
    step = len(items) / float(count)
    picked = []
    for i in range(count):
        idx = int(i * step)
        picked.append(items[min(idx, len(items) - 1)])
    return picked


def sample_media(folder: Path, audio_n: int, image_n: int, tz_offset: str = "+00:00") -> tuple[list[str], list[str]]:
    chat_txt = find_chat_txt(folder)
    messages = list(iter_messages(chat_txt, tz_offset=tz_offset))
    audio_files = sorted({m.file for msg in messages for m in msg.media if m.kind == "audio"})
    image_files = sorted({m.file for msg in messages for m in msg.media if m.kind == "image"})
    return _pick_evenly(audio_files, audio_n), _pick_evenly(image_files, image_n)


def _load_openai_model(model_name: str, device: str):
    import whisper  # type: ignore
    return whisper.load_model(model_name, device=device)


def _openai_transcribe(model, wav_path: Path, lang: str) -> tuple[str, Optional[float]]:
    language = None if lang == "auto" else lang
    result = model.transcribe(str(wav_path), language=language)
    text = (result.get("text") or "").strip()
    segments = result.get("segments") or []
    logprobs = [seg.get("avg_logprob") for seg in segments if isinstance(seg, dict) and seg.get("avg_logprob") is not None]
    avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
    return text, avg_logprob


def _load_faster_model(model_name: str, device: str):
    from faster_whisper import WhisperModel  # type: ignore
    if device == "cpu":
        return WhisperModel(model_name, device=device, compute_type="int8")
    try:
        return WhisperModel(model_name, device=device, compute_type="float16")
    except Exception:
        return WhisperModel(model_name, device=device, compute_type="int8")


def _faster_transcribe(model, wav_path: Path, lang: str) -> tuple[str, Optional[float]]:
    language = None if lang == "auto" else lang
    segments, _info = model.transcribe(str(wav_path), language=language)
    texts = []
    logprobs: list[float] = []
    for seg in segments:
        txt = getattr(seg, "text", "")
        if txt:
            texts.append(txt.strip())
        lp = getattr(seg, "avg_logprob", None)
        if lp is not None:
            logprobs.append(float(lp))
    avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
    return " ".join(texts).strip(), avg_logprob


def _benchmark_transcription(
    folder: Path,
    out_dir: Path,
    audio_files: list[str],
    backend: str,
    model_name: str,
    lang: str,
    device: str,
    log: LogFn,
    stop_flag,
) -> dict:
    wav_dir = out_dir / "benchmark" / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    total_elapsed = 0.0
    total_duration = 0.0
    logprobs: list[float] = []
    errors = 0

    if backend == "openai":
        model = _load_openai_model(model_name, device)
        transcribe = lambda wav: _openai_transcribe(model, wav, lang)
    else:
        model = _load_faster_model(model_name, device)
        transcribe = lambda wav: _faster_transcribe(model, wav, lang)

    for idx, file_name in enumerate(audio_files, 1):
        if stop_flag.is_set():
            break
        src = folder / file_name
        if not src.exists():
            log(f"[skip] missing audio: {file_name}")
            errors += 1
            continue
        wav_path = wav_dir / (Path(file_name).stem + ".wav")
        if not wav_path.exists():
            try:
                convert_to_wav(src, wav_path)
            except Exception as e:
                log(f"[error] wav convert failed: {file_name} ({e})")
                errors += 1
                continue

        duration = ffprobe_duration_seconds(wav_path) or 0.0
        t0 = time.time()
        try:
            _text, avg_logprob = transcribe(wav_path)
        except Exception as e:
            log(f"[error] transcribe failed: {file_name} ({e})")
            errors += 1
            continue
        elapsed = time.time() - t0
        total_elapsed += elapsed
        total_duration += duration
        if avg_logprob is not None:
            logprobs.append(avg_logprob)
        log(f"[ok] {idx}/{len(audio_files)} {file_name} in {elapsed:.2f}s (dur {duration:.1f}s)")

    avg_elapsed = total_elapsed / len(audio_files) if audio_files else 0.0
    avg_rtf = (total_duration / total_elapsed) if total_elapsed > 0 else 0.0
    avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
    avg_sample_duration = (total_duration / len(audio_files)) if audio_files else 0.0

    return {
        "backend": backend,
        "model": model_name,
        "device": device,
        "samples": len(audio_files),
        "errors": errors,
        "avg_sample_duration_seconds": round(avg_sample_duration, 3),
        "avg_seconds_per_sample": round(avg_elapsed, 3),
        "avg_realtime_factor": round(avg_rtf, 3),
        "avg_logprob": round(avg_logprob, 4) if avg_logprob is not None else None,
    }


def _benchmark_ocr(
    folder: Path,
    image_files: list[str],
    lang: str,
    log: LogFn,
    stop_flag,
) -> dict:
    ocr_lang = _ocr_lang_for(lang)
    total_elapsed = 0.0
    errors = 0
    total_chars = 0

    for idx, file_name in enumerate(image_files, 1):
        if stop_flag.is_set():
            break
        src = folder / file_name
        if not src.exists():
            log(f"[skip] missing image: {file_name}")
            errors += 1
            continue
        t0 = time.time()
        try:
            text = ocr_image(src, lang=ocr_lang)
            total_chars += len(text or "")
        except Exception as e:
            log(f"[error] ocr failed: {file_name} ({e})")
            errors += 1
            continue
        elapsed = time.time() - t0
        total_elapsed += elapsed
        log(f"[ok] {idx}/{len(image_files)} {file_name} in {elapsed:.2f}s")

    avg_elapsed = total_elapsed / len(image_files) if image_files else 0.0
    avg_chars = total_chars / len(image_files) if image_files else 0.0

    return {
        "samples": len(image_files),
        "errors": errors,
        "avg_seconds_per_sample": round(avg_elapsed, 3),
        "avg_chars": round(avg_chars, 1),
        "lang": ocr_lang,
    }


def _ocr_lang_for(lang: str) -> str:
    mapping = {
        "auto": "por+eng",
        "pt": "por",
        "en": "eng",
        "es": "spa",
        "fr": "fra",
    }
    return mapping.get(lang, lang)


def run_benchmark(
    req: BenchmarkRequest,
    log: LogFn,
    stop_flag,
) -> dict:
    cuda_ok = False
    try:
        import torch  # type: ignore
        cuda_ok = bool(torch.cuda.is_available())
    except Exception:
        cuda_ok = False

    devices = ["cpu"] + (["cuda"] if cuda_ok else [])

    chat_txt = find_chat_txt(req.folder)
    messages = list(iter_messages(chat_txt, tz_offset="+00:00"))
    all_audio = sorted({m.file for msg in messages for m in msg.media if m.kind == "audio"})
    all_images = sorted({m.file for msg in messages for m in msg.media if m.kind == "image"})

    audio_files = _pick_evenly(all_audio, req.audio_samples)
    image_files = _pick_evenly(all_images, req.image_samples)
    log(f"Sampled {len(audio_files)} audio (of {len(all_audio)}), {len(image_files)} image (of {len(all_images)}).")

    results = []

    backends = [req.backend]
    if req.backend == "auto":
        backends = ["openai", "faster"]

    for backend in backends:
        for model in req.models:
            for device in devices:
                if stop_flag.is_set():
                    break
                log(f"Benchmarking {backend}:{model} on {device}...")
                try:
                    results.append(
                        _benchmark_transcription(
                            req.folder,
                            req.out_dir,
                            audio_files,
                            backend,
                            model,
                            req.lang,
                            device,
                            log,
                            stop_flag,
                        )
                    )
                except Exception as e:
                    log(f"[error] benchmark failed for {backend}:{model} on {device} ({e})")
            if stop_flag.is_set():
                break

    ocr_result = None
    if req.include_ocr and image_files:
        log("Benchmarking OCR...")
        try:
            ocr_result = _benchmark_ocr(req.folder, image_files, req.lang, log, stop_flag)
        except Exception as e:
            log(f"[error] OCR benchmark failed ({e})")

    # Use sampled audio durations to estimate total audio duration quickly.
    sample_avg_dur = None
    for r in results:
        if r.get("avg_sample_duration_seconds"):
            sample_avg_dur = r["avg_sample_duration_seconds"]
            break
    if sample_avg_dur is None:
        sample_avg_dur = 0.0

    est_total_audio_duration = float(sample_avg_dur) * float(len(all_audio))
    est_total_ocr_seconds = None
    if ocr_result and ocr_result.get("avg_seconds_per_sample") is not None:
        est_total_ocr_seconds = float(ocr_result["avg_seconds_per_sample"]) * float(len(all_images))

    # Attach estimates per config.
    for r in results:
        rtf = r.get("avg_realtime_factor") or 0.0
        est_audio_seconds = (est_total_audio_duration / float(rtf)) if rtf > 0 else None
        r["estimated_total_audio_seconds"] = round(est_audio_seconds, 1) if est_audio_seconds is not None else None
        if est_total_ocr_seconds is not None:
            r["estimated_total_ocr_seconds"] = round(est_total_ocr_seconds, 1)
            if est_audio_seconds is not None:
                r["estimated_total_audio_plus_ocr_seconds"] = round(est_audio_seconds + est_total_ocr_seconds, 1)
        else:
            r["estimated_total_ocr_seconds"] = None
            r["estimated_total_audio_plus_ocr_seconds"] = None

    recommendations = {}
    valid = [r for r in results if r.get("avg_seconds_per_sample")]
    if valid:
        fastest = min(valid, key=lambda r: r["avg_seconds_per_sample"])
        recommendations["fastest"] = fastest

        with_quality = [r for r in valid if r.get("avg_logprob") is not None]
        if with_quality:
            best_quality = max(with_quality, key=lambda r: r["avg_logprob"])
            recommendations["highest_quality"] = best_quality

            balanced = max(with_quality, key=lambda r: (r["avg_logprob"] / max(r["avg_seconds_per_sample"], 0.001)))
            recommendations["balanced"] = balanced

    return {
        "summary": {
            "audio_samples": len(audio_files),
            "image_samples": len(image_files),
            "total_audio_files": len(all_audio),
            "total_image_files": len(all_images),
            "backend_choice": req.backend,
            "models": req.models,
            "devices_tested": devices,
            "estimated_total_audio_duration_seconds": round(est_total_audio_duration, 1),
            "estimated_total_ocr_seconds": round(est_total_ocr_seconds, 1) if est_total_ocr_seconds is not None else None,
        },
        "results": results,
        "ocr": ocr_result,
        "recommendations": recommendations,
    }
