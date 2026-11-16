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

## Dropbox connection

ClipForge supports linking one Dropbox account through OAuth 2.0 with read-only scopes:
`account_info.read`, `files.metadata.read`, and `files.content.read`. Refresh tokens are encrypted at rest on the persistent volume using a key derived from the private `CLIPFORGE_API_KEY`. Dropbox originals are never modified.

Set these Railway variables on `clipforge-api`:
- `DROPBOX_APP_KEY`: Dropbox developer app key.
- `DROPBOX_APP_SECRET`: Dropbox developer app secret.
- `DROPBOX_REDIRECT_URI`: `https://YOUR-RAILWAY-DOMAIN/dropbox/callback` (must exactly match the OAuth redirect URI configured in the Dropbox developer console).
- Optional `DROPBOX_MAX_VIDEO_BYTES`: maximum imported file size in bytes (default 8 GiB).

REST flow:
1. Call `GET /dropbox/connect` with `X-API-Key` and open the returned `authorization_url`.
2. Approve access on Dropbox. Dropbox redirects to `/dropbox/callback`.
3. Call `GET /dropbox/videos?path=&recursive=false` with `X-API-Key` to browse.
4. Call the MCP tool `start_dropbox_video_edit` with the selected Dropbox `path`, or use the REST import workflow once enabled.
5. Poll `get_video_edit_status` for the finished MP4.

MCP tools added: `dropbox_connection_status`, `get_dropbox_authorization_url`, `list_dropbox_videos`, and `start_dropbox_video_edit`. Disconnect using `POST /dropbox/disconnect` with `X-API-Key`.
