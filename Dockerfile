# Runtime uses 3.13 (newer than requires-python >= 3.11) for latest security patches.
# Pin to exact patch tag for reproducible builds.
FROM python:3.13.5-alpine3.21

RUN addgroup -S app && adduser -S app -G app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app server.py .
COPY --chown=app:app caldav_mcp/ ./caldav_mcp/

EXPOSE 8080

USER app

# Streamable HTTP endpoint on /mcp
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
  CMD python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',8080)); s.close()"

CMD ["python3", "server.py"]
