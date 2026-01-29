# WhatsApp Android export → JSONL (with audio + image processing)

## Quick install (recommended)
First, go into the app folder:
```bash
cd WhatsAppChatProcessor
```

### Linux/macOS
```bash
chmod +x install.sh run.sh
./install.sh
```
Then run (defaults are: convert audio + transcribe + OCR, with progress):
```bash
./run.sh --tz +00:00
```

### Performance knobs
- Separate worker pools:
  - `--audio-workers 2` (transcription/conversion)
  - `--ocr-workers 2` (OCR)
- Quality-first transcription backend is default: `--transcribe-backend openai`
- Only do one stage:
  - `--only-transcribe`
  - `--only-ocr`

You can tune it:
- `--progress-every 25` (more frequent updates)
- `--quiet` (no progress output)
- `--no-transcribe` (skip audio transcripts)
- `--no-ocr` (skip image OCR)
- `--convert-audio none` (skip audio conversion)
- **Resume is ON by default**: it will skip work if `out/converted`, `out/transcripts`, `out/ocr` already contain results.
  - Use `--no-resume` to force reprocessing.
- A **manifest log** is written by default: `out/manifest.jsonl` (append-only).
  - Use `--no-manifest` to disable.
- A **readable Markdown transcript** is written by default: `out/transcript.md`
  - Use `--no-md` to disable.
- OCR speed controls:
  - `--ocr-max 200` (do at most 200 NEW OCRs per run)
  - `--ocr-mode likely-text` (only OCR images that look text-heavy)

## Add-ons for analysis / AI ingestion
- By-month split outputs are **ON by default**:
  - `out/by-month/YYYY-MM.jsonl`
  - `out/by-month/YYYY-MM.md`
  - disable with `--no-by-month`

- A quality report is written by default: `out/report.md`
  - disable with `--no-report`

- Sender normalization (optional):
  - `--me "ME_NAME" --them "OTHER_NAME"`
  - output will include `sender_id` (ME/THEM)

### Windows (PowerShell)
```powershell
cd WhatsAppChatProcessor
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```
Then run:
```bat
run.bat --tz +00:00
```

---

## 0) Put files in one folder (or use the zip directly)
### Option A: folder
Example:
```
export/
  WhatsApp Chat with Person.txt
  IMG-20250920-WA0000.jpg
  PTT-20250920-WA0001.opus
  PTT-20250920-WA0002.opus
  ...
```

### Option B: zip
If WhatsApp gives you a **.zip** export, you can pass the zip path directly:
```bash
python whatsapp_export_to_jsonl.py "/path/to/whatsapp-export.zip" --tz +00:00
```
The app will extract it into: `out/_extracted/<zipname>/` and process it from there.

## 1) Install prerequisites (Windows/macOS/Linux)
### ffmpeg (required for audio conversion/transcription)
- Windows: `winget install --id Gyan.FFmpeg -e`
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt-get update && sudo apt-get install -y ffmpeg`

### Tesseract (required for OCR)
- Windows: `winget install --id UB-Mannheim.TesseractOCR -e`
  - If `tesseract --version` still fails after install, add to PATH (common):
    - `C:\Program Files\Tesseract-OCR\`
- Ubuntu/Debian: `sudo apt-get install -y tesseract-ocr tesseract-ocr-por`
- macOS: `brew install tesseract` (language packs may vary)

### GPU / CUDA (optional, for faster transcription)
- You do **not** need an OpenAI subscription.
- You also typically do **not** need to install the full CUDA Toolkit.
- If you have an **NVIDIA GPU + drivers** (i.e. `nvidia-smi` works), the installer can install **CUDA-enabled PyTorch wheels** which include the runtime.
- If you do *not* have NVIDIA drivers installed, install those first from NVIDIA.

### Python deps
Create a venv (recommended):
```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\\Scripts\\Activate.ps1

pip install -U pip
```

## How it works (pipeline)
The app runs in two phases:
1) **Media preprocessing (resume-aware)**
   - converts audio (mp3/wav)
   - transcribes voice notes to `out/transcripts/*.txt`
   - OCRs images to `out/ocr/*.txt`
2) **Output generation (fast)**
   - writes `out/conversation.jsonl`
   - writes `out/transcript.md`
   - writes `out/by-month/YYYY-MM.{jsonl,md}`
   - writes `out/report.md`

The key idea: you can interrupt and rerun—already-produced files are reused.

## Outputs (what each file is for)
- `out/conversation.jsonl` — canonical dataset (one JSON object per message)
- `out/transcript.md` — human-readable transcript for easy AI ingestion
- `out/by-month/` — month-split add-on exports
- `out/transcripts/` — one transcript per voice note
- `out/ocr/` — one OCR text file per image
- `out/converted/` — converted audio (mp3/wav)
- `out/manifest.jsonl` — append-only processing log (audit + resume debugging)
- `out/report.md` — run summary + counts + detected participants

## Run examples
### Default (recommended)
```bash
python whatsapp_export_to_jsonl.py --tz +00:00
```

### Voice-notes only (skip OCR)
```bash
python whatsapp_export_to_jsonl.py --no-ocr --audio-workers 1
```

### OCR only (later)
```bash
python whatsapp_export_to_jsonl.py --only-ocr --ocr-mode likely-text --ocr-workers 2
```

### Transcription only (later)
```bash
python whatsapp_export_to_jsonl.py --only-transcribe --audio-workers 1
```

### Map speakers (keeps real names, adds sender_id=ME/THEM)
```bash
python whatsapp_export_to_jsonl.py --me "ME_NAME" --them "OTHER_NAME"
```

## Transcription note (no OpenAI subscription needed)
Transcription is **local**. The default backend uses the open-source `openai-whisper` Python package.
That name is confusing: it does **not** require an OpenAI API key or any subscription.

## Troubleshooting
### Parsing looks wrong (dates swapped / no messages)
- Auto-detect supports PT Android, generic Android, and iOS bracketed exports.
- Force format:
  - `--format pt|android|ios`
- Force date order (android/ios only):
  - `--date-order dmy|mdy`

### OCR is extremely slow
- Prefer the text-only heuristic:
  - `--ocr-mode likely-text`
- Or cap work per run:
  - `--ocr-max 200`
- Or skip OCR entirely:
  - `--no-ocr`

### Transcription is extremely slow
- Use fewer audio workers (Whisper is CPU-heavy):
  - `--audio-workers 1`
- If you have an NVIDIA GPU and drivers (`nvidia-smi` works), reinstall with CUDA-enabled torch and rerun.

### `tesseract` not found (Windows)
- Ensure it’s on PATH. Common folder:
  - `C:\Program Files\Tesseract-OCR\`
- Verify:
  - `tesseract --version`

### `ffmpeg` not found
- Verify:
  - `ffmpeg -version`
- Windows quick install:
  - `winget install --id Gyan.FFmpeg -e`

### Resume / reruns
- Resume is file-based: if `out/transcripts/*.txt` / `out/ocr/*.txt` / `out/converted/*` exist, reruns will skip them.
- If you want to force reprocessing:
  - `--no-resume`

### I interrupted a run and now outputs look corrupted
- Delete the corresponding output file(s) (e.g. one broken transcript `.txt`) and rerun.
- This app writes outputs atomically (`*.tmp` then rename) to reduce the chance of corruption.
