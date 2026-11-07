"""Runtime configuration with production fail-closed validation."""
from __future__ import annotations

import os
from dataclasses import dataclass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def demo_mode() -> bool:
    # Demo access must be explicitly enabled. The previous implicit true default
    # was safe only while the server was guaranteed to bind to localhost.
    return env_bool("UDYAM_DEMO", False)


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    auto_create_schema: bool
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_url: str
    oidc_role_claim: str
    oidc_partner_code_claim: str
    customer_auth_required: bool
    otp_pepper: str
    otp_delivery_url: str
    otp_delivery_token: str
    otp_session_hours: int
    partner_referral_secret: str
    max_request_bytes: int
    ocr_concurrency: int
    trust_proxy_headers: bool
    allow_mock_kyc: bool
    enable_synthetic_model: bool

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.environment not in {"development", "test", "staging", "production"}:
            raise RuntimeError("UDYAM_ENV must be development, test, staging or production")
        if not 1 <= self.ocr_concurrency <= 16:
            raise RuntimeError("OCR_CONCURRENCY must be between 1 and 16")
        if not 1024 * 1024 <= self.max_request_bytes <= 100 * 1024 * 1024:
            raise RuntimeError("MAX_REQUEST_BYTES must be between 1 MB and 100 MB")
        if not self.production:
            return
        if demo_mode():
            raise RuntimeError("UDYAM_DEMO must be false in production")
        if self.enable_synthetic_model:
            raise RuntimeError("ENABLE_SYNTHETIC_MODEL must be false in production")
        if self.auto_create_schema:
            raise RuntimeError("AUTO_CREATE_SCHEMA must be false in production; run Alembic migrations")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("Production DATABASE_URL must use PostgreSQL")
        missing = [
            name
            for name, value in {
                "OIDC_ISSUER": self.oidc_issuer,
                "OIDC_AUDIENCE": self.oidc_audience,
                "OIDC_JWKS_URL": self.oidc_jwks_url,
                "PARTNER_REFERRAL_SECRET": self.partner_referral_secret,
            }.items()
            if not value
        ]
        if self.customer_auth_required:
            missing.extend(
                name
                for name, value in {
                    "OTP_PEPPER": self.otp_pepper,
                    "OTP_DELIVERY_URL": self.otp_delivery_url,
                    "OTP_DELIVERY_TOKEN": self.otp_delivery_token,
                }.items()
                if not value
            )
        if missing:
            raise RuntimeError("Missing production identity configuration: " + ", ".join(missing))
        try:
            import jwt  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("PyJWT[crypto] is required for production OIDC validation") from exc


def load_settings() -> Settings:
    environment = os.getenv("UDYAM_ENV", "development").strip().lower()
    settings = Settings(
        environment=environment,
        database_url=os.getenv("DATABASE_URL", ""),
        auto_create_schema=env_bool("AUTO_CREATE_SCHEMA", environment != "production"),
        oidc_issuer=os.getenv("OIDC_ISSUER", "").rstrip("/"),
        oidc_audience=os.getenv("OIDC_AUDIENCE", ""),
        oidc_jwks_url=os.getenv("OIDC_JWKS_URL", ""),
        oidc_role_claim=os.getenv("OIDC_ROLE_CLAIM", "roles"),
        oidc_partner_code_claim=os.getenv("OIDC_PARTNER_CODE_CLAIM", "partner_code"),
        customer_auth_required=env_bool("CUSTOMER_AUTH_REQUIRED", environment == "production"),
        otp_pepper=os.getenv("OTP_PEPPER", ""),
        otp_delivery_url=os.getenv("OTP_DELIVERY_URL", ""),
        otp_delivery_token=os.getenv("OTP_DELIVERY_TOKEN", ""),
        otp_session_hours=int(os.getenv("OTP_SESSION_HOURS", "12")),
        partner_referral_secret=os.getenv("PARTNER_REFERRAL_SECRET", ""),
        max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", str(40 * 1024 * 1024))),
        ocr_concurrency=int(os.getenv("OCR_CONCURRENCY", "2")),
        trust_proxy_headers=env_bool("TRUST_PROXY_HEADERS", False),
        allow_mock_kyc=env_bool("ALLOW_MOCK_KYC", demo_mode()),
        enable_synthetic_model=env_bool("ENABLE_SYNTHETIC_MODEL", demo_mode()),
    )
    settings.validate()
    return settings


SETTINGS = load_settings()
