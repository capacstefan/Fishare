"""Flask QR web server: phone uploads files or posts text to the app.

Runs in a background thread. Emits Qt signals when files/texts arrive so the
GUI can integrate them (history, quick text inbox, downloads folder).
"""
from __future__ import annotations

import hmac
import io
import secrets
import threading
from pathlib import Path

import qrcode
from flask import Flask, abort, render_template_string, request
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from werkzeug.serving import make_server

from . import config
from .discovery import _local_ip
from .util import unique_path

PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to {{name}}</title>
<style>
  :root{
    --bg:#f4f5f7; --surface:#ffffff; --border:#e4e6eb;
    --text:#1d1d1f; --muted:#6e6e73; --accent:#0a84ff; --accent-hi:#409cff;
    --ok:#1f8a3b; --err:#c0382b;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--text);}
  body{
    font-family:-apple-system,"Segoe UI Variable","Segoe UI",Roboto,sans-serif;
    min-height:100vh;
    display:flex; flex-direction:column; align-items:center;
    padding:28px 18px 40px;
  }
  .wrap{width:100%; max-width:520px; display:flex; flex-direction:column; align-items:stretch;}
  h1{
    font-size:26px; font-weight:700; margin:4px 0 20px;
    text-align:center; letter-spacing:-0.01em;
  }
  h1 .sub{display:block; font-size:14px; font-weight:500; color:var(--muted); margin-top:4px;}
  .card{
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:14px;
    padding:22px 20px;
    margin-bottom:16px;
    box-shadow:0 1px 2px rgba(0,0,0,0.03);
  }
  .card h2{
    font-size:17px; font-weight:600; margin:0 0 14px; text-align:center;
  }
  label.file{
    display:block; text-align:center; cursor:pointer;
    padding:14px; border:1px dashed var(--border); border-radius:10px;
    color:var(--muted); font-size:15px; background:#fafbfc;
    margin-bottom:14px;
  }
  label.file:hover{border-color:var(--accent); color:var(--accent);}
  input[type=file]{display:none;}
  #file-names{
    font-size:13px; color:var(--muted); text-align:center;
    margin:-8px 0 14px; min-height:18px; word-break:break-all;
  }
  textarea{
    width:100%; min-height:130px; resize:vertical;
    border:1px solid var(--border); border-radius:10px;
    padding:12px 14px; font-size:15px; font-family:inherit;
    background:var(--surface); color:var(--text);
    margin-bottom:14px;
  }
  textarea:focus{outline:none; border-color:var(--accent);}
  button{
    width:100%;
    background:var(--accent); color:#fff; border:0;
    padding:13px 18px; border-radius:10px;
    font-size:16px; font-weight:600; cursor:pointer;
    transition:background .15s ease;
  }
  button:hover{background:var(--accent-hi);}
  .msg{
    text-align:center; font-size:14px; padding:10px 14px;
    border-radius:10px; margin-bottom:8px;
  }
  .ok{background:#e6f7ec; color:var(--ok); border:1px solid #bde5c8;}
  .err{background:#fdecea; color:var(--err); border:1px solid #f5c6c1;}
</style></head>
<body>
  <div class="wrap">
    <h1>Send to {{name}}<span class="sub">Uploads arrive in the app's download folder</span></h1>
    {% if msg %}<div class="msg {{cls}}">{{msg}}</div>{% endif %}

    <div class="card">
      <h2>Upload files</h2>
      <form method="POST" action="upload" enctype="multipart/form-data">
        <label class="file" for="f">Tap to choose files</label>
        <input id="f" type="file" name="files" multiple required
               onchange="document.getElementById('file-names').textContent =
                 Array.from(this.files).map(x=>x.name).join(', ') || '';">
        <div id="file-names"></div>
        <button type="submit">Upload</button>
      </form>
    </div>

    <div class="card">
      <h2>Send quick text</h2>
      <form method="POST" action="text">
        <textarea name="text" maxlength="500" required
                  placeholder="Type up to 500 characters…"></textarea>
        <button type="submit">Send text</button>
      </form>
    </div>
  </div>
</body></html>"""


class QrWebServer(QObject):
    file_received = pyqtSignal(str, str, int)  # filename, saved_path, size
    text_received = pyqtSignal(str, str)       # sender_label, text
    started = pyqtSignal(str, object)          # url, QPixmap
    stopped = pyqtSignal()

    def __init__(self, device_name: str, download_dir: str) -> None:
        super().__init__()
        self._name = device_name
        self._dir = Path(download_dir)
        self._server = None
        self._thread: threading.Thread | None = None
        # Random URL path prevents anonymous LAN users hitting the endpoints;
        # only someone who scans the QR can reach /<token>/.
        self._token = secrets.token_urlsafe(16)

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._server is not None:
            return
        app = self._make_app()
        host = "0.0.0.0"
        self._server = make_server(host, config.WEB_PORT, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        ip = _local_ip()
        url = f"http://{ip}:{config.WEB_PORT}/{self._token}/"
        self.started.emit(url, self._make_qr_pixmap(url))

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
        except Exception:
            pass
        self._server = None
        self._thread = None
        self.stopped.emit()

    # ---------- Flask app ----------
    def _make_app(self) -> Flask:
        app = Flask(__name__)
        # werkzeug enforces max request body -> HTTP 413 if exceeded.
        app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_SIZE
        name = self._name
        dest = self._dir
        token = self._token

        def check(tok: str) -> None:
            if not hmac.compare_digest(tok, token):
                abort(404)

        @app.route("/<tok>/", methods=["GET"])
        def index(tok: str):
            check(tok)
            return render_template_string(PAGE, name=name, msg="", cls="")

        @app.route("/<tok>/upload", methods=["POST"])
        def upload(tok: str):
            check(tok)
            files = request.files.getlist("files")
            if not files:
                return render_template_string(PAGE, name=name, msg="No files.", cls="err")
            dest.mkdir(parents=True, exist_ok=True)
            saved = 0
            for fs in files:
                if not fs or not fs.filename:
                    continue
                safe = Path(fs.filename).name  # strip any path
                target = unique_path(dest / safe)
                fs.save(str(target))
                size = target.stat().st_size
                self.file_received.emit(target.name, str(target), size)
                saved += 1
            return render_template_string(
                PAGE, name=name, msg=f"Uploaded {saved} file(s).", cls="ok"
            )

        @app.route("/<tok>/text", methods=["POST"])
        def text(tok: str):
            check(tok)
            txt = (request.form.get("text") or "").strip()
            if not txt:
                return render_template_string(PAGE, name=name, msg="Empty text.", cls="err")
            txt = txt[: config.QUICK_TEXT_MAX_CHARS]
            self.text_received.emit("Phone", txt)
            return render_template_string(PAGE, name=name, msg="Text sent.", cls="ok")

        return app

    # ---------- QR ----------
    def _make_qr_pixmap(self, data: str) -> QPixmap:
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qimg = QImage.fromData(buf.getvalue(), "PNG")
        return QPixmap.fromImage(qimg)
