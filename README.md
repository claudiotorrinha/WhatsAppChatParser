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

> The installer can optionally install Whisper (for transcription) and OCR dependencies.

## What you need
- Python 3.10+
- ffmpeg (for audio conversion/transcription)
- Tesseract OCR (for image OCR)

The installers can guide you through installing these.

## Using the UI
1. Upload the WhatsApp export .zip.
2. Keep defaults unless you know you need changes.
3. Click Run pipeline.
4. Outputs appear in the out/ folder by default.

### Basics vs Advanced
- Basics shows the settings most people need (output folder, Whisper model, language, OCR).
- Advanced contains parsing overrides, performance tuning, benchmarking, and power-user controls.

### Benchmark
Use the Benchmark section (Advanced) to compare speed vs quality on a small sample of your export.
It also shows a rough estimate of total processing time (sample-based).

## Outputs
- out/conversation.jsonl - canonical dataset (one JSON object per message)
- out/transcript.md - human-readable transcript
- out/by-month/ - monthly split JSONL + MD
- out/transcripts/ - per-voice-note transcripts
- out/ocr/ - OCR text per image
- out/converted/ - converted audio files
- out/manifest.jsonl - append-only processing log
- out/report.md - summary report

## Optional CLI (advanced)
If you prefer CLI:
```bash
python whatsapp_export_to_jsonl.py --tz +00:00
```

## Troubleshooting
### Transcription not working
- Ensure Whisper is installed in the venv:
  ```powershell
  .venv\Scripts\Activate.ps1
  python -c "import whisper; print('whisper ok')"
  ```
- If using `large-v3-turbo`, install the HF backend and select `HF Transformers` (or `Auto`) in the UI:
  ```powershell
  python -c "import transformers; print('transformers ok')"
  ```
- Make sure Disable transcription is unchecked in the UI.

### Faster Whisper fails downloading models on Windows (WinError 1314)
This can happen when Windows blocks symlink creation in the Hugging Face cache.
Fix it by either enabling Windows Developer Mode (recommended) or running PowerShell as Administrator.

### OCR not working
- Ensure tesseract is installed and on PATH.
- Verify:
  ```powershell
  tesseract --version
  ```

### Dates look wrong
Use Parse format and Date order overrides in the UI.

### Rerun from scratch
Enable Disable resume in the UI to force reprocessing.
