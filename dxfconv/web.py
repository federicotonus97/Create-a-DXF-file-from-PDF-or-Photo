"""Local web page to try the converter: ``python -m dxfconv.web``.

Serves ``static/index.html`` and a ``POST /api/convert`` endpoint that runs the
same conversion as the command line and returns the DXF plus a preview.
Only binds to localhost by default: it is a testing tool, not a public service.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .cli import build_parser, convert
from .dxf_writer import write_dxf_r12

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_UPLOAD = 100 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# JSON option name -> (CLI flag, takes a value)
OPTIONS = {
    "scale": ("--scale", True),
    "calibrate": ("--calibrate", True),
    "page": ("--page", True),
    "raster": ("--raster", False),
    "raster_dpi": ("--raster-dpi", True),
    "no_circles": ("--no-circles", False),
    "dpi": ("--dpi", True),
    "px_per_mm": ("--px-per-mm", True),
    "ref": ("--ref", True),
    "ref_length": ("--ref-length", True),
    "sheet": ("--sheet", True),
    "corners": ("--corners", True),
    "mode": ("--mode", True),
    "threshold": ("--threshold", True),
    "invert": ("--invert", False),
    "tolerance": ("--tolerance", True),
    "min_length": ("--min-length", True),
    "margin": ("--margin", True),
    "resolution": ("--resolution", True),
}


def options_to_argv(options: dict) -> list[str]:
    argv: list[str] = []
    for key, value in options.items():
        if key not in OPTIONS or value in (None, "", False):
            continue
        flag, has_value = OPTIONS[key]
        argv.append(flag)
        if has_value:
            argv.append(str(value))
    return argv


def convert_upload(filename: str, data: bytes, options: dict) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Formato non supportato: {ext or filename}")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input" + ext)
        out = os.path.join(tmp, "output.dxf")
        with open(src, "wb") as f:
            f.write(data)
        args = build_parser().parse_args([src, *options_to_argv(options)])
        drawing, method = convert(args)
        write_dxf_r12(drawing, out)
        with open(out, encoding="ascii", errors="replace") as f:
            dxf = f.read()
    xmin, ymin, xmax, ymax = drawing.bounds()
    return {
        "ok": True,
        "method": method,
        "bounds": [xmin, ymin, xmax, ymax],
        "polylines": [
            {"points": [[round(x, 3), round(y, 3)] for x, y in p.points], "closed": p.closed}
            for p in drawing.polylines
        ],
        "circles": [
            {"center": [round(c.center[0], 3), round(c.center[1], 3)], "radius": round(c.radius, 3)}
            for c in drawing.circles
        ],
        "dxf": dxf,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "dxfconv"

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8")
        elif path == "/api/ping":
            self._json(HTTPStatus.OK, {"ok": True, "app": "dxfconv"})
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/convert":
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        query = parse_qs(url.query)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "File mancante o troppo grande"})
            return
        data = self.rfile.read(length)
        try:
            filename = query.get("filename", [""])[0]
            options = json.loads(query.get("options", ["{}"])[0])
            result = convert_upload(filename, data, options)
        except SystemExit as exc:  # argparse / CLI validation errors
            msg = str(exc) if exc.code not in (None, 0, 2) else "Opzioni non valide"
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": msg})
            return
        except Exception as exc:  # noqa: BLE001 - report any failure to the page
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self._json(HTTPStatus.OK, result)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _send(self, status, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="Pagina web locale per provare dxfconv")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    args = p.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"dxfconv web: {url}  (Ctrl+C per uscire)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
