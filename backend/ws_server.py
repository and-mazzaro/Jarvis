"""
WebSocket server (port 8765) + aiohttp static HTTP server (port 3000).

Broadcasts JSON state messages to all connected WebSocket clients, and also
exposes a small REST API used by the Electron/Knowledge Manager UI:

  GET  /api/status          — heartbeat + component status
  GET  /api/kiwix/status    — is kiwix-serve online?
  POST /api/ingest          — body: { "file_path": "..." }
  GET  /api/knowledge       — list of indexed files
  POST /api/knowledge/delete — body: { "file_name": "..." }
  POST /api/kiwix/search    — body: { "query": "..." }
"""
import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Optional

import websockets
try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:
    from websockets import serve as ws_serve
from aiohttp import web

logger = logging.getLogger("jarvis.ws_server")

# Injected by main.py via run_servers()
_ingestor = None
_retriever = None
_kiwix_client = None
_frontend_dist: Path = Path(__file__).parent.parent / "frontend" / "dist"


# --------------------------------------------------------------------------- #
# CORS helper
# --------------------------------------------------------------------------- #

def _cors(response: web.Response) -> web.Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def _handle_options(request: web.Request) -> web.Response:
    """Handle CORS preflight."""
    return _cors(web.Response(status=204))


# --------------------------------------------------------------------------- #
# WebSocket broadcast
# --------------------------------------------------------------------------- #

# websockets ≥ 12 uses websockets.asyncio.server.ServerConnection
_connected_clients: set = set()


async def broadcast(state: str, extra: Optional[dict] = None) -> None:
    """Send a state-change JSON message to all connected WebSocket clients."""
    msg: dict = {"state": state}
    if extra:
        msg.update(extra)
    payload = json.dumps(msg)
    if not _connected_clients:
        return
    await asyncio.gather(
        *[ws.send(payload) for ws in list(_connected_clients)],
        return_exceptions=True,
    )


async def _ws_handler(websocket) -> None:
    _connected_clients.add(websocket)
    logger.info("WS client connected  — %d total", len(_connected_clients))
    try:
        await websocket.wait_closed()
    finally:
        _connected_clients.discard(websocket)
        logger.info("WS client disconnected — %d total", len(_connected_clients))


# --------------------------------------------------------------------------- #
# HTTP REST API handlers
# --------------------------------------------------------------------------- #

async def _handle_status(request: web.Request) -> web.Response:
    kiwix_ok = False
    if _kiwix_client is not None:
        loop = asyncio.get_running_loop()
        kiwix_ok = await loop.run_in_executor(None, _kiwix_client.is_alive)
    return _cors(web.json_response({
        "status": "ok",
        "kiwix": kiwix_ok,
        "rag_docs": _ingestor.list_files().__len__() if _ingestor else 0,
    }))


async def _handle_kiwix_status(request: web.Request) -> web.Response:
    if _kiwix_client is None:
        return _cors(web.json_response({"online": False, "reason": "not initialised"}))
    loop = asyncio.get_running_loop()
    online = await loop.run_in_executor(None, _kiwix_client.is_alive)
    return _cors(web.json_response({"online": online}))


async def _handle_ingest(request: web.Request) -> web.Response:
    if _ingestor is None:
        return _cors(web.json_response({"error": "Ingestor not initialised"}, status=503))
    try:
        body = await request.json()
        file_path = body.get("file_path", "").strip()
        if not file_path:
            return _cors(web.json_response({"error": "file_path required"}, status=400))
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _ingestor.ingest_file, file_path)
        return _cors(web.json_response(result))
    except Exception as exc:
        logger.error("Ingest error: %s", exc, exc_info=True)
        return _cors(web.json_response({"error": str(exc)}, status=500))


async def _handle_knowledge(request: web.Request) -> web.Response:
    if _ingestor is None:
        return _cors(web.json_response([]))
    loop = asyncio.get_running_loop()
    files = await loop.run_in_executor(None, _ingestor.list_files)
    return _cors(web.json_response(files))


async def _handle_delete_file(request: web.Request) -> web.Response:
    if _ingestor is None:
        return _cors(web.json_response({"error": "Ingestor not initialised"}, status=503))
    try:
        body = await request.json()
        file_name = body.get("file_name", "").strip()
        if not file_name:
            return _cors(web.json_response({"error": "file_name required"}, status=400))
        loop = asyncio.get_running_loop()
        deleted = await loop.run_in_executor(None, _ingestor.delete_file, file_name)
        return _cors(web.json_response({"deleted_chunks": deleted}))
    except Exception as exc:
        logger.error("Delete error: %s", exc, exc_info=True)
        return _cors(web.json_response({"error": str(exc)}, status=500))


async def _handle_kiwix_search(request: web.Request) -> web.Response:
    if _kiwix_client is None:
        return _cors(web.json_response({"error": "Kiwix not initialised"}, status=503))
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        if not query:
            return _cors(web.json_response({"error": "query required"}, status=400))
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _kiwix_client.search, query)
        return _cors(web.json_response({"query": query, "excerpt": text}))
    except Exception as exc:
        logger.error("Kiwix search error: %s", exc, exc_info=True)
        return _cors(web.json_response({"error": str(exc)}, status=500))


async def _handle_static(request: web.Request) -> web.Response:
    """Serve frontend/dist/ as static files."""
    path = request.match_info.get("path", "") or "index.html"
    if path in ("", "/"):
        path = "index.html"
    file_path = _frontend_dist / path.lstrip("/")
    if not file_path.exists() or not file_path.is_file():
        file_path = _frontend_dist / "index.html"
    mime, _ = mimetypes.guess_type(str(file_path))
    return web.Response(
        body=file_path.read_bytes(),
        content_type=mime or "application/octet-stream",
    )


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #

def make_app() -> web.Application:
    app = web.Application()
    # CORS preflight
    app.router.add_route("OPTIONS", "/{path:.*}", _handle_options)
    # API routes
    app.router.add_get("/api/status",           _handle_status)
    app.router.add_get("/api/kiwix/status",     _handle_kiwix_status)
    app.router.add_post("/api/ingest",          _handle_ingest)
    app.router.add_get("/api/knowledge",        _handle_knowledge)
    app.router.add_post("/api/knowledge/delete",_handle_delete_file)
    app.router.add_post("/api/kiwix/search",    _handle_kiwix_search)
    # Static fallback
    app.router.add_get("/",          _handle_static)
    app.router.add_get("/{path:.*}", _handle_static)
    return app


# --------------------------------------------------------------------------- #
# Entry-point
# --------------------------------------------------------------------------- #

async def run_servers(
    ws_port: int,
    http_port: int,
    ingestor,
    retriever,
    kiwix_client,
    frontend_dist: Path,
) -> None:
    global _ingestor, _retriever, _kiwix_client, _frontend_dist
    _ingestor      = ingestor
    _retriever     = retriever
    _kiwix_client  = kiwix_client
    _frontend_dist = frontend_dist

    # HTTP server
    app    = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", http_port)
    await site.start()
    logger.info("HTTP server  →  http://localhost:%d", http_port)

    # WebSocket server (websockets ≥ 12 async API)
    async with ws_serve(_ws_handler, "localhost", ws_port):
        logger.info("WebSocket server  →  ws://localhost:%d", ws_port)
        await asyncio.Future()   # run forever
