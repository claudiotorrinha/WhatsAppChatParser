#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./install.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python whatsapp_export_to_jsonl.py "$@"
