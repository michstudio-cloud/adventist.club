#!/usr/bin/env python3
"""«Nodos vecinos»: nodes of the same event on one LAN see each other's progress without the
cloud (app/services/nodo_malla.py; nodo-evento/README.md «Nodos vecinos»). Runs in the node's
`malla` container (compose); the Mac app also calls the subcommands with
`docker compose exec malla python scripts/nodo_malla.py <subcomando> …`.

    servir [--puerto 8100] [--host 0.0.0.0] [--cada 30]
                       HTTP for the neighbours + loop: every N s reads $NODO_RUN_DIR/malla-vecinos.json,
                       pulls each neighbour's avance, applies it (read-only) and writes
                       $NODO_RUN_DIR/malla-estado.json
    identidad          this node's public identity (+ 6-digit fingerprint)
    estado             the same JSON as malla-estado.json, computed now
    tirar URL          one pull now from the neighbour at URL (http://192.168.8.151)
    emparejar-codigo   the pairing code to show as QR/text on THIS Mac
    emparejar-aceptar CODIGO
                       accept the code shown by the OTHER Mac (compare the fingerprint!)
    olvidar NODE_ID    stop trusting a neighbour (its rows already received stay, read-only)

Environment: NODE_DATABASE_URL (or DATABASE_URL), NODO_RUN_DIR (default /run-nodo),
MALLA_PUERTO (8100), MALLA_HOST (0.0.0.0), MALLA_CADA_SEG (30), EVENT_PASS_SECRET / JWT_SECRET
(the node's own secret: opens the node's signing key, like nodo_sync.py).

Output for the Mac app: «@@ok …», «@@aviso …», «@@error …» and, at the end, ONE line «@@json {…}».
Exit codes: 0 ok · 1 error · 3 refused (another event, overlap, bad code, unknown node).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", os.environ.get("NODE_DATABASE_URL") or "postgresql://unused@localhost/unused")

from app.services import nodo_crypto  # noqa: E402
from app.services import nodo_malla as malla  # noqa: E402
from app.services import nodo_malla_server as server  # noqa: E402
from app.services import nodo_sync_core as core  # noqa: E402


def say(*parts) -> None:
    print(*parts, flush=True)


def emit_json(data) -> None:
    say("@@json " + json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")))


def node_secret() -> str:
    from app.config import settings  # noqa: PLC0415

    secret = settings.EVENT_PASS_SECRET or settings.JWT_SECRET
    if not secret:
        raise core.SyncError("el nodo no tiene EVENT_PASS_SECRET / JWT_SECRET")
    return secret


async def _with_me(args, fn):
    conn = await core.connect(args.node_url)
    try:
        me_ = await malla.me(conn, node_secret())
        return await fn(conn, me_)
    finally:
        await conn.close()


async def cmd_identidad(args) -> int:
    async def run(conn, me_):
        data = {**malla.public_identity(me_), "huella": malla.huella(me_["sign_key"]),
                "vouch_key": bool(me_.get("vouch_key"))}
        say(f"@@ok Este nodo: «{me_['label']}» · huella {data['huella']}"
            + ("" if me_.get("voucher") else " · sin aval de la nube (emparejar a mano)"))
        emit_json(data)
    await _with_me(args, run)
    return 0


async def cmd_estado(args) -> int:
    async def run(conn, me_):
        emit_json(await malla.state(conn, me_, server.read_rejections(Path(args.run_dir))))
    await _with_me(args, run)
    return 0


async def cmd_tirar(args) -> int:
    result = await server.pull(args.node_url, node_secret(), args.url)
    rep = result["reporte"]
    say(f"@@ok «{result['label']}»: {rep['insertadas']} nuevas, {rep['actualizadas']} actualizadas, "
        f"{rep['sin_cambios']} sin cambios")
    for item in rep["traslapes"]:
        say(f"@@aviso traslape: {item['que']}")
    emit_json(result)
    return 0


async def cmd_codigo(args) -> int:
    async def run(conn, me_):
        code = malla.pairing_code(me_)
        say(f"@@ok Código de «{me_['label']}» · huella {malla.huella(me_['sign_key'])}")
        emit_json({"codigo": code, "huella": malla.huella(me_["sign_key"]), "node_id": me_["node_id"],
                   "label": me_["label"]})
    await _with_me(args, run)
    return 0


async def cmd_aceptar(args) -> int:
    async def run(conn, me_):
        peer = await malla.accept_pairing(conn, me_, args.codigo)
        say(f"@@ok Vecino «{peer['label']}» aceptado ({malla.SOURCE_ES[peer['source']]}) · huella "
            f"{malla.huella(peer['sign_key'])}: compárala con la de la otra Mac")
        emit_json({"node_id": peer["node_id"], "label": peer["label"], "origen": peer["source"],
                   "huella": malla.huella(peer["sign_key"]), "scope": peer["scope"]})
    await _with_me(args, run)
    return 0


async def cmd_olvidar(args) -> int:
    async def run(conn, me_):
        done = await malla.forget(conn, me_["event_id"], args.node_id)
        say("@@ok Vecino olvidado" if done else "@@aviso Ese vecino no se conocía")
        emit_json({"olvidado": done, "node_id": args.node_id})
    await _with_me(args, run)
    return 0


async def cmd_servir(args) -> int:
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    await server.serve(args.node_url, node_secret(), run_dir, host=args.host, port=args.puerto, every=args.cada)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nodos vecinos del evento (malla local).")
    parser.add_argument("--node-url", default=os.environ.get("NODE_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--run-dir", default=os.environ.get("NODO_RUN_DIR") or "/run-nodo")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("servir", help="servidor + ciclo de vecinos")
    s.add_argument("--puerto", type=int, default=int(os.environ.get("MALLA_PUERTO") or 8100))
    s.add_argument("--host", default=os.environ.get("MALLA_HOST") or "0.0.0.0")  # noqa: S104 (behind Caddy)
    s.add_argument("--cada", type=float, default=float(os.environ.get("MALLA_CADA_SEG") or 30))
    sub.add_parser("identidad")
    sub.add_parser("estado")
    t = sub.add_parser("tirar")
    t.add_argument("url")
    sub.add_parser("emparejar-codigo")
    a = sub.add_parser("emparejar-aceptar")
    a.add_argument("codigo")
    o = sub.add_parser("olvidar")
    o.add_argument("node_id")
    args = parser.parse_args(argv)
    if not args.node_url or "unused@localhost" in args.node_url:
        parser.error("falta NODE_DATABASE_URL")
    handler = {"servir": cmd_servir, "identidad": cmd_identidad, "estado": cmd_estado, "tirar": cmd_tirar,
               "emparejar-codigo": cmd_codigo, "emparejar-aceptar": cmd_aceptar, "olvidar": cmd_olvidar}[args.command]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 0
    except malla.MeshError as exc:
        say(f"@@error {exc}")
        emit_json({"error": str(exc), "codigo_http": exc.code})
        return 3 if exc.code in (403, 409, 422) else 1
    except (core.SyncError, nodo_crypto.CryptoError) as exc:
        say(f"@@error {exc}")
        emit_json({"error": str(exc)})
        return 1
    except (OSError, asyncpg.PostgresError) as exc:
        say(f"@@error ({type(exc).__name__}): {str(exc).splitlines()[0][:300] if str(exc) else ''}")
        emit_json({"error": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
