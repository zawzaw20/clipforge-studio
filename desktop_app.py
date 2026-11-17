import json, os, re, secrets, shutil, subprocess, sys, time, webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from PySide6.QtCore import QThread, Signal, Qt, QStandardPaths
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QListWidget, QListWidgetItem, QLineEdit, QCheckBox, QProgressBar, QMessageBox, QGroupBox, QFormLayout)

APP = "ClipForge Studio"
EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
DATA = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "ClipForge"
DATA.mkdir(parents=True, exist_ok=True)
CONFIG = DATA / "settings.json"
DBX_API = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"
CALLBACK_PORT = 53682

def config_read():
    try: return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception: return {}

def config_write(data):
    CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")

def command(args, timeout=14400):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "Command failed")[-2500:])
    return p.stdout

def duration(path):
    return float(command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 120).strip())

def keyring_get():
    try:
        import keyring
        return keyring.get_password(APP, "dropbox_refresh_token")
    except Exception: return None

def keyring_set(token):
    import keyring
    keyring.set_password(APP, "dropbox_refresh_token", token)

def dbx_token(app_key):
    refresh = keyring_get()
    if not refresh: raise RuntimeError("Connect Dropbox first.")
    r = requests.post("https://api.dropbox.com/oauth2/token", data={"grant_type":"refresh_token","refresh_token":refresh,"client_id":app_key}, timeout=30)
    if not r.ok: raise RuntimeError("Dropbox token refresh failed. Reconnect Dropbox.")
    return r.json()["access_token"]

def dbx_call(app_key, endpoint, payload):
    r = requests.post(f"{DBX_API}/{endpoint}", headers={"Authorization":"Bearer "+dbx_token(app_key),"Content-Type":"application/json"}, json=payload, timeout=60)
    if not r.ok: raise RuntimeError(f"Dropbox API error {r.status_code}: {r.text[:300]}")
    return r.json()

class DropboxOAuth:
    def __init__(self, app_key):
        import hashlib, base64
        self.app_key=app_key; self.state=secrets.token_urlsafe(32); self.verifier=secrets.token_urlsafe(64)
        self.challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        self.code=None; self.error=None

    def connect(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                q=parse_qs(urlparse(self.path).query)
                if q.get("state",[""])[0] != owner.state: owner.error="OAuth state validation failed."
                elif "error" in q: owner.error=q["error"][0]
                else: owner.code=q.get("code",[None])[0]
                body=b"Dropbox authorization received. Return to ClipForge."
                self.send_response(200 if owner.code else 400); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        server=HTTPServer(("127.0.0.1",CALLBACK_PORT),Handler); server.timeout=1
        redirect=f"http://localhost:{CALLBACK_PORT}/oauth/callback"
        params={"client_id":self.app_key,"response_type":"code","token_access_type":"offline","redirect_uri":redirect,"state":self.state,"code_challenge":self.challenge,"code_challenge_method":"S256","scope":"account_info.read files.metadata.read files.content.read"}
        webbrowser.open("https://www.dropbox.com/oauth2/authorize?"+urlencode(params))
        deadline=time.time()+240
        while time.time()<deadline and not self.code and not self.error: server.handle_request()
        server.server_close()
        if self.error: raise RuntimeError("Dropbox authorization failed: "+self.error)
        if not self.code: raise RuntimeError("Dropbox sign-in timed out.")
        r=requests.post("https://api.dropbox.com/oauth2/token",data={"code":self.code,"grant_type":"authorization_code","client_id":self.app_key,"redirect_uri":redirect,"code_verifier":self.verifier},timeout=30)
        if not r.ok: raise RuntimeError("Dropbox token exchange failed: "+r.text[:300])
        token=r.json().get("refresh_token")
        if not token: raise RuntimeError("Dropbox did not return an offline refresh token.")
        keyring_set(token)

def list_videos(app_key, folder):
    data=dbx_call(app_key,"files/list_folder",{"path":folder,"recursive":False,"include_deleted":False,"limit":2000})
    found=[]
    while True:
        for e in data.get("entries",[]):
            if e.get(".tag")=="file" and Path(e.get("name","")).suffix.lower() in EXTS:
                found.append({"name":e["name"],"path":e.get("path_display") or e["path_lower"],"size":e.get("size",0)})
        if not data.get("has_more"): break
        data=dbx_call(app_key,"files/list_folder/continue",{"cursor":data["cursor"]})
    return found

class EditorWorker(QThread):
    progress=Signal(str,int); done=Signal(list); failed=Signal(str)
    def __init__(self,source,out,captions,silence,vertical):
        super().__init__(); self.source=Path(source); self.out=Path(out); self.captions=captions; self.silence=silence; self.vertical=vertical
    def run(self):
        try:
            self.out.mkdir(parents=True,exist_ok=True)
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"): raise RuntimeError("FFmpeg/ffprobe missing. Run setup_windows.ps1.")
            total=duration(self.source); segments=[]
            if self.captions and shutil.which("whisperx"):
                self.progress.emit("Transcribing with WhisperX…",10)
                td=self.out/"_transcript"; td.mkdir(exist_ok=True)
                command(["whisperx",str(self.source),"--output_dir",str(td),"--output_format","json","--compute_type","float16"])
                files=list(td.glob("*.json"))
                if files: segments=json.loads(files[0].read_text(encoding="utf-8")).get("segments",[])
            if shutil.which("scenedetect"):
                self.progress.emit("Detecting scenes…",30)
                try: command(["scenedetect","-i",str(self.source),"detect-content","list-scenes","-o",str(self.out/"_scenes")],3600)
                except Exception: pass
            if self.silence and shutil.which("auto-editor"):
                self.progress.emit("Analyzing silence…",40)
                try: command(["auto-editor",str(self.source),"--output",str(self.out/"_silence_review.mp4"),"--no-open"])
                except Exception: pass
            windows=[]
            if segments:
                for s in segments:
                    a=max(0,float(s.get("start",0))-12); b=min(total,max(float(s.get("end",a+20))+18,a+30))
                    if b-a>=12: windows.append((a,min(b,a+65)))
            if not windows:
                a=0
                while a<total:
                    b=min(total,a+45)
                    if b-a>=12: windows.append((a,b))
                    a+=38
            selected=[]
            for w in sorted(windows):
                if selected and w[0]<selected[-1][1]-8: selected[-1]=(selected[-1][0],min(total, max(selected[-1][1],w[1])))
                else: selected.append(w)
            results=[]
            nvenc=False
            try: nvenc="h264_nvenc" in command(["ffmpeg","-hide_banner","-encoders"],60)
            except Exception: pass
            for i,(a,b) in enumerate(selected[:30],1):
                self.progress.emit(f"Rendering clip {i}/{min(len(selected),30)}…",50+int(48*i/max(1,min(len(selected),30))))
                target=self.out/f"clip_{i:02d}.mp4"
                args=["ffmpeg","-y","-ss",f"{a:.3f}","-i",str(self.source),"-t",f"{b-a:.3f}"]
                if self.vertical: args += ["-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"]
                if nvenc: args += ["-c:v","h264_nvenc","-preset","p4","-cq","21"]
                else: args += ["-c:v","libx264","-preset","veryfast","-crf","21"]
                args += ["-c:a","aac","-b:a","192k","-movflags","+faststart",str(target)]
                try: command(args,7200)
                except Exception:
                    if not nvenc: raise
                    args=["ffmpeg","-y","-ss",f"{a:.3f}","-i",str(self.source),"-t",f"{b-a:.3f}"]
                    if self.vertical: args += ["-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"]
                    args += ["-c:v","libx264","-preset","veryfast","-crf","21","-c:a","aac","-movflags","+faststart",str(target)]
                    command(args,7200)
                results.append(str(target))
            if not results: raise RuntimeError("No clips generated.")
            self.progress.emit(f"Finished {len(results)} clips.",100); self.done.emit(results)
        except Exception as e: self.failed.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("ClipForge Studio"); self.resize(1100,760)
        self.cfg=config_read(); root=QWidget(); self.setCentralWidget(root); layout=QVBoxLayout(root)
        head=QHBoxLayout(); title=QLabel("CLIPFORGE STUDIO"); title.setStyleSheet("font-size:26px;font-weight:900;color:#d6b45b"); head.addWidget(title); head.addStretch(); head.addWidget(QLabel("WINDOWS DESKTOP · LOCAL PROCESSING")); layout.addLayout(head)
        columns=QHBoxLayout()
        local=QGroupBox("Local video library"); ll=QVBoxLayout(local); self.files=QListWidget(); ll.addWidget(self.files)
        row=QHBoxLayout(); add=QPushButton("Add videos"); add.clicked.connect(self.add_files); row.addWidget(add); ll.addLayout(row); columns.addWidget(local,1)
        cloud=QGroupBox("Dropbox · read-only"); cl=QVBoxLayout(cloud); form=QFormLayout(); self.appkey=QLineEdit(self.cfg.get("dropbox_app_key","")); self.folder=QLineEdit(""); self.folder.setPlaceholderText("Folder path (blank = root)"); form.addRow("App key",self.appkey); form.addRow("Folder",self.folder); cl.addLayout(form)
        row=QHBoxLayout()
        connect=QPushButton("Connect Dropbox"); connect.clicked.connect(self.connect_dbx); browse=QPushButton("Browse"); browse.clicked.connect(self.browse_dbx); imp=QPushButton("Import selected"); imp.clicked.connect(self.import_dbx)
        for b in (connect,browse,imp): row.addWidget(b)
        cl.addLayout(row); self.dbfiles=QListWidget(); cl.addWidget(self.dbfiles); columns.addWidget(cl,1); layout.addLayout(columns)
        opts=QGroupBox("Editing options"); ol=QHBoxLayout(opts); self.ai=QCheckBox("AI transcription"); self.ai.setChecked(True); self.silence=QCheckBox("Silence analysis"); self.silence.setChecked(True); self.vertical=QCheckBox("Vertical 9:16"); self.vertical.setChecked(True)
        for w in (self.ai,self.silence,self.vertical): ol.addWidget(w)
        self.output=QLineEdit(str(Path.home()/"Videos"/"ClipForge Exports")); ol.addWidget(self.output,1); choose=QPushButton("Export folder"); choose.clicked.connect(self.choose_output); ol.addWidget(choose); layout.addWidget(opts)
        action=QHBoxLayout(); self.go=QPushButton("CREATE CLIPS"); self.go.setMinimumHeight(46); self.go.clicked.connect(self.process); action.addWidget(self.go); self.bar=QProgressBar(); action.addWidget(self.bar,1); layout.addLayout(action)
        self.status=QLabel("Select a video to start. Imported videos are copied locally; Dropbox originals are never changed."); layout.addWidget(self.status); self.results=QListWidget(); layout.addWidget(self.results,1)
        self.setStyleSheet("QMainWindow,QWidget{background:#111916;color:#e9eee9} QGroupBox{border:1px solid #35473d;border-radius:8px;margin-top:10px;padding:10px;font-weight:700} QGroupBox::title{left:10px;padding:0 5px;color:#d6b45b} QPushButton{background:#293b32;color:white;border:1px solid #486052;padding:9px;border-radius:6px} QLineEdit,QListWidget{background:#19241e;border:1px solid #35473d;border-radius:6px;padding:6px} QProgressBar{border:1px solid #35473d;border-radius:5px;text-align:center} QProgressBar::chunk{background:#1d8b5d}")
    def add_files(self):
        files,_=QFileDialog.getOpenFileNames(self,"Choose videos",str(Path.home()),"Videos (*.mp4 *.mov *.m4v *.mkv *.avi *.webm)")
        for f in files: self.files.addItem(f)
    def choose_output(self):
        p=QFileDialog.getExistingDirectory(self,"Export folder",self.output.text())
        if p: self.output.setText(p)
    def connect_dbx(self):
        key=self.appkey.text().strip()
        if not key: QMessageBox.warning(self,"Dropbox","Create a Dropbox app and enter its App key."); return
        self.cfg["dropbox_app_key"]=key; config_write(self.cfg); self.status.setText("Opening Dropbox authorization…")
        import threading
        def go():
            try: DropboxOAuth(key).connect(); self.status.setText("Dropbox connected.")
            except Exception as e: self.status.setText("Dropbox: "+str(e))
        threading.Thread(target=go,daemon=True).start()
    def browse_dbx(self):
        try:
            self.dbx=list_videos(self.appkey.text().strip(),self.folder.text().strip()); self.dbfiles.clear()
            for v in self.dbx:
                it=QListWidgetItem(f'{v["name"]} · {v["size"]/1048576:.1f} MB'); it.setData(Qt.UserRole,v); self.dbfiles.addItem(it)
            self.status.setText(f"Found {len(self.dbx)} videos. Dropbox files are read-only.")
        except Exception as e: QMessageBox.critical(self,"Dropbox",str(e))
    def import_dbx(self):
        it=self.dbfiles.currentItem()
        if not it: QMessageBox.information(self,"Import","Select a Dropbox video."); return
        v=it.data(Qt.UserRole); target=Path.home()/"Videos"/"ClipForge Imports"/re.sub(r'[<>:"/\\\\|?*]',"_",v["name"]); target.parent.mkdir(parents=True,exist_ok=True)
        try:
            with requests.post(f'{DBX_CONTENT}/2/files/download',headers={"Authorization":"Bearer "+dbx_token(self.appkey.text().strip()),"Dropbox-API-Arg":json.dumps({"path":v["path"]})},stream=True,timeout=(30,900)) as r:
                r.raise_for_status()
                with target.open("wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk: f.write(chunk)
            self.files.addItem(str(target)); self.status.setText("Imported locally. Dropbox original unchanged.")
        except Exception as e:
            target.unlink(missing_ok=True); QMessageBox.critical(self,"Import failed",str(e))
    def process(self):
        it=self.files.currentItem()
        if not it: QMessageBox.information(self,"ClipForge","Select a local video."); return
        source=it.text(); out=Path(self.output.text())/Path(source).stem; self.go.setEnabled(False); self.results.clear(); self.bar.setValue(0)
        self.worker=EditorWorker(source,out,self.ai.isChecked(),self.silence.isChecked(),self.vertical.isChecked())
        self.worker.progress.connect(lambda s,n:(self.status.setText(s),self.bar.setValue(n)))
        self.worker.done.connect(self.finished); self.worker.failed.connect(self.error); self.worker.start()
    def finished(self,files):
        self.go.setEnabled(True)
        for f in files: self.results.addItem(f)
        self.status.setText(f"Created {len(files)} clips locally."); QMessageBox.information(self,"Complete",f"Created {len(files)} clips.")
    def error(self,msg):
        self.go.setEnabled(True); self.status.setText("Processing failed."); QMessageBox.critical(self,"ClipForge error",msg)

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP); w=MainWindow(); w.show(); sys.exit(app.exec())
if __name__=="__main__": main()
