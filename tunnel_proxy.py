"""
Reverse proxy that consolidates all Sign2Text services behind a single port
so that a single Cloudflare Quick Tunnel (or public host binding) can expose
them all.

Routing:
  /asl-api/*     → http://127.0.0.1:8000/*   (ASL backend, prefix stripped)
  /voice-api/*   → http://127.0.0.1:8001/*   (Voice backend, prefix stripped)
  /letter-api/*  → http://127.0.0.1:8002/*   (Letter backend, prefix stripped)
  /admin-api/*   → http://127.0.0.1:8003/*   (Admin backend, prefix stripped;
                                               token-auth enforced by the service)
  /*             → http://127.0.0.1:5500/*   (Frontend static files, catch-all)

WebSocket:
  /voice-api/ws/stt    → ws://127.0.0.1:8001/ws/stt
  /asl-api/ws/frames   → ws://127.0.0.1:8000/ws/frames

Usage:
  python tunnel_proxy.py [--port 8080] [--host 127.0.0.1]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from starlette.responses import Response, StreamingResponse

logger = logging.getLogger("tunnel_proxy")


# Suppress uvicorn access logs for high-volume streaming endpoints. Each
# letter-mode JPEG upload (~1 fps from mobile, ~6 fps from web) and each
# WebRTC frame post would otherwise produce a `INFO: ... 204 No Content`
# line, drowning out the actually-useful logs. The endpoints stay
# functional; only the per-request access-log line is hidden.
class _SuppressNoisyAccessLogs(logging.Filter):
    _SUPPRESSED = (
        "/letter-api/upload_frame",
        "/asl-api/upload_frame",
        "/upload_frame",
        "/keypoints",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(p in msg for p in self._SUPPRESSED)


logging.getLogger("uvicorn.access").addFilter(_SuppressNoisyAccessLogs())

FRONTEND_UPSTREAM = "http://127.0.0.1:5500"
ASL_UPSTREAM = "http://127.0.0.1:8000"
VOICE_UPSTREAM = "http://127.0.0.1:8001"
LETTER_UPSTREAM = "http://127.0.0.1:8002"
ADMIN_UPSTREAM = "http://127.0.0.1:8003"

app = FastAPI(title="Sign2Text Tunnel Proxy", docs_url=None, redoc_url=None)

_http_client: Optional[httpx.AsyncClient] = None

# Headers that must not be forwarded between hops.
_HOP_BY_HOP = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
    }
)


@app.on_event("startup")
async def _startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(
        # read=None allows infinite streaming (SSE, MJPEG).
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _http_client:
        await _http_client.aclose()


# ── helpers ──────────────────────────────────────────────────────────────────


def _forwarded_headers(raw_headers: dict) -> dict:
    """Return request headers with hop-by-hop entries removed."""
    return {k: v for k, v in raw_headers.items() if k.lower() not in _HOP_BY_HOP}


async def _proxy_http(
    request: Request,
    upstream_base: str,
    path: str,
) -> Response:
    """Stream an HTTP request/response to/from an upstream service."""
    assert _http_client is not None

    url = f"{upstream_base}/{path}"
    if request.query_params:
        url = f"{url}?{request.query_params}"

    headers = _forwarded_headers(dict(request.headers))
    # Add standard proxy headers so upstream services know the original origin
    headers["X-Forwarded-Host"] = request.headers.get("host", "")
    headers["X-Forwarded-Proto"] = "https" if request.url.scheme == "https" else request.headers.get("x-forwarded-proto", request.url.scheme)
    headers["X-Forwarded-For"] = request.client.host if request.client else ""
    body = await request.body()

    try:
        upstream_req = _http_client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if body else None,
        )
        upstream_resp = await _http_client.send(upstream_req, stream=True)
    except httpx.ConnectError:
        return Response(
            content=b"Upstream service unavailable",
            status_code=502,
            media_type="text/plain",
        )

    resp_headers = {
        k: v
        for k, v in upstream_resp.headers.multi_items()
        if k.lower() not in _HOP_BY_HOP
    }

    async def _stream():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        _stream(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
    )


# ── WebSocket proxy ─────────────────────────────────────────────────────────


@app.websocket("/voice-api/ws/stt")
async def ws_stt_proxy(ws: WebSocket) -> None:
    """Bidirectional WebSocket relay for the Voice STT endpoint."""
    await ws.accept()

    upstream_url = "ws://127.0.0.1:8001/ws/stt"

    # Forward cookies from the handshake so session auth works.
    extra_headers: dict = {}
    if ws.cookies:
        extra_headers["Cookie"] = "; ".join(
            f"{k}={v}" for k, v in ws.cookies.items()
        )

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=extra_headers,
        ) as upstream_ws:

            async def _client_to_upstream():
                try:
                    while True:
                        data = await ws.receive()
                        msg_type = data.get("type", "")
                        if msg_type == "websocket.receive":
                            if "text" in data and data["text"] is not None:
                                await upstream_ws.send(data["text"])
                            elif "bytes" in data and data["bytes"] is not None:
                                await upstream_ws.send(data["bytes"])
                        elif msg_type == "websocket.disconnect":
                            break
                except WebSocketDisconnect:
                    pass

            async def _upstream_to_client():
                try:
                    async for message in upstream_ws:
                        if isinstance(message, str):
                            await ws.send_text(message)
                        else:
                            await ws.send_bytes(message)
                except websockets.ConnectionClosed:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(_client_to_upstream()),
                    asyncio.create_task(_upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception:
        logger.debug("WebSocket proxy error", exc_info=True)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.websocket("/asl-api/ws/frames")
async def ws_frames_proxy(ws: WebSocket) -> None:
    """Unidirectional WebSocket relay: browser camera frames → ASL backend."""
    await ws.accept()

    upstream_url = "ws://127.0.0.1:8000/ws/frames"

    # Forward cookies from the handshake so session auth works.
    extra_headers: dict = {}
    if ws.cookies:
        extra_headers["Cookie"] = "; ".join(
            f"{k}={v}" for k, v in ws.cookies.items()
        )

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=extra_headers,
        ) as upstream_ws:

            async def _client_to_upstream():
                try:
                    while True:
                        data = await ws.receive()
                        msg_type = data.get("type", "")
                        if msg_type == "websocket.receive":
                            if "bytes" in data and data["bytes"] is not None:
                                await upstream_ws.send(data["bytes"])
                        elif msg_type == "websocket.disconnect":
                            break
                except WebSocketDisconnect:
                    pass

            async def _upstream_to_client():
                try:
                    async for message in upstream_ws:
                        if isinstance(message, str):
                            await ws.send_text(message)
                        else:
                            await ws.send_bytes(message)
                except websockets.ConnectionClosed:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(_client_to_upstream()),
                    asyncio.create_task(_upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception:
        logger.debug("WebSocket frames proxy error", exc_info=True)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── HTTP routes (order matters: specific prefixes before catch-all) ──────────

_ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]


@app.api_route(
    "/asl-api/{path:path}",
    methods=_ALL_METHODS,
)
async def proxy_asl(request: Request, path: str) -> Response:
    return await _proxy_http(request, ASL_UPSTREAM, path)


@app.api_route(
    "/voice-api/{path:path}",
    methods=_ALL_METHODS,
)
async def proxy_voice(request: Request, path: str) -> Response:
    return await _proxy_http(request, VOICE_UPSTREAM, path)


@app.api_route(
    "/letter-api/{path:path}",
    methods=_ALL_METHODS,
)
async def proxy_letter(request: Request, path: str) -> Response:
    return await _proxy_http(request, LETTER_UPSTREAM, path)


# Admin API is proxied so it is reachable in container deployments where only
# port 8080 is exposed.  The admin_server enforces token authentication on all
# write endpoints; this proxy layer adds no additional access controls.
@app.api_route(
    "/admin-api/{path:path}",
    methods=_ALL_METHODS,
)
async def proxy_admin(request: Request, path: str) -> Response:
    return await _proxy_http(request, ADMIN_UPSTREAM, path)


# Frontend catch-all — MUST be registered last.
@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD"],
)
async def proxy_frontend(request: Request, path: str) -> Response:
    return await _proxy_http(request, FRONTEND_UPSTREAM, path)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sign2Text tunnel reverse proxy")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
