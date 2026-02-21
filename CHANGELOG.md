# Changelog

## v1.1.0

### Highlights
- Added HF Transformers transcription backend (`--transcribe-backend hf`), including support for `openai/whisper-large-v3-turbo` (UI option: `large-v3-turbo`).
- Added **Model Quick Test** in the UI to transcribe a single audio sample with the selected model and report elapsed time.

## v1.0.0

First release of WhatsApp Export Studio.

### Highlights
- Local web UI (Material 3) for running the pipeline without CLI flags.
- Resume-aware processing (append-only mindset) with per-run manifest and report.
- Audio transcription via OpenAI Whisper (GPU if available) or Faster Whisper (CPU).
- Image OCR via Tesseract (optional).
- Benchmark mode to compare speed vs quality and estimate total run time (sample-based).
- Improved reliability: ffmpeg temp output handling, stop/abort support, and log health trimming.

### Notes
- On small GPUs (e.g. ~2GB VRAM), large Whisper models on CUDA can be slower or fail (OOM/cuDNN). CPU or smaller models may be faster.
- Faster Whisper model downloads on Windows may require Developer Mode or Administrator privileges if the model is not already cached.
