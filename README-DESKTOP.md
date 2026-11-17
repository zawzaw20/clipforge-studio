# ClipForge Studio — Windows 11 Desktop

This branch adds a local Windows desktop client. It does not need Railway, S3, or a cloud server for editing.

## Requirements
- Windows 11 64-bit
- Python 3.11
- NVIDIA GPU with current driver (optional for CUDA transcription/NVENC; CPU fallback is available)
- FFmpeg and ffprobe on PATH

## Install
1. Install Python 3.11 and current NVIDIA drivers.
2. Install FFmpeg: \`winget install Gyan.FFmpeg\`.
3. Open PowerShell in this repository. If required: \`Set-ExecutionPolicy -Scope Process Bypass\`.
4. Run \`.\setup_windows.ps1\`.
5. Launch \`.\.venv\Scripts\python.exe desktop_app.py\`.

WhisperX and CUDA PyTorch are large downloads. The script installs CUDA 12.6 PyTorch wheels. If incompatible with your driver, use the selector at https://pytorch.org/get-started/locally/. Run WhisperX once while online to cache the speech model before offline use.

## Dropbox — read-only
1. Create an app at https://www.dropbox.com/developers/apps.
2. Select scoped access and enable only \`account_info.read\`, \`files.metadata.read\`, and \`files.content.read\`.
3. Add redirect URI exactly: \`http://localhost:53682/oauth/callback\`.
4. Enter the Dropbox App key in ClipForge, click Connect Dropbox, and authorize in your browser.
5. Browse Dropbox folders and import selected videos.

OAuth uses authorization-code + PKCE and does not embed an app secret. Refresh tokens are stored in Windows Credential Manager via Python keyring. ClipForge only lists/downloads videos; it never uploads, renames, deletes, or modifies Dropbox originals. Dropbox access requires internet; editing imported files is local.

## Local editing
Choose a video, output folder, and options, then Create Clips. WhisperX transcription is optional, PySceneDetect and Auto-Editor are optional analysis tools, and FFmpeg renders MP4 clips. NVIDIA NVENC is used when present; CPU x264 fallback is available.

## Current scope
The initial desktop build creates first-pass 30–65 second windows from transcript segments or fixed intervals, not a trained semantic highlight-ranking model. It exports up to 30 clips per run and currently processes one selected video at a time. Review clips before publishing.
