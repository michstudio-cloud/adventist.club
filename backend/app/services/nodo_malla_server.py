"""The «nodos vecinos» process of a node (`scripts/nodo_malla.py servir`, compose service `malla`).

  * HTTP (Starlette, served by uvicorn; Caddy forwards http://<ip-del-nodo>/nodo-malla/* here):
      GET  /nodo-malla/v1/hola     public identity
      POST /nodo-malla/v1/avance   signed request -> sealed + signed avance (nodo_malla)
      GET  /nodo-malla/v1/salud    "ok"
  * LOOP every MALLA_CADA_SEG seconds: reads <run dir>/malla-vecinos.json (written by the Mac app
    from its Bonjour discovery), pulls each neighbour's avance, applies it read-only and writes
    <run dir>/malla-estado.json (atomic) for the app.

One short database connection per request / pull: the process stays light and survives a
database restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

from app.services import nodo_crypto as nc
from app.services import nodo_malla as malla
from app.services import nodo_sync_core as core

PREFIX = "/nodo-malla/v1"
MAX_REQUEST_BYTES = 64 * 1024
PEERS_FILE = "malla-vecinos.json"
STATE_FILE = "malla-estado.json"


class Rejections:
    """Recent rejections (incoming and outgoing), newest kept, for the state file."""

    def __init__(self, limit: int = 30):
        self.limit = limit
        self.items: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def add(self, url: str, motivo: str) -> None:
        key = (url, motivo)
        self.items.pop(key, None)
        self.items[key] = {"url": url, "motivo": motivo, "cuando": int(time.time())}
        while len(self.items) > self.limit:
            self.items.popitem(last=False)

    def clear_url(self, url: str) -> None:
        for key in [k for k in self.items if k[0] == url]:
            self.items.pop(key, None)

    def list(self) -> list[dict]:
        return list(reversed(self.items.values()))


# --------------------------------------------------------------------------------------------
# HTTP client (stdlib; no proxies: neighbours are on the LAN)
# --------------------------------------------------------------------------------------------
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_sync(method: str, url: str, body: bytes | None, headers: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Accept": "application/json", "User-Agent": "nodo-malla/1", **headers})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (LAN peers the app discovered)
            data = resp.read(malla.MAX_ANSWER_BYTES + 1)
            status = resp.status
    except urllib.error.HTTPError as exc:
        data, status = exc.read(65536), exc.code
    if len(data) > malla.MAX_ANSWER_BYTES:
        raise malla.MeshError("respuesta demasiado grande", 502)
    try:
        return status, json.loads(data or b"{}")
    except ValueError:
        return status, {"detail": f"respuesta no JSON (HTTP {status})"}


async def http_default(method: str, url: str, body: bytes | None = None, headers: dict | None = None,
                       timeout: float = 30) -> tuple[int, dict]:
    try:
        return await asyncio.to_thread(_http_sync, method, url, body, headers or {}, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise malla.MeshError(f"no contesta ({type(exc).__name__})", 504) from exc


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    return url


async def pull(dsn: str, secret: str, url: str, *, http=http_default) -> dict:
    """One pull from the neighbour at `url`: who it is, its avance, applied here.
    -> {"url", "node_id", "label", "reporte"}. Raises MeshError (reason in Spanish)."""
    url = normalize_url(url)
    status, hola = await http("GET", f"{url}{PREFIX}/hola", timeout=10)
    if status != 200:
        raise malla.MeshError((hola or {}).get("detail") or f"no contesta como nodo (HTTP {status})", 502)
    conn = await core.connect(dsn)
    try:
        me_ = await malla.me(conn, secret)
        known = await malla.identify(conn, me_, hola, url)
        body, headers, eph_private = malla.build_request(me_)
        epk = json.loads(body)["epk"]
        try:
            status, answer = await http("POST", f"{url}{PREFIX}/avance", body, headers, timeout=120)
            if status != 200:
                raise malla.MeshError(f"el vecino no comparte su avance: {(answer or {}).get('detail') or status}",
                                      status if status in (403, 409) else 502)
            changes = malla.open_answer(me_, known, answer, eph_private, epk)
        except malla.MeshError as exc:
            await malla.record_error(conn, me_["event_id"], known["node_id"], str(exc))
            raise
        report = await malla.apply_avance(conn, me_, known, changes)
        return {"url": url, "node_id": known["node_id"], "label": known["label"], "reporte": report}
    finally:
        await conn.close()


# --------------------------------------------------------------------------------------------
# Files shared with the Mac app
# --------------------------------------------------------------------------------------------
def read_peers_file(run_dir: Path) -> list[str]:
    path = run_dir / PEERS_FILE
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (ValueError, OSError):
        return []
    urls = []
    for item in data if isinstance(data, list) else []:
        url = normalize_url(item.get("url") if isinstance(item, dict) else item if isinstance(item, str) else "")
        if url and url not in urls:
            urls.append(url)
    return urls


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=1))
    os.replace(tmp, path)


def read_rejections(run_dir: Path) -> list[dict]:
    try:
        return json.loads((run_dir / STATE_FILE).read_text()).get("rechazados") or []
    except (FileNotFoundError, ValueError, OSError, AttributeError):
        return []


async def write_state(dsn: str, secret: str, run_dir: Path, rejections: Rejections) -> dict:
    conn = await core.connect(dsn)
    try:
        try:
            me_ = await malla.me(conn, secret)
        except malla.MeshError as exc:
            data = {"actualizado": int(time.time()), "yo": None, "vecinos": [], "rechazados": rejections.list(),
                    "aviso": str(exc)}
        else:
            data = await malla.state(conn, me_, rejections.list())
    finally:
        await conn.close()
    write_json_atomic(run_dir / STATE_FILE, data)
    return data


async def cycle(dsn: str, secret: str, run_dir: Path, rejections: Rejections, *, http=http_default) -> dict:
    """One round: pull every neighbour in malla-vecinos.json, then write the state."""
    results = []
    for url in read_peers_file(run_dir):
        try:
            results.append(await pull(dsn, secret, url, http=http))
            rejections.clear_url(url)
        except malla.MeshError as exc:
            if str(exc) != "es este mismo nodo":
                rejections.add(url, str(exc))
        except (nc.CryptoError, core.SyncError) as exc:
            rejections.add(url, str(exc))
        except Exception as exc:  # noqa: BLE001 (one bad neighbour never stops the loop)
            rejections.add(url, f"error ({type(exc).__name__})")
    await write_state(dsn, secret, run_dir, rejections)
    return {"pulled": results}


# --------------------------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------------------------
def make_app(dsn: str, secret: str, rejections: Rejections | None = None):
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse, PlainTextResponse  # noqa: PLC0415
    from starlette.routing import Route  # noqa: PLC0415

    rejections = rejections if rejections is not None else Rejections()

    def _client(request: Request) -> str:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        host = forwarded or (request.client.host if request.client else "?")
        return f"http://{host}"

    async def hola(request: Request):
        conn = await core.connect(dsn)
        try:
            me_ = await malla.me(conn, secret)
            return JSONResponse(malla.public_identity(me_))
        except malla.MeshError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.code)
        finally:
            await conn.close()

    async def avance(request: Request):
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_REQUEST_BYTES:
            return JSONResponse({"detail": "petición demasiado grande"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_REQUEST_BYTES:
            return JSONResponse({"detail": "petición demasiado grande"}, status_code=413)
        conn = await core.connect(dsn)
        try:
            answer = await malla.answer_avance(conn, secret, body, request.headers.get("x-nodo-firma"))
            return JSONResponse(answer)
        except malla.MeshError as exc:
            if exc.code in (400, 403):
                rejections.add(_client(request), f"petición rechazada: {exc}")
            return JSONResponse({"detail": str(exc)}, status_code=exc.code)
        finally:
            await conn.close()

    async def salud(request: Request):
        return PlainTextResponse("ok")

    return Starlette(routes=[Route(f"{PREFIX}/hola", hola, methods=["GET"]),
                             Route(f"{PREFIX}/avance", avance, methods=["POST"]),
                             Route(f"{PREFIX}/salud", salud, methods=["GET"])])


async def serve(dsn: str, secret: str, run_dir: Path, *, host: str, port: int, every: float) -> None:
    import uvicorn  # noqa: PLC0415

    rejections = Rejections()
    for item in read_rejections(run_dir):
        if isinstance(item, dict) and item.get("url") and item.get("motivo"):
            rejections.items[(item["url"], item["motivo"])] = item
    app = make_app(dsn, secret, rejections)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                                           proxy_headers=True, forwarded_allow_ips="*"))

    async def loop():
        while not server.started:
            await asyncio.sleep(0.1)
        print(f"@@ok Nodos vecinos: escuchando en {host}:{port}, revisa vecinos cada {every:g} s", flush=True)
        while not server.should_exit:
            try:
                await cycle(dsn, secret, run_dir, rejections)
            except Exception as exc:  # noqa: BLE001 (the database may be restarting)
                print(f"@@aviso ciclo de vecinos: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            for _ in range(max(1, int(every * 10))):
                if server.should_exit:
                    break
                await asyncio.sleep(0.1)

    task = asyncio.create_task(loop())
    try:
        await server.serve()
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
