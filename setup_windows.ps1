$ErrorActionPreference = "Stop"
py -3.11 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
# CUDA-enabled PyTorch for NVIDIA GPUs. CPU fallback remains available.
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r requirements-desktop.txt
python -m pip install "scenedetect[opencv]" auto-editor whisperx
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Install FFmpeg with: winget install Gyan.FFmpeg"
}
python -c "import torch; print('CUDA available:',torch.cuda.is_available()); print('GPU:',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback')"
Write-Host "Setup complete. Start with: python desktop_app.py"
