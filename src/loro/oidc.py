"""OpenID Connect and JWT verification for Loro identities.

Loro verifies tokens itself instead of trusting a label: the issuer's discovery document and
JWKS are fetched over HTTPS (plain HTTP only for loopback test issuers), keys are cached and
refreshed when an unknown ``kid`` appears, and every token is checked for signature, algorithm
(an explicit allowlist; ``none`` and HMAC algorithms are never accepted), ``iss``, ``aud``,
``exp``, ``nbf`` and ``iat`` with bounded clock skew, and ``nonce`` for browser logins.

Signature verification uses the ``cryptography`` package (installed on Linux by default and
through the ``loro-agent[oidc]`` extra elsewhere).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from loro.config import OIDCConfig

SUPPORTED_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
MAX_DOCUMENT_BYTES = 1_000_000
MAX_TOKEN_CHARS = 16_384
JWKS_REFRESH_COOLDOWN_SECONDS = 30.0


class OIDCError(ValueError):
    """A token or provider problem, with a message safe to show the user."""


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as error:
        raise OIDCError("Token is not valid base64url.") from error


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_secure_url(url: str, what: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and _loopback(url)):
        return url
    raise OIDCError(f"{what} must use https (plain http is allowed only on loopback): {url}")


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""

    verifier = secrets.token_urlsafe(48)
    challenge = _b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass
class _Cache:
    metadata: dict[str, Any] | None = None
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    keys_fetched_at: float = float("-inf")


class OIDCProvider:
    """Discovery, JWKS caching and token verification for one configured issuer."""

    def __init__(
        self,
        config: OIDCConfig,
        *,
        client: Callable[[], httpx.Client] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not config.issuer:
            raise OIDCError("identity.oidc.issuer is not configured.")
        self.config = config
        self.issuer = require_secure_url(config.issuer.rstrip("/"), "identity.oidc.issuer")
        self._client = client or (lambda: httpx.Client(timeout=5.0, follow_redirects=False))
        self._clock = clock
        self._cache = _Cache()
        self._lock = threading.Lock()
        self.refresh_cooldown = JWKS_REFRESH_COOLDOWN_SECONDS

    # ------------------------------------------------------------------ fetching

    def _get_json(self, url: str, what: str) -> dict[str, Any]:
        require_secure_url(url, what)
        try:
            with self._client() as client:
                response = client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise OIDCError(f"Could not reach the identity provider for {what}: {error}") from error
        if response.status_code != 200:
            raise OIDCError(
                f"The identity provider returned HTTP {response.status_code} for {what}."
            )
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise OIDCError(f"The identity provider's {what} is too large.")
        try:
            payload = response.json()
        except ValueError as error:
            raise OIDCError(f"The identity provider's {what} is not JSON.") from error
        if not isinstance(payload, dict):
            raise OIDCError(f"The identity provider's {what} is not a JSON object.")
        return payload

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            if self._cache.metadata is not None:
                return self._cache.metadata
        url = self.config.discovery_url or f"{self.issuer}/.well-known/openid-configuration"
        document = self._get_json(url, "discovery document")
        if str(document.get("issuer", "")).rstrip("/") != self.issuer:
            raise OIDCError(
                "The discovery document names a different issuer than identity.oidc.issuer."
            )
        with self._lock:
            self._cache.metadata = document
        return document

    def _refresh_keys(self, *, force: bool) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            age = now - self._cache.keys_fetched_at
            fresh = age < self.config.jwks_cache_seconds
            cooling = age < self.refresh_cooldown
            if self._cache.keys and (fresh and not force or cooling):
                return self._cache.keys
        jwks_uri = self.config.jwks_uri or str(self.metadata().get("jwks_uri") or "")
        if not jwks_uri:
            raise OIDCError("The identity provider does not publish a jwks_uri.")
        document = self._get_json(jwks_uri, "JWKS")
        keys: dict[str, dict[str, Any]] = {}
        for index, key in enumerate(document.get("keys") or []):
            if isinstance(key, dict) and key.get("use", "sig") == "sig":
                keys[str(key.get("kid") or f"#{index}")] = key
        with self._lock:
            self._cache.keys = keys
            self._cache.keys_fetched_at = now
        return keys

    def _key_for(self, header: Mapping[str, Any]) -> dict[str, Any]:
        kid = header.get("kid")
        keys = self._refresh_keys(force=False)
        if kid is not None and str(kid) not in keys:
            keys = self._refresh_keys(force=True)  # key rotation
        if kid is not None:
            key = keys.get(str(kid))
            if key is None:
                raise OIDCError("Token was signed with an unknown key.")
            return key
        candidates = [
            key for key in keys.values() if _kty_for(str(header["alg"])) == key.get("kty")
        ]
        if len(candidates) != 1:
            raise OIDCError("Token has no key id and the key set is ambiguous.")
        return candidates[0]

    # ------------------------------------------------------------------ verify

    def verify(
        self,
        token: str,
        *,
        audience: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        """Verify a compact JWS and return its claims, or raise :class:`OIDCError`."""

        if len(token) > MAX_TOKEN_CHARS:
            raise OIDCError("Token is too large.")
        parts = token.strip().split(".")
        if len(parts) != 3 or not all(parts):
            raise OIDCError("Token is not a signed JWT.")
        try:
            header = json.loads(_b64decode(parts[0]))
            claims = json.loads(_b64decode(parts[1]))
        except (ValueError, UnicodeDecodeError) as error:
            raise OIDCError("Token header or claims are not JSON.") from error
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise OIDCError("Token header and claims must be JSON objects.")
        algorithm = header.get("alg")
        allowed = set(self.config.algorithms) & SUPPORTED_ALGORITHMS
        if not isinstance(algorithm, str) or algorithm not in allowed:
            raise OIDCError(f"Token algorithm {algorithm!r} is not allowed.")
        if header.get("crit"):
            raise OIDCError("Token uses critical header extensions Loro does not support.")
        key = self._key_for(header)
        if key.get("alg") and key["alg"] != algorithm:
            raise OIDCError("Token algorithm does not match its signing key.")
        _verify_signature(
            key, algorithm, f"{parts[0]}.{parts[1]}".encode("ascii"), _b64decode(parts[2])
        )
        self._check_claims(claims, audience=audience, nonce=nonce)
        return claims

    def _check_claims(
        self, claims: Mapping[str, Any], *, audience: str | None, nonce: str | None
    ) -> None:
        now = self._clock()
        skew = self.config.clock_skew_seconds
        if str(claims.get("iss", "")).rstrip("/") != self.issuer:
            raise OIDCError("Token was issued by a different issuer.")
        expected = audience or self.config.audience or self.config.client_id
        if not expected:
            raise OIDCError("identity.oidc.audience (or client_id) is not configured.")
        audiences = claims.get("aud")
        if isinstance(audiences, str):
            values = [audiences]
        elif isinstance(audiences, list) and all(isinstance(item, str) for item in audiences):
            values = audiences
        else:
            raise OIDCError("Token audience is missing or malformed.")
        if expected not in values:
            raise OIDCError("Token is not intended for this audience.")
        if nonce is not None and len(values) > 1 and claims.get("azp") != expected:
            raise OIDCError("Token authorized party does not match this client.")
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            raise OIDCError("Token has no expiry.")
        if now > exp + skew:
            raise OIDCError("Token has expired. Sign in again.")
        nbf = claims.get("nbf")
        if isinstance(nbf, (int, float)) and now + skew < nbf:
            raise OIDCError("Token is not valid yet (check the system clock).")
        iat = claims.get("iat")
        if isinstance(iat, (int, float)) and iat > now + skew:
            raise OIDCError("Token was issued in the future (check the system clock).")
        if nonce is not None and claims.get("nonce") != nonce:
            raise OIDCError("Sign-in response does not match this login attempt.")
        if not claims.get(self.config.subject_claim):
            raise OIDCError(f"Token has no {self.config.subject_claim!r} claim.")

    # ------------------------------------------------------------------ browser flow

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        endpoint = str(self.metadata().get("authorization_endpoint") or "")
        if not endpoint:
            raise OIDCError("The identity provider does not publish an authorization endpoint.")
        require_secure_url(endpoint, "authorization endpoint")
        query = {
            "response_type": "code",
            "client_id": self.config.client_id or "",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urlencode(query)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: str, client_secret: str | None
    ) -> dict[str, Any]:
        endpoint = str(self.metadata().get("token_endpoint") or "")
        if not endpoint:
            raise OIDCError("The identity provider does not publish a token endpoint.")
        require_secure_url(endpoint, "token endpoint")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id or "",
            "code_verifier": code_verifier,
        }
        if client_secret:
            form["client_secret"] = client_secret
        try:
            with self._client() as client:
                response = client.post(endpoint, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise OIDCError(f"Could not reach the identity provider: {error}") from error
        if response.status_code != 200:
            raise OIDCError(
                f"The identity provider rejected the sign-in (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise OIDCError("The identity provider's token response is not JSON.") from error
        if not isinstance(payload, dict) or not payload.get("id_token"):
            raise OIDCError("The identity provider did not return an ID token.")
        return payload


def _kty_for(algorithm: str) -> str:
    if algorithm.startswith(("RS", "PS")):
        return "RSA"
    if algorithm.startswith("ES"):
        return "EC"
    return "OKP"


def _int(value: str) -> int:
    return int.from_bytes(_b64decode(value), "big")


def _verify_signature(
    key: Mapping[str, Any], algorithm: str, message: bytes, signature: bytes
) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    except ImportError as error:  # pragma: no cover - depends on the platform
        raise OIDCError(
            "OIDC verification needs the cryptography package: pip install 'loro-agent[oidc]'."
        ) from error
    digest = {"256": hashes.SHA256(), "384": hashes.SHA384(), "512": hashes.SHA512()}
    kty = key.get("kty")
    if kty != _kty_for(algorithm):
        raise OIDCError("Token algorithm does not match its signing key type.")
    try:
        if kty == "RSA":
            public = rsa.RSAPublicNumbers(_int(key["e"]), _int(key["n"])).public_key()
            if public.key_size < 2048:
                raise OIDCError("Signing key is weaker than 2048 bits.")
            chosen = digest[algorithm[2:]]
            scheme = (
                padding.PKCS1v15()
                if algorithm.startswith("RS")
                else padding.PSS(mgf=padding.MGF1(chosen), salt_length=chosen.digest_size)
            )
            public.verify(signature, message, scheme, chosen)
        elif kty == "EC":
            curves = {
                "P-256": (ec.SECP256R1(), 32),
                "P-384": (ec.SECP384R1(), 48),
                "P-521": (ec.SECP521R1(), 66),
            }
            expected_curve = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}[algorithm]
            if key.get("crv") != expected_curve:
                raise OIDCError("Token algorithm does not match its signing key curve.")
            curve, size = curves[expected_curve]
            if len(signature) != 2 * size:
                raise OIDCError("Token signature has the wrong length.")
            public_ec = ec.EllipticCurvePublicNumbers(
                _int(key["x"]), _int(key["y"]), curve
            ).public_key()
            der = encode_dss_signature(
                int.from_bytes(signature[:size], "big"), int.from_bytes(signature[size:], "big")
            )
            public_ec.verify(der, message, ec.ECDSA(digest[algorithm[2:]]))
        elif kty == "OKP" and key.get("crv") == "Ed25519":
            ed25519.Ed25519PublicKey.from_public_bytes(_b64decode(key["x"])).verify(
                signature, message
            )
        else:
            raise OIDCError("Unsupported signing key type.")
    except InvalidSignature as error:
        raise OIDCError("Token signature is invalid.") from error
    except (KeyError, ValueError) as error:
        if isinstance(error, OIDCError):
            raise
        raise OIDCError("Signing key is malformed.") from error


def claim_values(claims: Mapping[str, Any], name: str | None) -> tuple[str, ...]:
    if not name:
        return ()
    value: Any = claims
    for part in name.split("."):  # dotted paths reach nested claims such as realm_access.roles
        value = value.get(part) if isinstance(value, Mapping) else None
    if isinstance(value, str):
        return tuple(item for item in value.replace(",", " ").split() if item)
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item))
    return ()


def claim_value(claims: Mapping[str, Any], name: str | None) -> str | None:
    values = claim_values(claims, name)
    if not name:
        return None
    raw: Any = claims
    for part in name.split("."):
        raw = raw.get(part) if isinstance(raw, Mapping) else None
    if isinstance(raw, (str, int)):
        return str(raw)
    return values[0] if values else None
