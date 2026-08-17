FROM python:3.13-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Run as root (caldav library needs no special perms, but keep simple for Bifrost STDIO spawn)
USER root

EXPOSE 8080

CMD ["python3", "server.py"]
