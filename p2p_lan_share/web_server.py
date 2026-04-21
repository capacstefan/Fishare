"""Flask QR web server: phone uploads files or posts text to the app.

Runs in a background thread. Emits Qt signals when files/texts arrive so the
GUI can integrate them (history, quick text inbox, downloads folder).
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

import qrcode
from flask import Flask, render_template_string, request
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from werkzeug.serving import make_server

from . import config
from .discovery import _local_ip

PAGE = """<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to {{name}}</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:18px;max-width:520px;margin:auto;}
  h2{margin-top:0}
  .card{border:1px solid #ddd;border-radius:10px;padding:14px;margin-bottom:14px;}
  input[type=file],textarea{width:100%;box-sizing:border-box;}
  textarea{min-height:110px}
  button{background:#2d7;color:#fff;border:0;padding:10px 16px;border-radius:8px;font-size:16px}
  .ok{color:#2a7a2a}.err{color:#b00}
</style></head><body>
<h2>Send to {{name}}</h2>
<div class="card">
  <form method="POST" action="/upload" enctype="multipart/form-data">
    <p>Upload files</p>
    <input type="file" name="files" multiple required><br><br>
    <button type="submit">Upload</button>
  </form>
</div>
<div class="card">
  <form method="POST" action="/text">
    <p>Send quick text</p>
    <textarea name="text" maxlength="500" required placeholder="Type up to 500 chars…"></textarea>
    <br><br><button type="submit">Send text</button>
  </form>
</div>
{% if msg %}<p class="{{cls}}">{{msg}}</p>{% endif %}
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
        url = f"http://{ip}:{config.WEB_PORT}/"
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
        name = self._name
        dest = self._dir

        @app.route("/", methods=["GET"])
        def index():
            return render_template_string(PAGE, name=name, msg="", cls="")

        @app.route("/upload", methods=["POST"])
        def upload():
            files = request.files.getlist("files")
            if not files:
                return render_template_string(PAGE, name=name, msg="No files.", cls="err")
            dest.mkdir(parents=True, exist_ok=True)
            saved = 0
            for fs in files:
                if not fs or not fs.filename:
                    continue
                safe = Path(fs.filename).name  # strip any path
                target = _unique(dest / safe)
                fs.save(str(target))
                size = target.stat().st_size
                self.file_received.emit(target.name, str(target), size)
                saved += 1
            return render_template_string(
                PAGE, name=name, msg=f"Uploaded {saved} file(s).", cls="ok"
            )

        @app.route("/text", methods=["POST"])
        def text():
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


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    i = 1
    while True:
        c = path.with_name(f"{stem} ({i}){suf}")
        if not c.exists():
            return c
        i += 1
