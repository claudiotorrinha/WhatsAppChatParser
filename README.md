# WhatsApp Export Studio

Turn WhatsApp exports into structured JSONL + Markdown with optional audio transcription and image OCR.
Runs locally on your machine.

## Quick start (UI - recommended)
### Windows (PowerShell)
```powershell
# In the repo folder (where install.ps1 lives)
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
.venv\Scripts\Activate.ps1
python -m wcp.ui_app
```
Open: http://127.0.0.1:8000

### macOS / Linux
```bash
# In the repo folder (where install.sh lives)
chmod +x install.sh
./install.sh
source .venv/bin/activate
python -m wcp.ui_app
```
Open: http://127.0.0.1:8000

> The installers are non-interactive and install all runtime dependencies.

## What you need
- Python 3.10+
- ffmpeg (for audio conversion/transcription)
- Tesseract OCR (for image OCR)

The installers will attempt to install missing system dependencies and fail fast with exact commands when they cannot.

## Using the UI
1. Upload the WhatsApp export .zip.
2. Optionally choose model (`medium` or `large-v3-turbo`).
3. Optionally use **Model Quick Test**: upload one audio sample, run the selected model, and compare transcript + elapsed time before starting a full run.
4. Optionally disable transcription and/or OCR, or force CPU for low-VRAM GPUs.
5. Click Run pipeline.
6. Outputs appear in the out/ folder by default.
7. Parser format, timezone, and language are auto-selected.

### Defaults
- UI is intentionally minimal for plug-and-play execution.
- Supported transcription models are `medium` and `large-v3-turbo` on the HF Transformers backend.
- Parser format, timezone, and language are automatic.
- `speed-preset=auto` is the default.

### Speed preset (`auto`)
- `auto` prioritizes throughput and selects:
  - `medium + cuda` when CUDA is available.
  - `medium + cpu` when CUDA is not available.
- `off` keeps your selected model/device behavior (manual mode).

## Outputs
- out/conversation.jsonl - canonical dataset (one JSON object per message)
- out/transcript.md - human-readable transcript
- out/by-month/ - monthly split JSONL + MD
- out/transcripts/ - per-voice-note transcripts
- out/ocr/ - OCR text per image
- out/converted/ - converted audio files
- out/manifest.jsonl - append-only processing log
- out/report.md - summary report

## Optional CLI
If you prefer CLI, pass a folder or zip and optionally set only core toggles:
```bash
python whatsapp_export_to_jsonl.py "<export-folder-or-zip>" \
  --out out \
  --whisper-model medium
```

Optional toggles: `--no-transcribe`, `--no-ocr`, `--quiet`, `--force-cpu`, `--speed-preset {auto,off}`.

Legacy advanced flags are currently accepted but ignored with a warning for compatibility.

## Progress and logs
- Progress now uses a single coherent denominator:
  - `X/Y done` includes pending transcription work inside the same `Y`.
  - When media workers are done but transcription/retry is still running, logs show `media_done=A/B` plus the active transcription item.
- Log labels:
  - Normal queue: `transcribe=...` / `transcribe_queue=...`.
  - Quality retry phase: `retry_transcribe=...` / `retry_queue=...`.

## Transcript quality validation and retry
- Each generated transcript is normalized and quality-checked.
- In resume mode, existing transcript files are also revalidated on each run (when transcription is enabled).
- Flagged transcripts are queued and retried once at the end of the run with anti-repetition decoding settings.
- A retry replaces the existing transcript only when quality improves (with guards against very short bad fallbacks).
- Manifest events include:
  - `audio_transcript_quality_flagged`
  - `audio_transcript_retried`
  - `audio_transcript_retry_failed`
  - `audio_transcript_quality_still_flagged`
- Report counters include retry/quality stats (`audio_transcript_quality_flagged`, `audio_transcript_retry_*`).

## CLI vs browser speed
- Pipeline speed is effectively the same between CLI and UI when using the same model/device/settings.
- UI overhead is minimal; most runtime is audio conversion/transcription and OCR.
- Practical speed differences usually come from runtime choices (`--force-cpu`, `--speed-preset`, selected model) or other machine load.

## Troubleshooting
### Transcription not working
- Ensure core Python dependencies are installed in the venv:
  ```powershell
  .venv\Scripts\Activate.ps1
  python -c "import torch, transformers; print('ok')"
  ```
- Make sure Disable transcription is unchecked in the UI.

### OCR not working
- Ensure tesseract is installed and on PATH.
- Verify:
  ```powershell
  tesseract --version
  ```

### Timezone
Timezone is now automatic (`auto`) using your local environment offset.

### Re-run behavior
The pipeline resumes by default and skips already processed artifacts where possible.
When transcription is enabled, existing transcripts are still quality-checked during resume runs and bad ones are retried at the end.
