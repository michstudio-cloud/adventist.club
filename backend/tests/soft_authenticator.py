"""A WebAuthn authenticator in software, for the passkey tests (tests/test_passkeys.py).

It does what a phone does with a passkey: holds a P-256 private key, answers a registration
with an attestation object (format "none", like iCloud Keychain and Google Password Manager)
and a login with a signed assertion. Every knob a test needs to lie with is a parameter:
the origin, the RP ID, the user-verification flag, the counter.
"""
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01  # user present
FLAG_UV = 0x04  # user verified (biometrics / PIN)
FLAG_BE = 0x08  # backup eligible (a synced passkey)
FLAG_BS = 0x10  # backed up
FLAG_AT = 0x40  # attested credential data included

ICLOUD_AAGUID = bytes.fromhex("fbfc3007154e4ecc8c0b6e020557d7bd")


def b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, *, synced: bool = True, aaguid: bytes = ICLOUD_AAGUID):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(20)
        self.synced = synced
        self.aaguid = aaguid
        # Synced passkeys always report 0; a device-bound key counts its signatures.
        self.sign_count = 0
        self.user_handle = b""

    # -- pieces ---------------------------------------------------------------
    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def _flags(self, *, user_verified: bool, attested: bool) -> int:
        flags = FLAG_UP
        if user_verified:
            flags |= FLAG_UV
        if self.synced:
            flags |= FLAG_BE | FLAG_BS
        if attested:
            flags |= FLAG_AT
        return flags

    def _cose_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
        )

    def _next_count(self) -> int:
        if not self.synced:
            self.sign_count += 1
        return self.sign_count

    # -- ceremonies -----------------------------------------------------------
    def create(self, options: dict, *, origin: str, rp_id: str | None = None, user_verified: bool = True) -> dict:
        """The answer to `navigator.credentials.create(options)`, as `credential.toJSON()`."""
        rp_id = rp_id or options["rp"]["id"]
        self.user_handle = b64url_decode(options["user"]["id"])
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(user_verified=user_verified, attested=True)])
            + struct.pack(">I", self._next_count())
            + self.aaguid
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal", "hybrid"],
            },
        }

    def get(
        self,
        options: dict,
        *,
        origin: str,
        rp_id: str | None = None,
        user_verified: bool = True,
        sign_count: int | None = None,
    ) -> dict:
        """The answer to `navigator.credentials.get(options)`, as `credential.toJSON()`."""
        rp_id = rp_id or options["rpId"]
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        count = self._next_count() if sign_count is None else sign_count
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(user_verified=user_verified, attested=False)])
            + struct.pack(">I", count)
        )
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle),
            },
        }
