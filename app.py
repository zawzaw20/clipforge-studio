import os, json, uuid, time, shutil, subprocess, threading, secrets, mimetypes
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import boto3
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from dropbox_integration import router as dropbox_router, list_videos as dropbox_list_videos, import_video as dropbox_import_video

APP_DIR=Path(os.getenv("CLIPFORGE_DATA_DIR","/data"))
APP_DIR.mkdir(parents=True,exist_ok=True)
JOBS=APP_DIR/"jobs"; JOBS.mkdir(exist_ok=True)
WORK=Path(os.getenv("CLIPFORGE_WORK_DIR","/tmp/clipforge-work")); WORK.mkdir(parents=True,exist_ok=True)
API_KEY=os.getenv("CLIPFORGE_API_KEY","")
BUCKET=os.getenv("S3_BUCKET","")
REGION=os.getenv("AWS_DEFAULT_REGION","us-west-2")
ENDPOINT=os.getenv("S3_ENDPOINT_URL","")
mcp=FastMCP("ClipForge Studio")
app=FastAPI(title="ClipForge Studio API",version="0.3.0")
app.include_router(dropbox_router)

@app.middleware("http")
async def protect_mcp(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        auth=request.headers.get("authorization","")
        supplied=auth[7:] if auth.lower().startswith("bearer ") else ""
        if not API_KEY or not secrets.compare_digest(supplied,API_KEY):
            return JSONResponse(status_code=401,content={"detail":"Unauthorized MCP connection"})
    return await call_next(request)

def now(): return datetime.now(timezone.utc).isoformat()
def require_key(key):
    if not API_KEY or not secrets.compare_digest(key or "",API_KEY):
        raise ValueError("Invalid API key. Configure CLIPFORGE_API_KEY in the cloud service.")
def s3():
    if not BUCKET: raise RuntimeError("Object storage is not configured.")
    kw={"region_name":REGION,"config":Config(signature_version="s3v4")}
    if ENDPOINT: kw["endpoint_url"]=ENDPOINT
    return boto3.client("s3",**kw)
def job_path(jid): return JOBS/f"{jid}.json"
def read_job(jid):
    p=job_path(jid)
    if not p.exists(): raise KeyError(jid)
    return json.loads(p.read_text())
def write_job(j):
    j["updated_at"]=now(); job_path(j["id"]).write_text(json.dumps(j,indent=2))
def command(args,timeout=7200):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-5000:])
    return (p.stdout or "")[-3000:]
def available(name): return shutil.which(name) is not None

def process_job(jid):
    j=read_job(jid); j["status"]="processing"; j["stages"]=[{"name":"download","status":"running"}]; write_job(j)
    folder=WORK/jid; folder.mkdir(parents=True,exist_ok=True)
    source=folder/"source.mp4"; output=folder/"final.mp4"
    try:
        client=s3(); client.download_file(BUCKET,j["source_key"],str(source))
        j["stages"][0]["status"]="complete"; write_job(j)
        current=source; transcript=None; captions=[]
        settings=j.get("settings",{})
        # WhisperX: optional install because CUDA/GPU builds vary by host.
        j["stages"].append({"name":"WhisperX transcription","status":"running"}); write_job(j)
        if available("whisperx") and settings.get("captions",True):
            out=folder/"transcript"; out.mkdir(exist_ok=True)
            command(["whisperx",str(current),"--output_dir",str(out),"--output_format","json","--compute_type",settings.get("compute_type","int8")],timeout=14400)
            js=list(out.glob("*.json"))
            if js:
                transcript=json.loads(js[0].read_text())
                for seg in transcript.get("segments",[]):
                    for w in seg.get("words",[]) or []:
                        if w.get("start") is not None and w.get("end") is not None and w.get("word"):
                            captions.append({"start":float(w["start"]),"end":float(w["end"]),"text":str(w["word"]).strip()})
            j["stages"][-1].update(status="complete",details=f"{len(captions)} timed words")
        else: j["stages"][-1].update(status="skipped",details="WhisperX not installed or captions disabled")
        write_job(j)
        j["stages"].append({"name":"PySceneDetect","status":"running"}); write_job(j)
        if available("scenedetect"):
            command(["scenedetect","-i",str(current),"detect-content","list-scenes","-o",str(folder/"scenes")],timeout=3600)
            j["stages"][-1].update(status="complete")
        else: j["stages"][-1].update(status="skipped",details="PySceneDetect unavailable")
        write_job(j)
        j["stages"].append({"name":"Auto-Editor","status":"running"}); write_job(j)
        if available("auto-editor") and settings.get("remove_silence",True):
            edited=folder/"auto-edited.mp4"
            command(["auto-editor",str(current),"--output",str(edited),"--no-open"],timeout=14400)
            if edited.exists(): current=edited
            j["stages"][-1].update(status="complete")
        else: j["stages"][-1].update(status="skipped",details="Auto-Editor unavailable or silence removal disabled")
        write_job(j)
        j["stages"].append({"name":"Remotion captions","status":"running"}); write_job(j)
        remotion=Path("/app/remotion/node_modules/.bin/remotion")
        if captions and remotion.exists() and settings.get("captions",True):
            props={"src":str(current.resolve()),"items":captions,"durationInFrames":max(1,int(probe_duration(current)*30))}
            props_file=folder/"props.json"; props_file.write_text(json.dumps(props))
            rendered=folder/"captioned.mp4"
            command([str(remotion),"render","src/index.jsx","ClipForge",str(rendered),"--props",str(props_file),"--codec","h264"],timeout=14400)
            if rendered.exists(): current=rendered
            j["stages"][-1].update(status="complete")
        else: j["stages"][-1].update(status="skipped",details="No timed captions or Remotion unavailable")
        write_job(j)
        j["stages"].append({"name":"FFmpeg export","status":"running"}); write_job(j)
        command(["ffmpeg","-y","-i",str(current),"-c:v","libx264","-preset","veryfast","-crf","21","-c:a","aac","-movflags","+faststart",str(output)],timeout=14400)
        key=f"exports/{jid}/final.mp4"; client.upload_file(str(output),BUCKET,key,ExtraArgs={"ContentType":"video/mp4"})
        j["status"]="complete"; j["output_key"]=key
        j["download_url"]=client.generate_presigned_url("get_object",Params={"Bucket":BUCKET,"Key":key},ExpiresIn=86400)
        j["stages"][-1].update(status="complete"); j["completed_at"]=now(); write_job(j)
    except Exception as e:
        j["status"]="failed"; j["error"]=str(e)[:2000]
        if j.get("stages"): j["stages"][-1]["status"]="failed"
        write_job(j)

def probe_duration(path):
    try:
        out=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],text=True)
        return max(1,float(out.strip()))
    except Exception: return 30.0

class EditSettings(BaseModel):
    captions: bool=True
    remove_silence: bool=True
    compute_type: str="int8"
    aspect_ratio: str="original"

class StartEdit(BaseModel):
    source_key: str
    settings: EditSettings=Field(default_factory=EditSettings)

@app.get("/health")
def health():
    return {"status":"ok","service":"ClipForge Studio","engines":{n:available(n) for n in ["ffmpeg","ffprobe","whisperx","scenedetect","auto-editor"]},"storage_configured":bool(BUCKET),"mcp":"/mcp"}

@app.post("/uploads/presign")
def presign(filename: str, content_type: str="video/mp4", x_api_key: Optional[str]=Header(default=None)):
    try: require_key(x_api_key)
    except ValueError as e: raise HTTPException(401,str(e))
    ext=Path(filename).suffix or ".mp4"; key=f"uploads/{uuid.uuid4().hex}{ext}"
    try:
        url=s3().generate_presigned_url("put_object",Params={"Bucket":BUCKET,"Key":key,"ContentType":content_type},ExpiresIn=3600)
    except Exception as e: raise HTTPException(503,f"Storage unavailable: {e}")
    return {"upload_url":url,"source_key":key,"method":"PUT","content_type":content_type,"expires_in":3600}

@app.post("/jobs")
def create_job(body: StartEdit, background: BackgroundTasks, x_api_key: Optional[str]=Header(default=None)):
    try: require_key(x_api_key)
    except ValueError as e: raise HTTPException(401,str(e))
    jid=uuid.uuid4().hex
    j={"id":jid,"status":"queued","source_key":body.source_key,"settings":body.settings.model_dump(),"created_at":now(),"stages":[]}
    write_job(j); background.add_task(process_job,jid)
    return j

@app.get("/jobs/{jid}")
def job_status(jid: str, x_api_key: Optional[str]=Header(default=None)):
    try: require_key(x_api_key); return read_job(jid)
    except ValueError as e: raise HTTPException(401,str(e))
    except KeyError: raise HTTPException(404,"Job not found")

@mcp.tool()
def clipforge_health() -> dict:
    """Check ClipForge API and installed editing engines. Requires HTTP Authorization: Bearer token."""
    return {"service":"ClipForge Studio","status":"ok","engines":{n:available(n) for n in ["ffmpeg","ffprobe","whisperx","scenedetect","auto-editor"]},"storage_configured":bool(BUCKET)}

@mcp.tool()
def create_video_upload(filename: str, content_type: str="video/mp4") -> dict:
    """Create a temporary S3 upload URL. Upload the video bytes to upload_url using HTTP PUT, then pass source_key to start_video_edit. Requires HTTP Authorization: Bearer token."""
    ext=Path(filename).suffix or ".mp4"; key=f"uploads/{uuid.uuid4().hex}{ext}"
    url=s3().generate_presigned_url("put_object",Params={"Bucket":BUCKET,"Key":key,"ContentType":content_type},ExpiresIn=3600)
    return {"upload_url":url,"source_key":key,"method":"PUT","content_type":content_type,"expires_in":3600}

@mcp.tool()
def start_video_edit(source_key: str, captions: bool=True, remove_silence: bool=True, aspect_ratio: str="original") -> dict:
    """Queue an automatic video edit for an already uploaded source_key. Requires HTTP Authorization: Bearer token."""
    jid=uuid.uuid4().hex
    j={"id":jid,"status":"queued","source_key":source_key,"settings":{"captions":captions,"remove_silence":remove_silence,"aspect_ratio":aspect_ratio,"compute_type":"int8"},"created_at":now(),"stages":[]}
    write_job(j); threading.Thread(target=process_job,args=(jid,),daemon=True).start()
    return {"job_id":jid,"status":"queued"}

@mcp.tool()
def get_video_edit_status(job_id: str) -> dict:
    """Get processing stage details and the finished MP4 download URL when ready. Requires HTTP Authorization: Bearer token."""
    try: return read_job(job_id)
    except KeyError: return {"error":"Job not found"}


@mcp.tool()
def dropbox_connection_status() -> dict:
    """Check whether Dropbox is linked to this ClipForge instance."""
    from dropbox_integration import TOKEN_FILE
    return {"connected": TOKEN_FILE.exists()}

@mcp.tool()
def get_dropbox_authorization_url() -> dict:
    """Create a Dropbox OAuth authorization link. Open it in a browser, approve read-only access, then list videos."""
    from dropbox_integration import APP_KEY, APP_SECRET, REDIRECT_URI, API_KEY, STATE_FILE
    from urllib.parse import urlencode
    import hmac, hashlib, secrets
    if not (APP_KEY and APP_SECRET and REDIRECT_URI):
        return {"error": "Dropbox app credentials are not configured in Railway yet."}
    nonce = secrets.token_urlsafe(32); issued = int(time.time()); payload = f"{nonce}.{issued}"
    signature = hmac.new(API_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    state = payload + "." + signature
    STATE_FILE.write_text(json.dumps({"state": state, "created": issued}))
    params = {"client_id": APP_KEY, "response_type": "code", "token_access_type": "offline",
              "redirect_uri": REDIRECT_URI, "state": state,
              "scope": "account_info.read files.metadata.read files.content.read"}
    return {"authorization_url": "https://www.dropbox.com/oauth2/authorize?" + __import__("urllib.parse", fromlist=["urlencode"]).urlencode(params)}

@mcp.tool()
def list_dropbox_videos(path: str = "", recursive: bool = False, limit: int = 200) -> dict:
    """List video files in a Dropbox folder. path is an empty string for the Dropbox root."""
    from dropbox_integration import TOKEN_FILE
    if not TOKEN_FILE.exists(): return {"error": "Connect Dropbox first using get_dropbox_authorization_url."}
    return {"videos": dropbox_list_videos(path, recursive, limit)}

@mcp.tool()
def start_dropbox_video_edit(path: str, captions: bool = True, remove_silence: bool = True, aspect_ratio: str = "original") -> dict:
    """Download one selected Dropbox video into ClipForge storage and queue it for automatic editing."""
    from dropbox_integration import TOKEN_FILE
    if not TOKEN_FILE.exists(): return {"error": "Connect Dropbox first."}
    jid = uuid.uuid4().hex
    settings = {"captions": captions, "remove_silence": remove_silence, "aspect_ratio": aspect_ratio, "compute_type": "int8"}
    def make_job(source_key, opts):
        j = {"id": jid, "status": "queued", "source_key": source_key, "settings": opts, "created_at": now(), "stages": []}
        write_job(j)
        threading.Thread(target=process_job, args=(jid,), daemon=True).start()
        return {"job_id": jid, "status": "queued"}
    try:
        result = dropbox_import_video(path, settings, make_job)
        return result
    except Exception as e:
        return {"error": str(e)[:1000]}

# MCP Streamable HTTP endpoint is served by the same ASGI application.
app.mount("/mcp",mcp.streamable_http_app())
