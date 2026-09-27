"""Shared fixtures. `idp` is a tiny in-process OpenID Connect provider for identity tests."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _int_b64(value: int, size: int | None = None) -> str:
    length = size or (value.bit_length() + 7) // 8
    return _b64(value.to_bytes(length, "big"))


class MockIdP:
    """Discovery, JWKS, authorization-code + PKCE, and token endpoints on 127.0.0.1."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        self.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec_key = ec.generate_private_key(ec.SECP256R1())
        self.published = {"rsa-1": self.rsa_key, "ec-1": self.ec_key}
        self.codes: dict[str, dict[str, Any]] = {}
        self.login_claims: dict[str, Any] = {
            "sub": "alex@example.com",
            "name": "Alex Example",
            "groups": ["platform"],
            "roles": ["operator"],
        }
        self.token_requests: list[dict[str, str]] = []
        self.jwks_fetches = 0
        self.client_id = "loro-web"
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def _json(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                if parts.path == "/.well-known/openid-configuration":
                    self._json(200, idp.discovery())
                elif parts.path == "/jwks":
                    idp.jwks_fetches += 1
                    self._json(200, idp.jwks())
                elif parts.path == "/authorize":
                    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
                    code = secrets.token_urlsafe(16)
                    idp.codes[code] = query
                    target = f"{query['redirect_uri']}?" + urlencode(
                        {"code": code, "state": query["state"]}
                    )
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                else:
                    self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/token":
                    self._json(404, {"error": "not_found"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                idp.token_requests.append(form)
                request = idp.codes.pop(form.get("code", ""), None)
                verifier = form.get("code_verifier", "")
                challenge = _b64(hashlib.sha256(verifier.encode()).digest())
                if (
                    request is None
                    or request.get("code_challenge") != challenge
                    or request.get("redirect_uri") != form.get("redirect_uri")
                ):
                    self._json(400, {"error": "invalid_grant"})
                    return
                id_token = idp.token(
                    {**idp.login_claims, "aud": idp.client_id, "nonce": request.get("nonce")}
                )
                self._json(200, {"id_token": id_token, "token_type": "Bearer"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "jwks_uri": f"{self.issuer}/jwks",
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
        }

    def jwks(self) -> dict[str, Any]:
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        keys = []
        for kid, key in self.published.items():
            public = key.public_key()
            if isinstance(public, rsa.RSAPublicKey):
                numbers = public.public_numbers()
                keys.append(
                    {
                        "kty": "RSA",
                        "kid": kid,
                        "use": "sig",
                        "n": _int_b64(numbers.n),
                        "e": _int_b64(numbers.e),
                    }
                )
            elif isinstance(public, ec.EllipticCurvePublicKey):
                numbers = public.public_numbers()
                keys.append(
                    {
                        "kty": "EC",
                        "kid": kid,
                        "use": "sig",
                        "crv": "P-256",
                        "x": _int_b64(numbers.x, 32),
                        "y": _int_b64(numbers.y, 32),
                    }
                )
        return {"keys": keys}

    def token(
        self,
        claims: dict[str, Any],
        *,
        alg: str = "RS256",
        kid: str | None = None,
        key: Any = None,
        header: dict[str, Any] | None = None,
        lifetime: int = 300,
    ) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        now = int(time.time())
        body = {"iss": self.issuer, "iat": now, "exp": now + lifetime, **claims}
        body = {name: value for name, value in body.items() if value is not None}
        kid = kid or ("ec-1" if alg.startswith("ES") else "rsa-1")
        head = {"alg": alg, "kid": kid, "typ": "JWT", **(header or {})}
        signing_input = f"{_b64(json.dumps(head).encode())}.{_b64(json.dumps(body).encode())}"
        if alg == "none":
            return signing_input + "."
        signer = key or self.published.get(kid) or self.rsa_key
        if alg == "RS256":
            signature = signer.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            der = signer.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
            r, s = decode_dss_signature(der)
            signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        elif alg == "HS256":
            import hmac

            signature = hmac.new(b"public-key-as-secret", signing_input.encode(), "sha256").digest()
        else:
            raise ValueError(alg)
        return f"{signing_input}.{_b64(signature)}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def idp() -> Iterator[MockIdP]:
    provider = MockIdP()
    try:
        yield provider
    finally:
        provider.close()
