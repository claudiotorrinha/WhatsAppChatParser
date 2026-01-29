@echo off
setlocal

if not exist .venv\Scripts\python.exe (
  echo No .venv found. Run install.ps1 first.
  exit /b 1
)

.venv\Scripts\python.exe whatsapp_export_to_jsonl.py %*
