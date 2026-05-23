# syntax=docker/dockerfile:1
# ── stage 1: build deps ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /build

RUN pip install --upgrade pip hatchling

COPY pyproject.toml .
RUN pip install --prefix=/install \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "cryptography>=43"

# ── stage 2: runtime ────────────────────────��──────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user
RUN groupadd --gid 1000 webhook && \
    useradd  --uid 1000 --gid webhook --shell /bin/bash --create-home webhook

COPY --from=builder /install /usr/local
COPY app/ /app/app/

WORKDIR /app
USER 1000

EXPOSE 8443

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8443", \
     "--ssl-certfile", "/certs/tls.crt", \
     "--ssl-keyfile",  "/certs/tls.key"]
