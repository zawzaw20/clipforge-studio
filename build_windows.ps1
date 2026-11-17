$ErrorActionPreference = "Stop"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) { throw "Run setup_windows.ps1 first." }
& .\.venv\Scripts\python.exe -m pip install pyinstaller
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onedir --windowed --name "ClipForge Studio" desktop_app.py
New-Item -ItemType Directory -Force -Path ".\dist\ClipForge Studio\tools" | Out-Null
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
Copy-Item $ffmpeg ".\dist\ClipForge Studio\tools\ffmpeg.exe" -Force
Copy-Item $ffprobe ".\dist\ClipForge Studio\tools\ffprobe.exe" -Force
Write-Host "Desktop app packaged to dist\ClipForge Studio."
Write-Host "WhisperX/PyTorch model caches and GPU runtime remain installed in the Python environment; use the venv launcher for full AI support."
