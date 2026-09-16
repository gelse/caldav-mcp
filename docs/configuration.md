# Configuration Reference

All configuration is via environment variables, validated at startup with
Pydantic.

## Environment variables

### Server

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container) |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `CALDAV_MCP_API_KEY` | `""` (disabled) | Shared secret for MCP endpoint auth |
| `CALDAV_MCP_READ_ONLY` | `false` | Hide write tools; only query tools are exposed when `true` |
| `CALDAV_MCP_CONFIG_SECRET` | `""` | Master secret for encrypting CalDAV passwords at rest in the SQLite store. Required by the CLI (encrypt) and server (decrypt). Changing it invalidates stored ciphertexts. |
| `CALDAV_MCP_DB_PATH` | `""` | Path to the SQLite configuration store. Required by the CLI and by the server in pro mode; `--db` CLI flag takes precedence. |
| `DB_CONFIG_ENABLED` | `false` | Enable pro mode: load config and users from the SQLite store; requires `CALDAV_MCP_DB_PATH` and `CALDAV_MCP_CONFIG_SECRET`; `CALDAV_MCP_API_KEY` is ignored. |
| `TZ` | `""` (UTC) | IANA timezone (e.g. `Europe/Vienna`) for today/week boundaries |

### CalDAV

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_URL` | `""` | CalDAV server URL. When set, request headers are ignored and all credentials come from env (environment mode). When unset, `X-Caldav-*` headers are required (header mode). |
| `CALDAV_USERNAME` | `""` | CalDAV username. Used in environment mode; ignored in header mode. |
| `CALDAV_PASSWORD` | `""` | CalDAV password. Used in environment mode; ignored in header mode. |
| `CALDAV_MCP_CALDAV_VERIFY_SSL` | `true` | Verify TLS certs on CalDAV connections. Set `false` only for testing with self-signed certs. |

### TLS

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_TLS_CERT` | `""` | Path to TLS certificate PEM file |
| `CALDAV_MCP_TLS_KEY` | `""` | Path to TLS private key PEM file |
| `CALDAV_MCP_TLS_CA_BUNDLE` | `""` | Optional CA bundle for custom certificate authorities |

### Rate limiting

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` | `10` | Max failed auth attempts per IP within the sliding window |
| `CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window duration in seconds |

### Logging

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_LOG_FORMAT` | `text` | Audit log format: `text` or `json` |

## Deployment patterns

### Install from a release

```bash
git clone --branch v0.1.0 https://github.com/gelse/caldav-mcp.git
cd caldav-mcp
cp .env.example .env
# Edit .env with your CalDAV credentials
docker compose up -d
```

### Local / private deployment

The simplest setup — AI client and caldav-mcp on the same machine:

```
AI Client → http://localhost:8600/mcp → CalDAV Server
```

```bash
docker compose up -d
```

The server listens on `localhost:8600` and is not accessible from the
network unless you explicitly publish the port.

### Remote / shared deployment

For multi-user or remote access, put the server behind a TLS-terminating
reverse proxy:

```
AI Client → HTTPS → reverse proxy → caldav-mcp → CalDAV Server
```

```nginx
server {
    listen 443 ssl;
    server_name caldav-mcp.example.com;

    ssl_certificate     /etc/ssl/certs/caldav-mcp.pem;
    ssl_certificate_key /etc/ssl/private/caldav-mcp-key.pem;

    location /mcp {
        proxy_pass http://127.0.0.1:8600/mcp;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Built-in TLS

If you prefer not to use a reverse proxy, enable built-in TLS:

```bash
CALDAV_MCP_TLS_CERT=/path/to/cert.pem \
CALDAV_MCP_TLS_KEY=/path/to/key.pem \
docker compose up -d
```

> ⚠️ **Do not expose the MCP endpoint publicly without both authentication
> and TLS.** Without `CALDAV_MCP_API_KEY` set, the endpoint is open. Without
> TLS, all traffic — including API keys and CalDAV passwords — is transmitted
> in plaintext.

## Deployment recommendations

- Bind to `127.0.0.1` or a private network unless you need remote access.
- Restrict access at the network/firewall layer to trusted hosts or a VPN.
- Never commit CalDAV app passwords to version control.
- Use a reverse proxy for TLS termination in production.
- For read-only deployments, set `CALDAV_MCP_READ_ONLY=true`.
