# ── Stage 1: build dependencies ──────────────────────────────────
# Runtime uses 3.13 (newer than requires-python >= 3.11) for latest security patches.
# Pin to exact patch tag for reproducible builds.
FROM python:3.13.5-alpine3.21 AS builder

WORKDIR /build

# Install build deps once; the wheel cache survives into stage 2 only if
# we copy it explicitly.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: slim runtime ────────────────────────────────────────
FROM python:3.13.5-alpine3.21

RUN addgroup -S app && adduser -S app -G app

# Copy pre-built packages from the builder stage.
COPY --from=builder /install /usr/local

WORKDIR /app

COPY --chown=app:app server.py .
COPY --chown=app:app caldav_mcp/ ./caldav_mcp/

EXPOSE 8080

# TLS / HTTPS configuration (optional)
ENV CALDAV_MCP_TLS_CERT=""
ENV CALDAV_MCP_TLS_KEY=""
ENV CALDAV_MCP_TLS_CA_BUNDLE=""
# CalDAV server SSL verification (set to false only for testing with self-signed certs)
ENV CALDAV_MCP_CALDAV_VERIFY_SSL="true"
# Audit log format (text or json)
ENV CALDAV_MCP_LOG_FORMAT="text"

USER app

# Streamable HTTP endpoint on /mcp
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
  CMD python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',8080)); s.close()"

CMD ["python3", "server.py"]
