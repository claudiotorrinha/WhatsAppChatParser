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

Optional toggles: `--no-transcribe`, `--no-ocr`, `--quiet`, `--force-cpu`.

Legacy advanced flags are currently accepted but ignored with a warning for compatibility.

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
