"""What each phone on the event node has been doing, for the Mac's «Dispositivos conectados» panel
(GET /api/v1/nodo/operador/dispositivos, docs/RATE_LIMITS.md).

In memory of this process only (the node runs ONE uvicorn worker), bounded and never persisted:
a restart starts empty, like the rate limiter. Active only on the node (LOGIN_LOCKOUT_MODE=device
or NODO_OPERADOR_TOKEN set); in production every call here is a no-op.
"""
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from starlette.requests import Request

from app.config import settings
from app.rate_limit import client_ip

MAX_DEVICES = 2000
RETENTION_SECONDS = 12 * 3600
FAILURE_WINDOW_SECONDS = 10 * 60
MAX_FAILURES_KEPT = 200
REQUEST_MINUTES_KEPT = 60
SUSPICIOUS_DISTINCT_EMAILS = 3


@dataclass
class _Device:
    first_seen: float
    last_seen: float
    # (email, name, when) of the last successful login from this address.
    account: tuple[str, str, float] | None = None
    failures: deque = field(default_factory=lambda: deque(maxlen=MAX_FAILURES_KEPT))  # (when, email)
    requests: dict[int, int] = field(default_factory=dict)  # minute -> count


_devices: "OrderedDict[str, _Device]" = OrderedDict()


def enabled() -> bool:
    return settings.login_lockout_by_device or settings.nodo_operador_token is not None


def _touch(ip: str, now: float) -> _Device:
    device = _devices.pop(ip, None)
    if device is None:
        device = _Device(first_seen=now, last_seen=now)
    device.last_seen = now
    _devices[ip] = device
    while len(_devices) > MAX_DEVICES:
        _devices.popitem(last=False)
    return device


def _prune(now: float) -> None:
    # Oldest first (least recently seen), so stop at the first one still within retention.
    while _devices:
        ip, device = next(iter(_devices.items()))
        if now - device.last_seen <= RETENTION_SECONDS:
            break
        _devices.pop(ip)


def record_request(ip: str) -> None:
    if not enabled():
        return
    now = time.time()
    device = _touch(ip, now)
    minute = int(now // 60)
    device.requests[minute] = device.requests.get(minute, 0) + 1
    if len(device.requests) > REQUEST_MINUTES_KEPT:
        for old in [m for m in device.requests if m <= minute - REQUEST_MINUTES_KEPT]:
            del device.requests[old]


def record_login_success(ip: str, email: str, name: str) -> None:
    if not enabled():
        return
    now = time.time()
    _touch(ip, now).account = (email.strip().lower(), name, now)


def record_login_failure(ip: str, email: str) -> None:
    if not enabled():
        return
    now = time.time()
    _touch(ip, now).failures.append((now, email.strip().lower()))


def _iso(when: float | None) -> str | None:
    return datetime.fromtimestamp(when, tz=timezone.utc).isoformat() if when else None


def snapshot(minutes: int, locked_until=None) -> list[dict]:
    """Addresses seen in the last `minutes`, most recent first. `locked_until(ip)` names when a
    device blocked by the per-address login limit may try again (or None)."""
    now = time.time()
    _prune(now)
    since = now - minutes * 60
    current_minute = int(now // 60)
    out = []
    for ip, device in reversed(_devices.items()):
        if device.last_seen < since:
            continue
        recent = [(at, email) for at, email in device.failures if at >= now - FAILURE_WINDOW_SECONDS]
        emails = sorted({email for _at, email in recent})
        request_minutes = min(minutes, REQUEST_MINUTES_KEPT)
        requests = sum(n for m, n in device.requests.items() if m > current_minute - request_minutes)
        last_five = sum(n for m, n in device.requests.items() if m > current_minute - 5)
        out.append({
            "ip": ip,
            "primera_vez": _iso(device.first_seen),
            "ultima_vez": _iso(device.last_seen),
            "cuenta": (
                {"email": device.account[0], "nombre": device.account[1], "a_las": _iso(device.account[2])}
                if device.account else None
            ),
            "fallos_10min": len(recent),
            "ultimo_fallo": _iso(device.failures[-1][0]) if device.failures else None,
            "correos_distintos": len(emails),
            "correos": emails if len(emails) > SUSPICIOUS_DISTINCT_EMAILS else None,
            "sospechoso": len(emails) > SUSPICIOUS_DISTINCT_EMAILS,
            "peticiones": requests,
            "peticiones_por_minuto": round(last_five / 5, 1),
            "bloqueado_hasta": locked_until(ip) if locked_until else None,
        })
    return out


def reset() -> None:
    """Tests only."""
    _devices.clear()


# Paths that are not a phone's activity: the container healthcheck and the operator's own calls.
_NOT_COUNTED = ("/api/v1/health", "/api/v1/nodo/operador/")


class DeviceTrackerMiddleware:
    """Counts each API request per client address (pure ASGI, a dict update per request; nothing
    at all when the tracker is off, i.e. in production)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and enabled():
            path = scope.get("path", "")
            if path.startswith("/api/") and not path.startswith(_NOT_COUNTED):
                record_request(client_ip(Request(scope)))
        await self.app(scope, receive, send)
