# Secure pilot deployment

This platform-neutral foundation is suitable for controlled engineering and provider-acceptance work. It does not claim live Airtel provisioning, legal KYC or production charging.

## Implemented foundation

- Production configuration fails closed.
- Demo mode, mock KYC, missing OIDC settings or a non-PostgreSQL database prevent production startup.
- Staff identity uses signed OIDC access tokens and explicit `udyamone_*` roles.
- Customer identity uses short-lived phone OTP sessions through an approved delivery-gateway abstraction.
- PostgreSQL 17 and Alembic replace automatic production schema creation.
- Customer order payloads omit staff evidence and event details.
- Consent events are persisted in the database; JSONL remains a local-demo compatibility log.
- OCR runs outside the ASGI event loop with bounded concurrency, image validation and request limits.
- Sensitive API responses are non-cacheable and receive additional browser security headers.
- The production container runs as a non-root user, uses a read-only filesystem, drops Linux capabilities and copies only runtime files.
- Runtime and development dependencies have hash-locked files.

## Staff roles

The configured OIDC role claim can contain:

- `udyamone_admin`
- `udyamone_manager`
- `udyamone_sales`
- `udyamone_kyc`
- `udyamone_activation`
- `udyamone_partner`
- `udyamone_billing`
- `udyamone_analyst`

Route policies restrict order reading, activation events, analytics and payment reconciliation. Order assignment and partner-specific data scopes still require Airtel's authoritative organisation and partner directory.

## Configure and start

1. Copy `.env.production.example` to `.env.production` outside source control.
2. Replace every example using a secrets manager.
3. Supply PostgreSQL, OIDC, OTP-delivery and OTP-pepper values.
4. Mount an authorised catalogue source at `/app/data`. Challenge CSVs are not production tariffs.
5. Keep mock KYC, live payment and provisioning disabled until acceptance is complete.

```bash
docker compose --env-file .env.production -f compose.production.yaml up --build
```

The service binds to `127.0.0.1:8000`; put an approved HTTPS gateway/WAF in front of it. The container runs `alembic upgrade head`. Set `AUTO_MIGRATE=false` where migrations run as a separate controlled release job.

## Identity flow

- Staff: signed bearer token with valid issuer, audience, expiry, JWKS signature and an allowed role. The current UI accepts a token for pilot testing; replace manual entry with Authorization Code + PKCE or a BFF session before general use.
- Customer: request a six-digit code, verify it and use the returned opaque bearer session. Demo mode exposes a local test code; production sends it only through `OTP_DELIVERY_URL`.
- The accepted order phone must match the verified customer phone when `CUSTOMER_AUTH_REQUIRED=true`.

## Remaining live-pilot gates

1. Airtel product, tariff, tax, discount and quote-expiry APIs.
2. Airtel serviceability, scheduling, provisioning, cancellation and activation callbacks.
3. Approved GST/Udyam/signatory/address policy and KYC provider acceptance tests.
4. Razorpay production acceptance, invoices, refunds and settlement reconciliation.
5. Meta account, templates, delivery monitoring and a durable outbox worker.
6. Partner directory and partner-scoped signed referral attribution.
7. Retention, rights requests, incident response, DPIA/legal approval and processor contracts.
8. Gateway-wide distributed limits, durable OCR jobs, malware scanning and encrypted temporary object storage.
9. Independent penetration, load, accessibility, localization and disaster-recovery tests.

Until these gates close, process synthetic records only and call the environment a controlled technical pilot.
