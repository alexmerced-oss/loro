"""Web UI authentication: the per-launch token (default) or OpenID Connect sign-in.

In ``oidc`` mode the browser signs in with the authorization-code flow and PKCE (S256), and
API clients send ``Authorization: Bearer <JWT>``. Either way every ``/api/`` request carries a
verified identity in ``request.state.identity``; the server never falls back to the local OS
user in this mode.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from loro.config import OIDCConfig
from loro.identity import IdentityContext, identity_from_claims
from loro.oidc import OIDCError, OIDCProvider, pkce_pair

SESSION_COOKIE = "loro_web_session"
LOGIN_TTL_SECONDS = 600
MAX_PENDING_LOGINS = 256
MAX_SESSIONS = 1024


@dataclass
class WebSession:
    csrf: str
    identity: IdentityContext | None = None
    expires_at: float = float("inf")
    created_at: float = field(default_factory=time.time)


@dataclass
class _PendingLogin:
    nonce: str
    verifier: str
    next_path: str
    created_at: float


class WebAuth:
    """Session store plus the OIDC login flow for one Web UI server."""

    def __init__(self, mode: str, oidc: OIDCConfig | None = None) -> None:
        if mode not in {"token", "oidc"}:
            raise ValueError(f"Unknown Web UI auth mode: {mode}")
        self.mode = mode
        self.oidc = oidc
        self.provider: OIDCProvider | None = None
        if mode == "oidc":
            if oidc is None or not oidc.enabled or not oidc.client_id:
                raise ValueError(
                    "OIDC sign-in needs [identity.oidc] enabled = true with issuer and client_id."
                )
            self.provider = OIDCProvider(oidc)
        self.sessions: dict[str, WebSession] = {}
        self._pending: dict[str, _PendingLogin] = {}
        self._lock = threading.Lock()

    def _oidc_parts(self) -> tuple[OIDCProvider, OIDCConfig]:
        # An explicit check rather than an assert: `python -O` strips asserts, and these
        # guard the sign-in path.
        if self.provider is None or self.oidc is None:
            raise OIDCError("OIDC sign-in is not configured for this server.")
        return self.provider, self.oidc

    # ------------------------------------------------------------------ sessions

    def new_session(
        self, identity: IdentityContext | None = None, expires_at: float | None = None
    ) -> tuple[str, WebSession]:
        session = WebSession(
            csrf=secrets.token_urlsafe(32),
            identity=identity,
            expires_at=expires_at if expires_at is not None else float("inf"),
        )
        session_id = secrets.token_urlsafe(24)
        with self._lock:
            while len(self.sessions) >= MAX_SESSIONS:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session_id] = session
        return session_id, session

    def session(self, session_id: str | None) -> WebSession | None:
        if not session_id:
            return None
        with self._lock:
            session = self.sessions.get(session_id)
            if session is not None and session.expires_at <= time.time():
                self.sessions.pop(session_id, None)
                return None
            return session

    def end_session(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self.sessions.pop(session_id, None)

    # ------------------------------------------------------------------ bearer tokens

    def identity_from_bearer(self, authorization: str) -> IdentityContext:
        provider, oidc = self._oidc_parts()
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise OIDCError("Send Authorization: Bearer <token>.")
        claims = provider.verify(token.strip())
        return identity_from_claims(oidc, claims, session_id=f"api-{secrets.token_hex(8)}")

    # ------------------------------------------------------------------ login flow

    def begin_login(self, redirect_uri: str, next_path: str) -> str:
        provider, _ = self._oidc_parts()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        now = time.time()
        with self._lock:
            for key in [
                k for k, v in self._pending.items() if now - v.created_at > LOGIN_TTL_SECONDS
            ]:
                self._pending.pop(key, None)
            while len(self._pending) >= MAX_PENDING_LOGINS:
                self._pending.pop(next(iter(self._pending)))
            self._pending[state] = _PendingLogin(nonce, verifier, safe_next(next_path), now)
        return provider.authorization_url(
            redirect_uri=redirect_uri, state=state, nonce=nonce, code_challenge=challenge
        )

    def complete_login(
        self, *, state: str, code: str, redirect_uri: str
    ) -> tuple[IdentityContext, float, str]:
        """Exchange the code, verify the ID token, and return (identity, expiry, next path)."""

        provider, oidc = self._oidc_parts()
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending.created_at > LOGIN_TTL_SECONDS:
            raise OIDCError("This sign-in link expired or was already used. Start again.")
        secret = os.environ.get(oidc.client_secret_env) if oidc.client_secret_env else None
        tokens = provider.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=pending.verifier,
            client_secret=secret,
        )
        claims = provider.verify(
            str(tokens["id_token"]), audience=oidc.client_id, nonce=pending.nonce
        )
        identity = identity_from_claims(oidc, claims, session_id=f"web-{secrets.token_hex(8)}")
        # The ID token proves the sign-in; the session then lasts web_session_seconds.
        expires = time.time() + oidc.web_session_seconds
        return identity, expires, pending.next_path

    def describe(self) -> dict[str, Any]:
        if self.mode != "oidc" or self.oidc is None:
            return {"mode": self.mode}
        host = urlsplit(self.oidc.issuer or "").hostname or "your identity provider"
        return {"mode": "oidc", "issuer": self.oidc.issuer, "issuer_name": host}


def safe_next(path: str) -> str:
    """Only same-origin relative paths survive the login round trip (no open redirects)."""

    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        # Browsers drop tabs and newlines from URLs, so "/\t/evil.example" becomes
        # "//evil.example"; control characters could also split the Location header.
        or any(ord(char) < 0x21 or ord(char) == 0x7F for char in path)
    ):
        return "/"
    return path


def login_error_redirect(message: str) -> str:
    return "/?auth_error=" + quote(message[:300])
