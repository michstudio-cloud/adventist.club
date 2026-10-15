"""The node's TLS certificate, obtained by the API (030; app/services/node_certs.py).

  * the small ACME v2 client against a FAKE ACME server (real JWS ES256 verification, a fake CA)
    and a FAKE Cloudflare: DNS-01 record value, cleanup, staging directory, chain + key;
  * the cache: reused while it has > 30 days, renewed below, key encrypted at rest, «no
    configurado» without the env vars, errors reported (never raised);
  * optional live check against Let's Encrypt STAGING only (account + order, no validation):
    run with ACME_STAGING_LIVE=1. Production Let's Encrypt is never contacted by tests.
"""
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID

from app.config import settings
from app.services import node_certs, nodo_crypto
from app.services import nodo_sync_core as core
from tests.conftest import RUN, TEST_DATABASE_URL, requires_db


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _ca():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "(STAGING) Fake CA")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    return key, cert


def issue(ca, public_key, names, days=90):
    key, cert = ca
    now = datetime.now(timezone.utc)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(cert.subject).public_key(public_key).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
            .sign(key, hashes.SHA256()))
    return (leaf.public_bytes(serialization.Encoding.PEM) + cert.public_bytes(serialization.Encoding.PEM)).decode()


class FakeWorld:
    """Fake ACME server + fake Cloudflare + fake DNS behind one `http` callable."""

    def __init__(self, base="https://acme-staging-v02.api.letsencrypt.org"):
        self.base, self.ca = base, _ca()
        self.nonces, self.accounts, self.orders, self.authz = set(), {}, {}, {}
        self.txt: dict[str, dict[str, str]] = {}  # name -> {record id: value}
        self.calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def _nonce(self):
        n = uuid.uuid4().hex
        self.nonces.add(n)
        return n

    def _verify(self, body: bytes) -> tuple[dict, dict | str]:
        jws = json.loads(body)
        protected = json.loads(_unb64u(jws["protected"]))
        assert protected["alg"] == "ES256"
        assert protected["nonce"] in self.nonces, "unknown nonce"
        self.nonces.discard(protected["nonce"])
        jwk = protected.get("jwk") or self.accounts[protected["kid"]]
        public = ec.EllipticCurvePublicNumbers(int.from_bytes(_unb64u(jwk["x"]), "big"),
                                               int.from_bytes(_unb64u(jwk["y"]), "big"), ec.SECP256R1()).public_key()
        sig = _unb64u(jws["signature"])
        try:
            public.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                          f"{jws['protected']}.{jws['payload']}".encode(), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:  # pragma: no cover
            raise AssertionError("bad JWS signature") from None
        payload = json.loads(_unb64u(jws["payload"])) if jws["payload"] else ""
        return {**protected, "jwk": jwk}, payload

    def http(self, method, url, headers=None, body=None, timeout=30):
        self.calls.append((method, url))
        ok = lambda data, extra=None, status=200: (status, {"replay-nonce": self._nonce(), **(extra or {})},  # noqa: E731
                                                   json.dumps(data).encode() if not isinstance(data, bytes) else data)
        if url.startswith(node_certs.CLOUDFLARE_API):
            assert headers["Authorization"] == "Bearer cf-token"
            path = url[len(node_certs.CLOUDFLARE_API):]
            if method == "GET" and path.startswith("/zones?name="):
                name = path.split("=", 1)[1]
                return ok({"success": True, "result": [{"id": "zone1"}] if name == "adventist.club" else []})
            if method == "POST" and path == "/zones/zone1/dns_records":
                data = json.loads(body)
                rid = uuid.uuid4().hex
                self.txt.setdefault(data["name"], {})[rid] = data["content"]
                return ok({"success": True, "result": {"id": rid}})
            if method == "DELETE":
                rid = path.rsplit("/", 1)[1]
                for values in self.txt.values():
                    values.pop(rid, None)
                self.deleted.append(rid)
                return ok({"success": True, "result": {"id": rid}})
            raise AssertionError(f"unexpected Cloudflare call {method} {path}")
        if url.endswith("/directory"):
            assert url.startswith(self.base), url
            d = self.base
            return ok({"newNonce": f"{d}/nonce", "newAccount": f"{d}/account", "newOrder": f"{d}/order"})
        if url == f"{self.base}/nonce":
            return ok(b"")
        protected, payload = self._verify(body)
        if url == f"{self.base}/account":
            assert payload["termsOfServiceAgreed"] is True
            kid = f"{self.base}/acct/{len(self.accounts) + 1}"
            self.accounts[kid] = protected["jwk"]
            self.thumb = base64.urlsafe_b64encode(hashlib.sha256(json.dumps(
                protected["jwk"], sort_keys=True, separators=(",", ":")).encode()).digest()).decode().rstrip("=")
            return ok({"status": "valid"}, {"location": kid}, 201)
        if url == f"{self.base}/order":
            oid = uuid.uuid4().hex
            names = [i["value"] for i in payload["identifiers"]]
            auths = []
            for n in names:
                aid = uuid.uuid4().hex
                self.authz[aid] = {"name": n, "status": "pending", "token": uuid.uuid4().hex}
                auths.append(f"{self.base}/authz/{aid}")
            self.orders[oid] = {"status": "pending", "names": names, "authorizations": auths,
                                "finalize": f"{self.base}/finalize/{oid}"}
            return ok(self.orders[oid], {"location": f"{self.base}/orders/{oid}"}, 201)
        if "/authz/" in url:
            a = self.authz[url.rsplit("/", 1)[1]]
            return ok({"status": a["status"], "identifier": {"type": "dns", "value": a["name"]},
                       "challenges": [{"type": "http-01", "url": "x", "token": "y"},
                                      {"type": "dns-01", "url": f"{self.base}/chall/{url.rsplit('/', 1)[1]}",
                                       "token": a["token"]}]})
        if "/chall/" in url:
            a = self.authz[url.rsplit("/", 1)[1]]
            expected = base64.urlsafe_b64encode(hashlib.sha256(f"{a['token']}.{self.thumb}".encode()).digest()
                                                ).decode().rstrip("=")
            a["status"] = "valid" if expected in self.txt.get(f"_acme-challenge.{a['name']}", {}).values() else "invalid"
            return ok({"status": "processing"})
        if "/finalize/" in url:
            oid = url.rsplit("/", 1)[1]
            csr = x509.load_der_x509_csr(_unb64u(payload["csr"]))
            sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
                x509.DNSName)
            assert sorted(sans) == sorted(self.orders[oid]["names"])
            self.orders[oid].update(status="valid", certificate=f"{self.base}/cert/{oid}")
            self.orders[oid]["pem"] = issue(self.ca, csr.public_key(), sans)
            return ok(self.orders[oid])
        if "/orders/" in url:
            return ok({k: v for k, v in self.orders[url.rsplit("/", 1)[1]].items() if k != "pem"})
        if "/cert/" in url:
            return ok(self.orders[url.rsplit("/", 1)[1]]["pem"].encode())
        raise AssertionError(f"unexpected call {method} {url}")

    def resolver(self, name):
        return set(self.txt.get(name, {}).values())


NAMES = ["admin.adventist.club", "api.adventist.club", "events.adventist.club"]


def test_acme_dns01_against_a_fake_server_uses_staging_and_cleans_up():
    world = FakeWorld()
    chain, key_pem = node_certs.obtain(NAMES, email="owner@example.com", token="cf-token", staging=True,
                                       http=world.http, sleep=lambda s: None, resolver=world.resolver)
    assert world.calls[0] == ("GET", node_certs.LE_STAGING)
    assert not any(node_certs.LE_PRODUCTION in u for _, u in world.calls)
    leaf = x509.load_pem_x509_certificate(chain.encode())
    sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert sorted(sans) == NAMES
    key = serialization.load_pem_private_key(key_pem, None)
    assert key.public_key().public_numbers() == leaf.public_key().public_numbers()
    assert all(not v for v in world.txt.values()) and len(world.deleted) == 3  # TXT records removed
    assert node_certs.not_after_of(chain) > datetime.now(timezone.utc) + timedelta(days=80)


def test_acme_failed_validation_is_reported_and_records_removed():
    world2 = FakeWorld()
    orig = world2.http

    def bad_dns(method, url, headers=None, body=None, timeout=30):
        if method == "POST" and url.endswith("/dns_records"):
            data = json.loads(body)
            data["content"] = "wrong"
            body = json.dumps(data).encode()
        return orig(method, url, headers, body, timeout)
    with pytest.raises(node_certs.CertError):
        node_certs.obtain(NAMES, email="o@example.com", token="cf-token", staging=True, http=bad_dns,
                          sleep=lambda s: None, resolver=lambda name: {"wrong"} | world2.resolver(name))
    assert all(not v for v in world2.txt.values())


@pytest.fixture
def configured(monkeypatch):
    names = f"{RUN}-{uuid.uuid4().hex[:6]}.events.adventist.club"
    monkeypatch.setattr(settings, "CLOUDFLARE_DNS_API_TOKEN", "cf-token")
    monkeypatch.setattr(settings, "LE_EMAIL", "owner@example.com")
    monkeypatch.setattr(settings, "LE_STAGING", True)
    monkeypatch.setattr(settings, "NODO_HOSTNAMES", names)
    return [names]


@requires_db
async def test_certificate_cache_lifecycle(configured):
    ca = _ca()
    calls = []

    def fake_obtain(names, *, email, token, staging, days=90):
        calls.append((tuple(names), email, token, staging))
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        return issue(ca, key.public_key(), names, days=fake_obtain.days), pem
    fake_obtain.days = 90

    conn = await core.connect(TEST_DATABASE_URL)
    try:
        first = await node_certs.certificate_for_node(conn, obtain_fn=fake_obtain)
        assert first["status"] == "ok" and first["cached"] is False and first["staging"] is True
        assert calls == [(tuple(configured), "owner@example.com", "cf-token", True)]
        row = await conn.fetchrow("SELECT privkey_enc FROM node_tls_certs WHERE names = $1", configured[0])
        assert "PRIVATE KEY" not in row["privkey_enc"]  # encrypted at rest
        assert nodo_crypto.unbox(settings.nodo_sync_secret, node_certs.PURPOSE, row["privkey_enc"]).decode() \
            == first["privkey_pem"]
        second = await node_certs.certificate_for_node(conn, obtain_fn=fake_obtain)
        assert second["cached"] is True and len(calls) == 1 and second["fullchain_pem"] == first["fullchain_pem"]
        # 20 days left (< 30): renewed
        await conn.execute("UPDATE node_tls_certs SET not_after = now() + interval '20 days' WHERE names = $1",
                           configured[0])
        third = await node_certs.certificate_for_node(conn, obtain_fn=fake_obtain)
        assert third["cached"] is False and len(calls) == 2

        def broken(*a, **k):
            raise node_certs.CertError("Cloudflare rechazó la operación (token inválido)")
        await conn.execute("UPDATE node_tls_certs SET not_after = now() + interval '1 day' WHERE names = $1",
                           configured[0])
        failed = await node_certs.certificate_for_node(conn, obtain_fn=broken)
        assert failed["status"] == "error" and "token inválido" in failed["message"]
    finally:
        await conn.execute("DELETE FROM node_tls_certs WHERE names = $1", configured[0])
        await conn.close()


@requires_db
async def test_certificate_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_DNS_API_TOKEN", None)
    conn = await core.connect(TEST_DATABASE_URL)
    try:
        result = await node_certs.certificate_for_node(conn, obtain_fn=lambda *a, **k: pytest.fail("called"))
    finally:
        await conn.close()
    assert result["status"] == "no_configurado"


@pytest.mark.skipif(os.environ.get("ACME_STAGING_LIVE") != "1", reason="live Let's Encrypt STAGING check (opt-in)")
def test_live_staging_account_and_order():
    acme = node_certs.Acme(node_certs.LE_STAGING)
    acme.register(None)  # LE refuses example.com contacts; contact is optional
    assert acme.kid and "staging" in acme.kid
    _, headers, data = acme._post(acme.directory["newOrder"],
                                  {"identifiers": [{"type": "dns", "value": "events.adventist.club"}]})
    order = json.loads(data)
    assert order["status"] == "pending" and order["authorizations"]
