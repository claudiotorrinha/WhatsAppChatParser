#!/usr/bin/env bash
set -euo pipefail

# WhatsApp export pipeline installer (Linux/macOS)
# Creates a local virtualenv and installs Python deps.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN not found. Install Python 3.10+ and try again." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: ffmpeg not found. Audio conversion/transcription will not work without it." >&2
  if command -v apt-get >/dev/null 2>&1; then
    read -r -p "Install ffmpeg via apt-get now? (needs sudo) [y/N] " yn
    if [[ "$yn" =~ ^[Yy]$ ]]; then
      sudo apt-get update && sudo apt-get install -y ffmpeg
    fi
  elif command -v brew >/dev/null 2>&1; then
    read -r -p "Install ffmpeg via brew now? [y/N] " yn
    if [[ "$yn" =~ ^[Yy]$ ]]; then
      brew install ffmpeg
    fi
  else
    echo "Install ffmpeg via your package manager (brew/apt/etc.)." >&2
  fi

  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "WARNING: ffmpeg still not found. Continuing, but audio steps will fail." >&2
  fi
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install -U pip wheel

# Default behavior of the app is to do *all* enrichments.
# So we default these installs to YES (you can still opt out).
read -r -p "Install local Whisper transcription (openai-whisper)? [Y/n] " yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  # Install PyTorch first so we can choose CUDA vs CPU.
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU detected (nvidia-smi found)." >&2
    read -r -p "Install CUDA-enabled PyTorch wheels? (no CUDA toolkit needed) [Y/n] " yn3
    if [[ ! "$yn3" =~ ^[Nn]$ ]]; then
      # cu121 wheels generally work with modern NVIDIA drivers.
      pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    else
      pip install -U torch torchvision torchaudio
    fi
  else
    pip install -U torch torchvision torchaudio
  fi

  pip install -U openai-whisper

  echo "Verifying Whisper + torch..." >&2
  python - <<'PY'
import torch
print('torch', torch.__version__)
print('cuda_available', torch.cuda.is_available())
PY
fi

read -r -p "Install image OCR support (pytesseract + pillow)? [Y/n] " yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  pip install -U pytesseract pillow

  # Tesseract (system dependency)
  if ! command -v tesseract >/dev/null 2>&1; then
    echo "WARNING: tesseract not found. OCR will not work without it." >&2
    if command -v apt-get >/dev/null 2>&1; then
      read -r -p "Install tesseract-ocr + Portuguese language via apt-get now? (needs sudo) [y/N] " yn2
      if [[ "$yn2" =~ ^[Yy]$ ]]; then
        sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-por
      fi
    elif command -v brew >/dev/null 2>&1; then
      read -r -p "Install tesseract via brew now? [y/N] " yn2
      if [[ "$yn2" =~ ^[Yy]$ ]]; then
        brew install tesseract
      fi
      echo "Note: you may need to install Portuguese traineddata separately depending on your setup." >&2
    else
      echo "Install tesseract-ocr (and Portuguese language pack) via your OS package manager." >&2
    fi

    if ! command -v tesseract >/dev/null 2>&1; then
      echo "WARNING: tesseract still not found. Continuing, but OCR will fail." >&2
    fi
  fi
fi

echo
cat <<'EOF'
Installed.

Run:
  source .venv/bin/activate
  python whatsapp_export_to_jsonl.py --tz +00:00
EOF
