Param(
  [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

Write-Host "WhatsApp export pipeline installer (Windows, non-interactive)"

function Test-Command($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}

function Add-ToUserPath($dir) {
  $dir = $dir.TrimEnd('\')
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($current -and ($current.Split(';') -contains $dir)) { return }
  $newPath = if ($current) { "$current;$dir" } else { $dir }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Refresh-Path
}

function Try-FixPathForExe($exeName, $candidateDirs) {
  foreach ($d in $candidateDirs) {
    if (Test-Path (Join-Path $d $exeName)) {
      Add-ToUserPath $d
      return $true
    }
  }
  return $false
}

if (-not (Test-Command "python")) {
  throw "Python not found. Install Python 3.10+ and ensure it's on PATH."
}

if (-not (Test-Command "winget")) {
  throw "winget not found. Install App Installer from Microsoft Store and retry."
}

if (-not (Test-Command "ffmpeg")) {
  Write-Host "ffmpeg not found. Installing with winget..."
  winget install --id Gyan.FFmpeg -e
  Refresh-Path
  [void](Try-FixPathForExe "ffmpeg.exe" @(
    "C:\Program Files\FFmpeg\bin",
    "C:\Program Files\ffmpeg\bin",
    "C:\ffmpeg\bin"
  ))
}
if (-not (Test-Command "ffmpeg")) {
  throw "ffmpeg is still not available. Run: winget install --id Gyan.FFmpeg -e"
}

if (-not (Test-Command "tesseract")) {
  Write-Host "tesseract not found. Installing with winget..."
  winget install --id UB-Mannheim.TesseractOCR -e
  Refresh-Path
  [void](Try-FixPathForExe "tesseract.exe" @(
    "C:\Program Files\Tesseract-OCR",
    "C:\Program Files (x86)\Tesseract-OCR"
  ))
}
if (-not (Test-Command "tesseract")) {
  throw "tesseract is still not available. Run: winget install --id UB-Mannheim.TesseractOCR -e"
}

if (-not (Test-Path $VenvDir)) {
  python -m venv $VenvDir
}

& "$VenvDir\Scripts\python.exe" -m pip install -U pip wheel
& "$VenvDir\Scripts\python.exe" -m pip install -r requirements.txt

$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($hasNvidia) {
  & "$VenvDir\Scripts\python.exe" -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
} else {
  & "$VenvDir\Scripts\python.exe" -m pip install -U torch torchvision torchaudio
}

& "$VenvDir\Scripts\python.exe" -m pip install -U transformers pytesseract pillow

Write-Host "Verifying dependencies..."
& "$VenvDir\Scripts\python.exe" -c "import torch, transformers, pytesseract, PIL; print('torch', torch.__version__); print('transformers', transformers.__version__); print('cuda_available', torch.cuda.is_available())"
& ffmpeg -version | Select-Object -First 1
& tesseract --version | Select-Object -First 1

Write-Host ""
Write-Host "Installed."
Write-Host "Run (UI):"
Write-Host "  $VenvDir\Scripts\Activate.ps1"
Write-Host "  python -m wcp.ui_app"
