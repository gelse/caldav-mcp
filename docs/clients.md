# MCP Client Configuration

> **Streamable HTTP only** — the server does not support stdio transport.
> Any MCP client that supports Streamable HTTP can connect.

## Standard configuration

The standard configuration format with per-request CalDAV credentials:

```json
{
  "mcpServers": {
    "caldav": {
      "type": "http",
      "url": "http://localhost:8600/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY",
        "X-Caldav-Url": "https://cloud.example.com/remote.php/dav/calendars/user/",
        "X-Caldav-Username": "user",
        "X-Caldav-Password": "app-password"
      }
    }
  }
}
```

## Client-specific configuration

### Claude Desktop

Config file location:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Uses the `mcpServers` key. Custom Connectors added via the UI require a paid plan.

### Claude Code

Config file locations:

- **Global**: `~/.claude/settings.json`
- **Project**: `.mcp.json` (in project root)

Uses the `mcpServers` key. You can also add via CLI:

```bash
claude mcp add --transport http caldav http://localhost:8600/mcp
```

> **Note**: The CLI does not support setting custom headers. Add the
> `headers` block manually in the JSON config after using the CLI command.

### Cursor

Config file locations:

- **Project**: `.cursor/mcp.json`
- **Global**: `~/.cursor/mcp.json`

Uses the `mcpServers` key.

### VS Code

Config file location: `.vscode/mcp.json`

**Uses the `servers` key**, not `mcpServers`:

```json
{
  "servers": {
    "caldav": {
      "type": "http",
      "url": "http://localhost:8600/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY",
        "X-Caldav-Url": "https://cloud.example.com/remote.php/dav/calendars/user/",
        "X-Caldav-Username": "user",
        "X-Caldav-Password": "app-password"
      }
    }
  }
}
```

### OpenCode

Config file location: project root (e.g. `opencode.json`).

Uses the `mcpServers` key with the standard format shown above.

### OpenWebUI

Configure via **Admin Panel → Settings → Connections**. Add the MCP server
URL and headers through the UI.

## Multiple CalDAV accounts

Because credentials travel per-request in HTTP headers, a single server
instance can serve multiple CalDAV accounts. Configure each MCP client
connection with different `X-Caldav-*` headers.
