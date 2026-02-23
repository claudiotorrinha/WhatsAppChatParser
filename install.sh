#!/usr/bin/env bash
set -euo pipefail

# Non-interactive installer for Linux/macOS.
# Installs Python deps and validates required system dependencies.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN not found. Install Python 3.10+ and retry." >&2
  exit 1
fi

install_with_apt() {
  local pkg="$1"
  if [[ "$(id -u)" -eq 0 ]]; then
    apt-get update
    apt-get install -y "$pkg"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n apt-get update
    sudo -n apt-get install -y "$pkg"
    return 0
  fi
  return 1
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    return
  fi
  echo "ffmpeg not found. Attempting installation..." >&2
  if command -v apt-get >/dev/null 2>&1; then
    if install_with_apt ffmpeg; then
      return
    fi
    echo "ERROR: cannot auto-install ffmpeg without sudo privileges." >&2
    echo "Run: sudo apt-get update && sudo apt-get install -y ffmpeg" >&2
    exit 1
  elif command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
    return
  fi
  echo "ERROR: ffmpeg missing. Install it with your package manager and rerun." >&2
  exit 1
}

ensure_tesseract() {
  if command -v tesseract >/dev/null 2>&1; then
    return
  fi
  echo "tesseract not found. Attempting installation..." >&2
  if command -v apt-get >/dev/null 2>&1; then
    if install_with_apt tesseract-ocr; then
      return
    fi
    echo "ERROR: cannot auto-install tesseract without sudo privileges." >&2
    echo "Run: sudo apt-get update && sudo apt-get install -y tesseract-ocr" >&2
    exit 1
  elif command -v brew >/dev/null 2>&1; then
    brew install tesseract
    return
  fi
  echo "ERROR: tesseract missing. Install it with your package manager and rerun." >&2
  exit 1
}

ensure_ffmpeg
ensure_tesseract

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install -U pip wheel
pip install -r requirements.txt

# Install torch first so we can choose CUDA vs CPU wheels.
if command -v nvidia-smi >/dev/null 2>&1; then
  pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
else
  pip install -U torch torchvision torchaudio
fi

# Required runtime deps for plug-and-play execution.
pip install -U transformers pytesseract pillow

echo "Verifying dependencies..." >&2
python -c "import torch, transformers, pytesseract, PIL; print('torch', torch.__version__); print('transformers', transformers.__version__); print('cuda_available', torch.cuda.is_available())"
ffmpeg -version >/dev/null
tesseract --version >/dev/null

cat <<'EOF'

Installed.

Run (UI):
  source .venv/bin/activate
  python -m wcp.ui_app
EOF
