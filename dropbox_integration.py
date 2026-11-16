import os, json, time, hmac, hashlib, base64, secrets, uuid, mimetypes
from pathlib import Path
from urllib.parse import urlencode
import requests
import boto3
from botocore.config import Config
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()
DATA = Path(os.getenv("CLIPFORGE_DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = DATA / "dropbox-token.enc"
STATE_FILE = DATA / "dropbox-oauth-state.json"
API_KEY = os.getenv("CLIPFORGE_API_KEY", "")
APP_KEY = os.getenv("DROPBOX_APP_KEY", "")
APP_SECRET = os.getenv("DROPBOX_APP_SECRET", "")
REDIRECT_URI = os.getenv("DROPBOX_REDIRECT_URI", "")
BUCKET = os.getenv("S3_BUCKET", "")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
ENDPOINT = os.getenv("S3_ENDPOINT_URL", "")
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

def _fernet():
    if not API_KEY:
        raise RuntimeError("CLIPFORGE_API_KEY is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256((API_KEY + ":clipforge-dropbox-token-v1").encode()).digest())
    return Fernet(key)

def _save_token(token):
    TOKEN_FILE.write_bytes(_fernet().encrypt(json.dumps(token).encode()))

def _load_token():
    if not TOKEN_FILE.exists():
        raise HTTPException(409, "Dropbox is not connected. Connect Dropbox first.")
    try:
        return json.loads(_fernet().decrypt(TOKEN_FILE.read_bytes()))
    except (InvalidToken, ValueError):
        raise HTTPException(500, "Stored Dropbox credentials could not be decrypted. Reconnect Dropbox.")

def _auth(x_api_key):
    if not API_KEY or not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Unauthorized")

def _s3():
    kw = {"region_name": REGION, "config": Config(signature_version="s3v4")}
    if ENDPOINT: kw["endpoint_url"] = ENDPOINT
    return boto3.client("s3", **kw)

def _access_token():
    token = _load_token()
    if token.get("expires_at", 0) < time.time() + 90:
        r = requests.post("https://api.dropbox.com/oauth2/token", data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": APP_KEY,
            "client_secret": APP_SECRET,
        }, timeout=30)
        if not r.ok:
            raise HTTPException(502, "Dropbox token refresh failed. Reconnect Dropbox.")
        fresh = r.json()
        token.update(fresh)
        token["expires_at"] = time.time() + int(fresh.get("expires_in", 14400))
        _save_token(token)
    return token["access_token"]

def _dbx_post(endpoint, payload):
    r = requests.post("https://api.dropboxapi.com/2/" + endpoint,
        headers={"Authorization": "Bearer " + _access_token(), "Content-Type": "application/json"},
        json=payload, timeout=60)
    if not r.ok:
        raise HTTPException(502, "Dropbox API request failed: " + r.text[:500])
    return r.json()

def _video_entry(e):
    name = e.get("name", "")
    return {"id": e.get("id"), "path": e.get("path_display") or e.get("path_lower"),
            "name": name, "size": e.get("size", 0), "modified": e.get("server_modified"),
            "content_hash": e.get("content_hash")}

def list_videos(path="", recursive=False, limit=200):
    data = _dbx_post("files/list_folder", {"path": path, "recursive": recursive,
        "include_deleted": False, "limit": min(max(int(limit), 1), 2000)})
    entries = data.get("entries", [])
    videos = [_video_entry(e) for e in entries if e.get(".tag") == "file" and Path(e.get("name", "")).suffix.lower() in VIDEO_EXTS]
    cursor = data.get("cursor")
    while data.get("has_more") and len(videos) < limit:
        data = _dbx_post("files/list_folder/continue", {"cursor": cursor})
        cursor = data.get("cursor")
        videos.extend(_video_entry(e) for e in data.get("entries", []) if e.get(".tag") == "file" and Path(e.get("name", "")).suffix.lower() in VIDEO_EXTS)
    return videos[:limit]

def import_video(path, settings, start_job):
    metadata = _dbx_post("files/get_metadata", {"path": path})
    size = int(metadata.get("size", 0))
    max_bytes = int(os.getenv("DROPBOX_MAX_VIDEO_BYTES", str(8 * 1024 * 1024 * 1024)))
    if size <= 0 or size > max_bytes:
        raise ValueError(f"Video size must be between 1 byte and {max_bytes} bytes.")
    name = metadata.get("name", "dropbox-video.mp4")
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise ValueError("Selected Dropbox file is not a supported video.")
    key = f"uploads/dropbox-{uuid.uuid4().hex}{ext}"
    r = requests.post("https://content.dropboxapi.com/2/files/download",
        headers={"Authorization": "Bearer " + _access_token(),
                 "Dropbox-API-Arg": json.dumps({"path": path})}, stream=True, timeout=(30, 900))
    if not r.ok:
        raise ValueError("Dropbox download failed: " + r.text[:500])
    client = _s3()
    client.upload_fileobj(r.raw, BUCKET, key, ExtraArgs={"ContentType": mimetypes.guess_type(name)[0] or "application/octet-stream"})
    return start_job(key, settings)

@router.get("/dropbox/connect")
def connect_dropbox(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if not (APP_KEY and APP_SECRET and REDIRECT_URI):
        raise HTTPException(503, "Dropbox setup is incomplete. Configure DROPBOX_APP_KEY, DROPBOX_APP_SECRET, and DROPBOX_REDIRECT_URI in Railway.")
    nonce = secrets.token_urlsafe(32)
    issued = int(time.time())
    payload = f"{nonce}.{issued}"
    signature = hmac.new(API_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    state = payload + "." + signature
    STATE_FILE.write_text(json.dumps({"state": state, "created": issued}))
    params = {"client_id": APP_KEY, "response_type": "code", "token_access_type": "offline",
              "redirect_uri": REDIRECT_URI, "state": state,
              "scope": "account_info.read files.metadata.read files.content.read"}
    return {"authorization_url": "https://www.dropbox.com/oauth2/authorize?" + urlencode(params),
            "redirect_uri": REDIRECT_URI, "scopes": ["account_info.read", "files.metadata.read", "files.content.read"]}

@router.get("/dropbox/callback", response_class=HTMLResponse)
def dropbox_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse("<h2>Dropbox authorization was cancelled.</h2>", status_code=400)
    if not code or not state or not STATE_FILE.exists():
        raise HTTPException(400, "Missing or expired Dropbox authorization state.")
    saved = json.loads(STATE_FILE.read_text())
    STATE_FILE.unlink(missing_ok=True)
    if not hmac.compare_digest(state, saved.get("state", "")) or time.time() - saved.get("created", 0) > 600:
        raise HTTPException(400, "Invalid or expired Dropbox authorization state.")
    payload = ".".join(state.split(".")[:2])
    expected = hmac.new(API_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(state.split(".")[-1], expected):
        raise HTTPException(400, "Dropbox authorization state validation failed.")
    r = requests.post("https://api.dropbox.com/oauth2/token", data={
        "code": code, "grant_type": "authorization_code", "client_id": APP_KEY,
        "client_secret": APP_SECRET, "redirect_uri": REDIRECT_URI}, timeout=30)
    if not r.ok:
        raise HTTPException(502, "Dropbox token exchange failed.")
    token = r.json()
    if not token.get("refresh_token"):
        raise HTTPException(502, "Dropbox did not return an offline refresh token. Reauthorize with offline access.")
    token["expires_at"] = time.time() + int(token.get("expires_in", 14400))
    _save_token(token)
    return HTMLResponse("<h2>Dropbox connected to ClipForge Studio.</h2><p>You can close this window and return to ClipForge.</p>")

@router.get("/dropbox/status")
def dropbox_status(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    return {"connected": TOKEN_FILE.exists()}

@router.get("/dropbox/videos")
def dropbox_videos(path: str = "", recursive: bool = False, limit: int = 200, x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if not TOKEN_FILE.exists(): raise HTTPException(409, "Dropbox is not connected.")
    return {"videos": list_videos(path, recursive, limit)}

@router.post("/dropbox/disconnect")
def disconnect_dropbox(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if TOKEN_FILE.exists():
        try:
            token = _load_token()
            requests.post("https://api.dropboxapi.com/2/auth/token/revoke",
                headers={"Authorization": "Bearer " + token["access_token"]}, timeout=20)
        except Exception:
            pass
        TOKEN_FILE.unlink(missing_ok=True)
    return {"connected": False, "message": "Dropbox token removed from ClipForge."}
