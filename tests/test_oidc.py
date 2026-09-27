"""OIDC/JWT verification against an in-process mock identity provider."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from loro.config import IdentityConfig, OIDCConfig
from loro.identity import IdentityConfigurationError, resolve_identity
from loro.oidc import OIDCError, OIDCProvider, require_secure_url


def _provider(idp, **overrides) -> OIDCProvider:
    config = OIDCConfig(
        enabled=True, issuer=idp.issuer, client_id="loro-web", audience="loro-api", **overrides
    )
    provider = OIDCProvider(config)
    provider.refresh_cooldown = 0
    return provider


def test_valid_rs256_and_es256_tokens_verify(idp) -> None:
    provider = _provider(idp)
    claims = provider.verify(idp.token({"sub": "alex", "aud": "loro-api"}))
    assert claims["sub"] == "alex"
    claims = provider.verify(idp.token({"sub": "sam", "aud": ["other", "loro-api"]}, alg="ES256"))
    assert claims["sub"] == "sam"


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({"sub": "a", "aud": "someone-else"}, "audience"),
        ({"sub": "a", "aud": "loro-api", "iss": "https://evil.example"}, "different issuer"),
        ({"sub": "a", "aud": "loro-api", "exp": int(time.time()) - 3600}, "expired"),
        ({"sub": "a", "aud": "loro-api", "nbf": int(time.time()) + 3600}, "not valid yet"),
        ({"sub": "a", "aud": "loro-api", "iat": int(time.time()) + 3600}, "in the future"),
        ({"sub": "a", "aud": "loro-api", "exp": None}, "no expiry"),
        ({"aud": "loro-api"}, "'sub' claim"),
    ],
)
def test_claim_checks_reject_bad_tokens(idp, claims, message) -> None:
    with pytest.raises(OIDCError, match=message):
        _provider(idp).verify(idp.token(claims))


def test_clock_skew_is_tolerated_within_bounds(idp) -> None:
    now = int(time.time())
    token = idp.token({"sub": "a", "aud": "loro-api", "exp": now - 30, "nbf": now + 30})
    assert _provider(idp, clock_skew_seconds=60).verify(token)["sub"] == "a"
    with pytest.raises(OIDCError):
        _provider(idp, clock_skew_seconds=0).verify(token)


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_none_and_hmac_algorithms_are_rejected(idp, alg) -> None:
    token = idp.token({"sub": "a", "aud": "loro-api"}, alg=alg)
    with pytest.raises(OIDCError):
        _provider(idp).verify(token)


def test_config_refuses_unsafe_algorithms() -> None:
    for algorithm in ("none", "HS256"):
        with pytest.raises(ValidationError, match="never"):
            OIDCConfig(algorithms=[algorithm])


def test_algorithm_outside_the_allowlist_is_rejected(idp) -> None:
    token = idp.token({"sub": "a", "aud": "loro-api"}, alg="ES256")
    with pytest.raises(OIDCError, match="not allowed"):
        _provider(idp, algorithms=["RS256"]).verify(token)


def test_tampered_signature_and_payload_fail(idp) -> None:
    token = idp.token({"sub": "a", "aud": "loro-api"})
    header, payload, signature = token.split(".")
    flipped = signature[:-2] + ("A" if signature[-2] != "A" else "B") + signature[-1]
    with pytest.raises(OIDCError, match="signature"):
        _provider(idp).verify(f"{header}.{payload}.{flipped}")
    other = idp.token({"sub": "admin", "aud": "loro-api"}).split(".")[1]
    with pytest.raises(OIDCError, match="signature"):
        _provider(idp).verify(f"{header}.{other}.{signature}")


def test_key_from_another_issuer_is_rejected(idp) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = idp.token({"sub": "a", "aud": "loro-api"}, key=stranger)
    with pytest.raises(OIDCError, match="signature"):
        _provider(idp).verify(token)


def test_unknown_kid_triggers_one_refresh_for_key_rotation(idp) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    provider = _provider(idp)
    provider.verify(idp.token({"sub": "a", "aud": "loro-api"}))
    fetches = idp.jwks_fetches
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp.published["rsa-2"] = rotated
    token = idp.token({"sub": "a", "aud": "loro-api"}, kid="rsa-2")
    assert provider.verify(token)["sub"] == "a"
    assert idp.jwks_fetches == fetches + 1
    # Cached afterwards: no fetch per verification.
    provider.verify(token)
    assert idp.jwks_fetches == fetches + 1
    with pytest.raises(OIDCError, match="unknown key"):
        provider.verify(idp.token({"sub": "a", "aud": "loro-api"}, kid="missing"))


def test_nonce_and_azp_are_checked_for_logins(idp) -> None:
    provider = _provider(idp)
    token = idp.token({"sub": "a", "aud": "loro-web", "nonce": "n1"})
    assert provider.verify(token, audience="loro-web", nonce="n1")
    with pytest.raises(OIDCError, match="login attempt"):
        provider.verify(token, audience="loro-web", nonce="n2")
    multi = idp.token({"sub": "a", "aud": ["loro-web", "x"], "nonce": "n1", "azp": "x"})
    with pytest.raises(OIDCError, match="authorized party"):
        provider.verify(multi, audience="loro-web", nonce="n1")


def test_plain_http_issuers_are_loopback_only() -> None:
    assert require_secure_url("http://127.0.0.1:9/x", "issuer")
    assert require_secure_url("https://login.example.com", "issuer")
    with pytest.raises(OIDCError, match="https"):
        require_secure_url("http://login.example.com", "issuer")


def test_discovery_issuer_mismatch_is_rejected(idp) -> None:
    provider = OIDCProvider(
        OIDCConfig(
            enabled=True,
            issuer=idp.issuer + "/tenant",
            discovery_url=idp.issuer + "/.well-known/openid-configuration",
            client_id="c",
        )
    )
    with pytest.raises(OIDCError, match="different issuer"):
        provider.metadata()


def _identity_config(idp, **overrides) -> IdentityConfig:
    return IdentityConfig(
        oidc=OIDCConfig(
            enabled=True,
            issuer=idp.issuer,
            audience="loro-api",
            tenant_claim="tid",
            **overrides,
        )
    )


def test_resolved_identity_is_verified_from_the_token(idp) -> None:
    token = idp.token(
        {
            "sub": "alex",
            "aud": "loro-api",
            "name": "Alex",
            "tid": "acme",
            "groups": ["data"],
            "roles": ["approver"],
        }
    )
    identity = resolve_identity(_identity_config(idp), environ={"LORO_ID_TOKEN": token})
    assert identity.verified is True
    assert identity.auth_method == "oidc"
    assert (identity.subject, identity.display_name, identity.tenant) == ("alex", "Alex", "acme")
    assert identity.roles == ("approver",) and identity.groups == ("data",)
    assert identity.source == f"oidc:{idp.issuer}"


def test_required_verification_fails_closed(idp) -> None:
    config = _identity_config(idp, required=True)
    with pytest.raises(IdentityConfigurationError, match="verified identity is required"):
        resolve_identity(config, environ={})
    bad = idp.token({"sub": "a", "aud": "wrong"})
    with pytest.raises(IdentityConfigurationError, match="OIDC token rejected"):
        resolve_identity(config, environ={"LORO_ID_TOKEN": bad})
    # Environment assertions cannot stand in for a token.
    with pytest.raises(IdentityConfigurationError):
        resolve_identity(config, environ={"LORO_IDENTITY_SUBJECT": "root"})


def test_unverified_fallback_is_labelled_when_not_required(idp) -> None:
    identity = resolve_identity(_identity_config(idp), environ={})
    assert identity.verified is False
    assert identity.auth_method != "oidc"
