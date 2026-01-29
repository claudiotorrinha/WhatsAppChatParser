Param(
  [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

Write-Host "WhatsApp export pipeline installer (Windows)"

# Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  throw "Python not found. Install Python 3.10+ and ensure it's on PATH."
}

function Test-Command($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Ensure-Winget {
  if (-not (Test-Command "winget")) {
    Write-Warning "winget not found. Can't auto-install system dependencies."
    Write-Host "Install from Microsoft Store: 'App Installer' (winget) or install deps manually."
    return $false
  }
  return $true
}

function Add-ToUserPath($dir) {
  $dir = $dir.TrimEnd('\\')
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($current -and ($current.Split(';') -contains $dir)) {
    return
  }
  $newPath = if ($current) { "$current;$dir" } else { $dir }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  $env:Path = "$env:Path;$dir"
}

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}

function Try-FixPathForExe($exeName, $candidateDirs) {
  foreach ($d in $candidateDirs) {
    if (Test-Path (Join-Path $d $exeName)) {
      Write-Host "Found $exeName in: $d"
      Write-Host "Adding to USER PATH..."
      Add-ToUserPath $d
      return $true
    }
  }
  return $false
}

# ffmpeg (required for audio conversion/transcription)
if (-not (Test-Command "ffmpeg")) {
  Write-Warning "ffmpeg not found in PATH. Audio conversion/transcription will not work without it."
  if (Ensure-Winget) {
    $ans = Read-Host "Install ffmpeg via winget now? [y/N]"
    if ($ans -match '^[Yy]$') {
      winget install --id Gyan.FFmpeg -e
      Refresh-Path
    }
  }

  # Try common locations if install didn't update PATH
  [void](Try-FixPathForExe "ffmpeg.exe" @(
    "C:\\Program Files\\FFmpeg\\bin",
    "C:\\Program Files\\ffmpeg\\bin",
    "C:\\ffmpeg\\bin"
  ))

  if (-not (Test-Command "ffmpeg")) {
    Write-Warning "ffmpeg still not found in PATH. Do one of:"
    Write-Host "  1) Install via winget: winget install --id Gyan.FFmpeg -e"
    Write-Host "  2) Add its bin folder to PATH (common: C:\\Program Files\\FFmpeg\\bin)"
    Write-Host "Then open a NEW terminal and run: ffmpeg -version"
  } else {
    Write-Host "ffmpeg OK: " -NoNewline
    & ffmpeg -version | Select-Object -First 1
  }
}

if (-not (Test-Path $VenvDir)) {
  python -m venv $VenvDir
}

& "$VenvDir\Scripts\python.exe" -m pip install -U pip wheel

$installWhisper = Read-Host "Install local Whisper transcription (openai-whisper)? [Y/n]"
if (-not ($installWhisper -match '^[Nn]$')) {
  # Install PyTorch first so we can choose CUDA vs CPU.
  $hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)

  if ($hasNvidia) {
    Write-Host "NVIDIA GPU detected (nvidia-smi found)."
    $useCuda = Read-Host "Install CUDA-enabled PyTorch wheels? (no CUDA toolkit needed) [Y/n]"
    if (-not ($useCuda -match '^[Nn]$')) {
      & "$VenvDir\Scripts\python.exe" -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    } else {
      & "$VenvDir\Scripts\python.exe" -m pip install -U torch torchvision torchaudio
    }
  } else {
    & "$VenvDir\Scripts\python.exe" -m pip install -U torch torchvision torchaudio
  }

  & "$VenvDir\Scripts\python.exe" -m pip install -U openai-whisper

  Write-Host "Verifying Whisper + torch..."
  $code = @'
import torch
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
'@
  & "$VenvDir\Scripts\python.exe" -c $code
}

$installOcr = Read-Host "Install image OCR support (pytesseract + pillow)? [Y/n]"
if (-not ($installOcr -match '^[Nn]$')) {
  & "$VenvDir\Scripts\python.exe" -m pip install -U pytesseract pillow

  function Try-FixTesseractPath {
    return (Try-FixPathForExe "tesseract.exe" @(
      "C:\\Program Files\\Tesseract-OCR",
      "C:\\Program Files (x86)\\Tesseract-OCR"
    ))
  }

  # Tesseract (required for OCR)
  if (-not (Test-Command "tesseract")) {
    Write-Warning "tesseract not found in PATH. OCR will not work without it."
    if (Ensure-Winget) {
      $ans = Read-Host "Install Tesseract OCR via winget now? [y/N]"
      if ($ans -match '^[Yy]$') {
        winget install --id UB-Mannheim.TesseractOCR -e
        # winget/installer may not update this PowerShell session's PATH
        Refresh-Path
      }
    }

    # Try to add common install dir to PATH (covers many installers that don't update PATH)
    [void](Try-FixTesseractPath)
  }

  if (-not (Test-Command "tesseract")) {
    Write-Warning "tesseract still not found in PATH. Do one of:"
    Write-Host "  1) Install via winget: winget install --id UB-Mannheim.TesseractOCR -e"
    Write-Host "  2) Add its install folder to PATH (common: C:\\Program Files\\Tesseract-OCR)"
    Write-Host "Then open a NEW terminal and run: tesseract --version"
  } else {
    Write-Host "tesseract OK: " -NoNewline
    & tesseract --version | Select-Object -First 1
  }
}

Write-Host ""
Write-Host "Installed."
Write-Host "Run:"
Write-Host "  $VenvDir\Scripts\Activate.ps1"
Write-Host "  python whatsapp_export_to_jsonl.py --tz +00:00"
