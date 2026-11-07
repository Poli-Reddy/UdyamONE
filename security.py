"""OIDC principal validation and role policy for staff access."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Iterable

from config import SETTINGS


ADMIN = "udyamone_admin"
MANAGER = "udyamone_manager"
SALES = "udyamone_sales"
KYC_REVIEWER = "udyamone_kyc"
ACTIVATION = "udyamone_activation"
PARTNER = "udyamone_partner"
BILLING = "udyamone_billing"
ANALYST = "udyamone_analyst"

STAFF_ROLES = frozenset({ADMIN, MANAGER, SALES, KYC_REVIEWER, ACTIVATION, PARTNER, BILLING, ANALYST})
ORDER_READ_ROLES = frozenset({ADMIN, MANAGER, SALES, KYC_REVIEWER, ACTIVATION, BILLING})
ACTIVATION_WRITE_ROLES = frozenset({ADMIN, ACTIVATION})
KYC_WRITE_ROLES = frozenset({ADMIN, KYC_REVIEWER})
ANALYTICS_ROLES = frozenset({ADMIN, MANAGER, ANALYST})
BILLING_ROLES = frozenset({ADMIN, BILLING})


@dataclass(frozen=True)
class Principal:
    kind: str
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    phone: str = ""
    token_hash: str = ""
    partner_code: str = ""


_PRINCIPAL: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("udyam_principal", default=None)
_JWK_CLIENT = None


def current_principal() -> Principal | None:
    return _PRINCIPAL.get()


def set_principal(principal: Principal | None):
    return _PRINCIPAL.set(principal)


def reset_principal(token) -> None:
    _PRINCIPAL.reset(token)


def has_any_role(principal: Principal | None, allowed: Iterable[str]) -> bool:
    return bool(principal and principal.kind == "staff" and principal.roles.intersection(allowed))


def verify_oidc_token(token: str) -> Principal:
    """Validate signature, issuer and audience against the configured JWKS."""
    global _JWK_CLIENT
    if not (SETTINGS.oidc_issuer and SETTINGS.oidc_audience and SETTINGS.oidc_jwks_url):
        raise ValueError("OIDC is not configured")
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise ValueError("OIDC validation package is unavailable") from exc
    if _JWK_CLIENT is None:
        _JWK_CLIENT = PyJWKClient(SETTINGS.oidc_jwks_url, cache_keys=True, lifespan=300)
    key = _JWK_CLIENT.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
        audience=SETTINGS.oidc_audience,
        issuer=SETTINGS.oidc_issuer,
        options={"require": ["exp", "iat", "iss", "sub"]},
        leeway=30,
    )
    raw_roles = claims.get(SETTINGS.oidc_role_claim, [])
    if isinstance(raw_roles, str):
        raw_roles = raw_roles.replace(",", " ").split()
    if not isinstance(raw_roles, list):
        raw_roles = []
    roles = frozenset(str(role) for role in raw_roles).intersection(STAFF_ROLES)
    if not roles:
        raise ValueError("Token has no UdyamOne staff role")
    partner_code = str(claims.get(SETTINGS.oidc_partner_code_claim, "")).strip().upper()
    if partner_code and (len(partner_code) > 24 or not all(c.isalnum() or c == "-" for c in partner_code)):
        raise ValueError("Token partner code is invalid")
    if PARTNER in roles and not partner_code:
        raise ValueError("Partner token has no partner code")
    return Principal(kind="staff", subject=str(claims["sub"]), roles=roles, partner_code=partner_code)
