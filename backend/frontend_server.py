"""
Routing-aware static HTTP server for Sign2Text.

Routes when landing/dist/index.html exists:
  GET /             -> landing/dist/index.html     (landing home)
  GET /index.html   -> frontend/web/index.html         (main app, always)
  GET /admin.html   -> frontend/web/admin.html         (admin dashboard, always)
  GET /privacy.html -> frontend/web/privacy.html       (privacy policy, always)
  GET /terms.html   -> frontend/web/terms.html         (terms of use, always)
  GET /assets/...   -> landing/dist/assets/...         (Vite-bundled JS/CSS)
  GET /[SPA route]  -> landing/dist/index.html         (React Router catch-all)
  GET /[file]       -> frontend/web/[file]             (runtime-config.js, app.js, etc.)

Falls back to plain frontend/web/ serving when landing/dist/index.html is absent:
  GET /             -> frontend/web/index.html
  GET /[file]       -> frontend/web/[file]

Usage (same CLI interface as http.server):
  python -m backend.frontend_server [PORT] [--bind HOST]
"""

from __future__ import annotations

import argparse
import mimetypes
import posixpath
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend" / "web"
LANDING_DIST = ROOT / "landing" / "dist"


def _safe_resolve(base: Path, url_path: str) -> Path | None:
    """
    Resolve a URL path relative to base, with path traversal protection.

    Returns None (→ 403) if:
    - The decoded path contains backslashes or null bytes
    - The resolved path escapes base after normalization
    """
    decoded = urllib.parse.unquote(url_path)
    # Reject Windows path separators (from %5c) and null bytes
    if "\\" in decoded or "\x00" in decoded:
        return None
    # posixpath.normpath collapses all .. relative to / — safe on all platforms
    normalized = posixpath.normpath(decoded)
    rel = normalized.lstrip("/")
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
        return candidate
    except ValueError:
        return None


class FrontendHandler(BaseHTTPRequestHandler):
    landing_active: bool = False  # set once at process start

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # start.py streams all stdout; suppress per-request lines

    def do_GET(self) -> None:  # noqa: N802
        url_path = urllib.parse.urlparse(self.path).path
        url_path = posixpath.normpath(urllib.parse.unquote(url_path))
        if not url_path.startswith("/"):
            url_path = "/" + url_path

        # /index.html always serves the main app — never overridden by landing
        if url_path == "/index.html":
            self._serve_from(FRONTEND_DIR, url_path)
            return

        # /admin.html always serves from frontend/ — never caught by SPA routing
        if url_path == "/admin.html":
            self._serve_from(FRONTEND_DIR, url_path)
            return

        # Legal pages always serve from frontend/ — never caught by SPA routing
        if url_path in ("/privacy.html", "/terms.html"):
            self._serve_from(FRONTEND_DIR, url_path)
            return

        if self.landing_active:
            # / → landing home
            if url_path == "/":
                self._send_file(LANDING_DIST / "index.html")
                return

            # Files present in landing/dist (assets/, favicon.png, …)
            landing_file = _safe_resolve(LANDING_DIST, url_path)
            if landing_file and landing_file.is_file():
                self._send_file(landing_file)
                return

            # frontend/ static files (runtime-config.js, app.js, style.css, …)
            root_file = _safe_resolve(FRONTEND_DIR, url_path)
            if root_file and root_file.is_file():
                self._send_file(root_file)
                return

            # SPA catch-all: /about, /contribute, any unknown route → landing
            self._send_file(LANDING_DIST / "index.html")
            return

        # Landing not present — serve from frontend/
        if url_path == "/":
            self._serve_from(FRONTEND_DIR, "/index.html")
            return
        self._serve_from(FRONTEND_DIR, url_path)

    def _serve_from(self, base: Path, url_path: str) -> None:
        candidate = _safe_resolve(base, url_path)
        if candidate is None:
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        self._send_file(candidate)

    def _send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(path))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign2Text frontend static server")
    parser.add_argument("port", type=int, nargs="?", default=5500)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    landing_index = LANDING_DIST / "index.html"
    FrontendHandler.landing_active = landing_index.exists()
    mode = "landing + app" if FrontendHandler.landing_active else "app only"
    print(
        f"Serving HTTP on {args.bind} port {args.port} ({mode}) ...",
        flush=True,
    )

    server = ThreadingHTTPServer((args.bind, args.port), FrontendHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
