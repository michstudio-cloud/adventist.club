"""TLS certificate for the event node's hostnames (030), obtained by the API, not by the Mac.

When a node is prepared, the bundle carries a real Let's Encrypt certificate (+ key) for
events/api/admin.adventist.club so the node serves the same origin with a trusted certificate
and the Mac needs no Cloudflare token. The API obtains it by ACME DNS-01 on Cloudflare
(`CLOUDFLARE_DNS_API_TOKEN`, `LE_EMAIL`), caches it in `node_tls_certs` (private key encrypted
with a key derived from NODO_SYNC_SECRET / JWT_SECRET) and reuses it while it has more than
`RENEW_BEFORE_DAYS` left. Not configured or failing → the bundle says so and the Mac falls back
to its local CA.

The ACME v2 client below is deliberately small and pure Python (RFC 8555: JWS ES256 with
`cryptography`, HTTP with urllib): no extra dependency on Render. Every network call goes
through an injectable `http` callable, so tests drive it against a fake ACME server and a fake
Cloudflare (never the real Let's Encrypt).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from app.config import settings
from app.services import nodo_crypto

logger = logging.getLogger(__name__)

LE_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
DOH = "https://cloudflare-dns.com/dns-query"
RENEW_BEFORE_DAYS = 30
PURPOSE = "nodo-cert-key"


class CertError(Exception):
    """Safe to show (no token, no key)."""


# --------------------------------------------------------------------------------------------
# HTTP (injectable)
# --------------------------------------------------------------------------------------------
def urllib_http(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
                timeout: float = 30) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# --------------------------------------------------------------------------------------------
# Cloudflare DNS
# --------------------------------------------------------------------------------------------
class Cloudflare:
    def __init__(self, token: str, http=urllib_http):
        self._token, self._http = token, http

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        status, _, body = self._http(method, f"{CLOUDFLARE_API}{path}", headers,
                                     json.dumps(payload).encode() if payload is not None else None)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        if status >= 400 or not data.get("success", False):
            errors = "; ".join(str(e.get("message", e)) for e in data.get("errors", [])) or f"HTTP {status}"
            raise CertError(f"Cloudflare rechazó la operación ({errors})")
        return data

    def zone_id(self, hostname: str) -> str:
        labels = hostname.split(".")
        for i in range(len(labels) - 1):
            name = ".".join(labels[i:])
            result = self._call("GET", f"/zones?name={urllib.parse.quote(name)}")["result"]
            if result:
                return result[0]["id"]
        raise CertError(f"El token de Cloudflare no ve la zona de {hostname}")

    def add_txt(self, zone: str, name: str, content: str) -> str:
        data = self._call("POST", f"/zones/{zone}/dns_records",
                          {"type": "TXT", "name": name, "content": content, "ttl": 60})
        return data["result"]["id"]

    def delete(self, zone: str, record_id: str) -> None:
        try:
            self._call("DELETE", f"/zones/{zone}/dns_records/{record_id}")
        except CertError:
            logger.warning("could not delete the ACME TXT record %s", record_id)


def doh_txt(name: str, http=urllib_http) -> set[str]:
    """TXT values of `name` through DNS-over-HTTPS (no DNS library needed)."""
    status, _, body = http("GET", f"{DOH}?name={urllib.parse.quote(name)}&type=TXT",
                           {"Accept": "application/dns-json"}, None)
    if status != 200:
        return set()
    try:
        answers = json.loads(body).get("Answer") or []
    except ValueError:
        return set()
    return {a.get("data", "").strip('"') for a in answers}


# --------------------------------------------------------------------------------------------
# ACME v2
# --------------------------------------------------------------------------------------------
class Acme:
    def __init__(self, directory_url: str, http=urllib_http, sleep=time.sleep, poll_seconds: float = 3.0):
        self._http, self._sleep, self.poll_seconds = http, sleep, poll_seconds
        status, _, body = http("GET", directory_url, {}, None)
        if status != 200:
            raise CertError(f"Let's Encrypt no respondió (HTTP {status})")
        self.directory = json.loads(body)
        self.key = ec.generate_private_key(ec.SECP256R1())
        numbers = self.key.public_key().public_numbers()
        self.jwk = {"crv": "P-256", "kty": "EC", "x": _b64u(numbers.x.to_bytes(32, "big")),
                    "y": _b64u(numbers.y.to_bytes(32, "big"))}
        self.thumbprint = _b64u(hashlib.sha256(
            json.dumps(self.jwk, sort_keys=True, separators=(",", ":")).encode()).digest())
        self.kid: str | None = None
        self._nonce: str | None = None

    def _new_nonce(self) -> str:
        _, headers, _ = self._http("HEAD", self.directory["newNonce"], {}, None)
        nonce = headers.get("replay-nonce")
        if not nonce:
            raise CertError("Let's Encrypt no dio un nonce")
        return nonce

    def _post(self, url: str, payload, *, retry: bool = True) -> tuple[int, dict, bytes]:
        protected = {"alg": "ES256", "nonce": self._nonce or self._new_nonce(), "url": url}
        if self.kid:
            protected["kid"] = self.kid
        else:
            protected["jwk"] = self.jwk
        p64 = _b64u(json.dumps(protected).encode())
        b64 = "" if payload is None else _b64u(json.dumps(payload).encode())
        der = self.key.sign(f"{p64}.{b64}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        body = json.dumps({"protected": p64, "payload": b64, "signature": signature}).encode()
        status, headers, data = self._http("POST", url, {"Content-Type": "application/jose+json"}, body)
        self._nonce = headers.get("replay-nonce")
        if status == 400 and retry and b"badNonce" in data:
            self._nonce = None
            return self._post(url, payload, retry=False)
        if status >= 400:
            try:
                detail = json.loads(data).get("detail", "")
            except ValueError:
                detail = ""
            raise CertError(f"Let's Encrypt respondió {status}: {detail}"[:300])
        return status, headers, data

    def register(self, email: str | None) -> None:
        payload = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        _, headers, _ = self._post(self.directory["newAccount"], payload)
        self.kid = headers.get("location")
        if not self.kid:
            raise CertError("Let's Encrypt no devolvió la cuenta")

    def _poll(self, url: str, until: set[str], fail: set[str], attempts: int = 40) -> dict:
        for _ in range(attempts):
            _, _, data = self._post(url, None)
            doc = json.loads(data)
            if doc.get("status") in until:
                return doc
            if doc.get("status") in fail:
                problem = doc.get("error") or next((c.get("error") for c in doc.get("challenges", [])
                                                     if c.get("error")), {})
                raise CertError(f"Let's Encrypt: {doc.get('status')} {problem.get('detail', '') if problem else ''}"[:300])
            self._sleep(self.poll_seconds)
        raise CertError("Let's Encrypt tardó demasiado")

    def issue(self, names: list[str], dns: Cloudflare, *, resolver=doh_txt, propagation_attempts: int = 40) -> tuple[str, bytes]:
        """-> (fullchain PEM, private key PEM)."""
        _, headers, data = self._post(self.directory["newOrder"],
                                      {"identifiers": [{"type": "dns", "value": n} for n in names]})
        order, order_url = json.loads(data), headers.get("location")
        zone = dns.zone_id(names[0])
        created, pending = [], []
        try:
            for authz_url in order["authorizations"]:
                _, _, data = self._post(authz_url, None)
                authz = json.loads(data)
                if authz.get("status") == "valid":
                    continue
                challenge = next(c for c in authz["challenges"] if c["type"] == "dns-01")
                value = _b64u(hashlib.sha256(f"{challenge['token']}.{self.thumbprint}".encode()).digest())
                name = f"_acme-challenge.{authz['identifier']['value']}"
                created.append(dns.add_txt(zone, name, value))
                pending.append((authz_url, challenge["url"], name, value))
            for _, _, name, value in pending:
                for _ in range(propagation_attempts):
                    if value in resolver(name):
                        break
                    self._sleep(self.poll_seconds)
                else:
                    raise CertError(f"El registro DNS de {name} no se propagó a tiempo")
            for authz_url, challenge_url, _, _ in pending:
                self._post(challenge_url, {})
                self._poll(authz_url, {"valid"}, {"invalid", "revoked", "deactivated", "expired"})
            key = ec.generate_private_key(ec.SECP256R1())
            csr = (x509.CertificateSigningRequestBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
                   .sign(key, hashes.SHA256()))
            self._post(order["finalize"], {"csr": _b64u(csr.public_bytes(serialization.Encoding.DER))})
            done = self._poll(order_url, {"valid"}, {"invalid"})
            _, _, chain = self._post(done["certificate"], None)
            key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            return chain.decode(), key_pem
        finally:
            for record_id in created:
                dns.delete(zone, record_id)


def obtain(names: list[str], *, email: str, token: str, staging: bool, http=urllib_http, sleep=time.sleep,
           resolver=None, poll_seconds: float = 3.0) -> tuple[str, bytes]:
    acme = Acme(LE_STAGING if staging else LE_PRODUCTION, http=http, sleep=sleep, poll_seconds=poll_seconds)
    acme.register(email)
    return acme.issue(names, Cloudflare(token, http=http),
                      resolver=resolver or (lambda name: doh_txt(name, http=http)))


def not_after_of(pem: str) -> datetime:
    cert = x509.load_pem_x509_certificate(pem.encode())
    return cert.not_valid_after_utc


# --------------------------------------------------------------------------------------------
# Cache (node_tls_certs)
# --------------------------------------------------------------------------------------------
def _key_names(names: list[str]) -> str:
    return ",".join(sorted(names))


async def cached(conn, names: list[str], staging: bool) -> dict | None:
    row = await conn.fetchrow("SELECT fullchain_pem, privkey_enc, not_after FROM node_tls_certs"
                              " WHERE names = $1 AND staging = $2", _key_names(names), staging)
    if row is None:
        return None
    return dict(row)


def fresh(not_after: datetime, now: datetime | None = None) -> bool:
    return not_after - (now or datetime.now(timezone.utc)) > timedelta(days=RENEW_BEFORE_DAYS)


async def certificate_for_node(conn, *, obtain_fn=None, now: datetime | None = None) -> dict:
    """{"status": "ok", "fullchain_pem", "privkey_pem", "not_after", "names", "staging", "cached"}
    or {"status": "no_configurado" | "error", "message"}. Never raises."""
    names = settings.nodo_hostnames
    if not settings.nodo_certificates_configured:
        return {"status": "no_configurado", "names": names,
                "message": "El API no tiene configurado Cloudflare/Let's Encrypt: usa el certificado local del nodo"}
    secret = settings.nodo_sync_secret
    staging = bool(settings.LE_STAGING)
    try:
        row = await cached(conn, names, staging)
        if row is not None and fresh(row["not_after"], now):
            key = nodo_crypto.unbox(secret, PURPOSE, row["privkey_enc"])
            return {"status": "ok", "fullchain_pem": row["fullchain_pem"], "privkey_pem": key.decode(),
                    "not_after": row["not_after"].isoformat(), "names": names, "staging": staging, "cached": True}
        # One issuance at a time across API instances.
        await conn.execute("SELECT pg_advisory_lock(hashtext('node_tls_certs'))")
        try:
            row = await cached(conn, names, staging)
            if row is not None and fresh(row["not_after"], now):
                key = nodo_crypto.unbox(secret, PURPOSE, row["privkey_enc"])
                return {"status": "ok", "fullchain_pem": row["fullchain_pem"], "privkey_pem": key.decode(),
                        "not_after": row["not_after"].isoformat(), "names": names, "staging": staging,
                        "cached": True}
            fn = obtain_fn or obtain
            chain, key_pem = await asyncio.to_thread(
                fn, names, email=settings.LE_EMAIL, token=settings.CLOUDFLARE_DNS_API_TOKEN, staging=staging)
            not_after = not_after_of(chain)
            await conn.execute(
                "INSERT INTO node_tls_certs (id, names, staging, fullchain_pem, privkey_enc, not_after)"
                " VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (names, staging) DO UPDATE SET"
                " fullchain_pem = EXCLUDED.fullchain_pem, privkey_enc = EXCLUDED.privkey_enc,"
                " not_after = EXCLUDED.not_after, created_at = now()",
                uuid.uuid4(), _key_names(names), staging, chain, nodo_crypto.box(secret, PURPOSE, key_pem), not_after)
            return {"status": "ok", "fullchain_pem": chain, "privkey_pem": key_pem.decode(),
                    "not_after": not_after.isoformat(), "names": names, "staging": staging, "cached": False}
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('node_tls_certs'))")
    except (CertError, nodo_crypto.CryptoError) as exc:
        logger.warning("node certificate: %s", exc)
        return {"status": "error", "names": names, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — never break a node preparation over the certificate
        logger.exception("node certificate failed")
        return {"status": "error", "names": names, "message": f"No se pudo obtener el certificado ({type(exc).__name__})"}
