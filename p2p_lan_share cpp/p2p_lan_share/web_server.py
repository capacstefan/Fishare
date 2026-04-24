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

_PAGE_PATH = Path(__file__).with_name("web_assets") / "index.html"
PAGE = _PAGE_PATH.read_text(encoding="utf-8")


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
