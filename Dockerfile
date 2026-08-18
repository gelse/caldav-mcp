FROM python:3.13-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY caldav_mcp/ ./caldav_mcp/

EXPOSE 8080

# Streamable HTTP endpoint on /mcp
CMD ["python3", "server.py"]
