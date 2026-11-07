# syntax=docker/dockerfile:1.7
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    OCR_BACKEND=rapid \
    UDYAM_ENV=production \
    UDYAM_DEMO=false \
    ALLOW_MOCK_KYC=false \
    CUSTOMER_AUTH_REQUIRED=true \
    UDYAM_DB=/var/lib/udyamone/orders.db \
    UDYAM_CONSENT_LOG=/var/lib/udyamone/consent.jsonl

RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 udyamone \
    && useradd --system --uid 10001 --gid udyamone --home-dir /app udyamone

WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

# Explicit allowlist: organiser case documents, KYC samples, tests, databases,
# decks, archives and local build tools never enter the runtime image.
COPY app.py activation.py assistance.py config.py db.py engine.py integrations.py kyc.py planner.py providers.py rate_limit.py security.py whatsapp_bot.py ./
COPY static ./static
COPY alembic.ini docker-entrypoint.sh ./
COPY migrations ./migrations

RUN mkdir -p /app/data /var/lib/udyamone \
    && chmod 0555 /app/docker-entrypoint.sh \
    && chown -R udyamone:udyamone /app /var/lib/udyamone

USER 10001:10001
EXPOSE 8000
VOLUME ["/var/lib/udyamone"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/live', timeout=4)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
