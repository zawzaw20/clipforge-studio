# ClipForge Studio

ChatGPT-connected cloud video editing service. The API exposes an MCP Streamable HTTP endpoint at `/mcp` and REST endpoints for upload, job submission, and status.

## Engines
- WhisperX: transcription and word-level timestamps (optional GPU/CPU install; enable in a GPU worker image).
- PySceneDetect: scene detection.
- Auto-Editor: silence/quiet-section editing.
- Remotion: timed animated caption composition.
- FFmpeg: final H.264/AAC MP4 export.

## Deploy on Railway
1. Create a Railway service from this repository, branch `main`.
2. Set `CLIPFORGE_API_KEY` to a long random secret.
3. Provision Railway object storage and map its S3-compatible credentials to `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION`.
4. Add a persistent volume at `/data` for job metadata and temporary files. For production, move job state to Postgres/Redis and run the processor as a separate worker.
5. Generate a Railway public domain targeting port 8000.
6. Verify `/health` and configure a ChatGPT custom MCP connector for `https://YOUR-DOMAIN/mcp` using HTTP Bearer authentication with the `CLIPFORGE_API_KEY` value stored in Railway. Do not commit this key to GitHub.

## Upload flow
Call `create_video_upload`, PUT the video bytes to the returned `upload_url` with the exact returned content type, then call `start_video_edit` with `source_key`. Poll `get_video_edit_status` for the signed export URL. MCP requests must include `Authorization: Bearer <CLIPFORGE_API_KEY>`; the MCP tools do not ask for the key as an argument.

## Important deployment notes
The API includes optional-engine detection. WhisperX is not installed in the lightweight base image because CUDA/PyTorch wheels are hardware-specific; install WhisperX in a GPU-enabled worker image before enabling production transcription. Jobs currently run in-process and job metadata is stored on the mounted volume. Do not use this single-process configuration for large-scale concurrent workloads; add a durable queue and separate worker service before production launch.
