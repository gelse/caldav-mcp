# CLI Reference — Config Store

The `caldav-mcp-config` CLI manages the SQLite-backed configuration store.
This store is designed for multi-user, multi-server deployments where a
single server instance must route requests to different CalDAV accounts.

> **How the CLI and server work together:**
> The CLI manages the SQLite store; the server reads it at startup in pro
> mode (`DB_CONFIG_ENABLED=true`). The `CALDAV_MCP_CONFIG_SECRET` must
> **match** between CLI and server — the CLI encrypts passwords with it, and
> the server decrypts them on startup. Config changes require a server
> restart (the store is frozen into an immutable `AppConfig` at startup).
> In simple mode the server continues to use environment variables or
> per-request headers as described in [`docs/api.md`](api.md).

## Concepts

The store holds four entity types that mirror the M2 dataclasses
(`Config`, `Remote`, `Calendar`) in [`caldav_mcp/app_config.py`](../caldav_mcp/app_config.py):

| Entity | Description |
|--------|-------------|
| **Config** | A named configuration scope (e.g. `work`, `personal`). Owns remotes and calendars. |
| **Remote** | A CalDAV server endpoint attached to a config. Has an auth mode: `direct` (username + password) or `passthrough` (credentials supplied by the client at request time). |
| **Calendar** | A named calendar within a remote, referenced by `<remote>.<calendar>` dotted-path addressing (planned M4). |
| **User** | An API key holder with a username. Users are granted access to one or more configs. |

### Validation rules

- Config, remote, and calendar names must **not** contain dots (`.`).
- Across all remotes a user can access, at most one may use `passthrough` auth mode.

## Requirements

| Variable | Description |
|----------|-------------|
| `CALDAV_MCP_CONFIG_SECRET` | Master secret for encrypting CalDAV passwords at rest. **Required** for any command that stores a password (`remote add` in direct mode). The server will require this in pro mode (M4) to decrypt. Changing the secret invalidates all stored ciphertexts — re-add remotes after a change. |
| `CALDAV_MCP_DB_PATH` | Default path to the SQLite database file. The `--db` flag takes precedence. |

## Usage

```
caldav-mcp-config [--db DB_PATH] <noun> <verb> [flags]
```

Or via the module entry point:

```
python -m caldav_mcp.config_cli [--db DB_PATH] <noun> <verb> [flags]
```

The `--db` flag (or `CALDAV_MCP_DB_PATH` env var) specifies the SQLite
file path. It is required unless set via the env var.

## Command reference

### user

Manage users.

#### `user add`

Add a user with an API key.

| Flag | Required | Description |
|------|----------|-------------|
| `--username` | yes | Username |
| `--key` | yes | API key (must be non-empty) |

```bash
caldav-mcp-config --db store.db user add --username alice --key my-secret-key
```

The API key is stored as a PBKDF2-HMAC-SHA256 hash (see [Key hashing](#key-hashing)).
The plaintext key is never stored.

#### `user list`

List all usernames.

```bash
caldav-mcp-config --db store.db user list
```

#### `user show`

Show user details and the configs they have access to.

| Flag | Required | Description |
|------|----------|-------------|
| `--username` | yes | Username |

```bash
caldav-mcp-config --db store.db user show --username alice
```

#### `user delete`

Delete a user and all associated grants (cascading).

| Flag | Required | Description |
|------|----------|-------------|
| `--username` | yes | Username |

```bash
caldav-mcp-config --db store.db user delete --username alice
```

#### `user grant`

Grant a user access to a config.

| Flag | Required | Description |
|------|----------|-------------|
| `--username` | yes | Username |
| `--config` | yes | Config name |

```bash
caldav-mcp-config --db store.db user grant --username alice --config work
```

#### `user revoke`

Revoke a user's access to a config.

| Flag | Required | Description |
|------|----------|-------------|
| `--username` | yes | Username |
| `--config` | yes | Config name |

```bash
caldav-mcp-config --db store.db user revoke --username alice --config work
```

### config

Manage configs.

#### `config add`

Create a new config.

| Flag | Required | Description |
|------|----------|-------------|
| `--name` | yes | Config name |

```bash
caldav-mcp-config --db store.db config add --name work
```

#### `config list`

List all config names.

```bash
caldav-mcp-config --db store.db config list
```

#### `config show`

Show config details: remotes and their calendars (as dotted paths).

| Flag | Required | Description |
|------|----------|-------------|
| `--name` | yes | Config name |

```bash
caldav-mcp-config --db store.db config show --name work
```

#### `config delete`

Delete a config. Fails if the config is non-empty unless `--force` is used.

| Flag | Required | Description |
|------|----------|-------------|
| `--name` | yes | Config name |
| `--force` | no | Delete even if non-empty (cascades to remotes and calendars) |

```bash
caldav-mcp-config --db store.db config delete --name work --force
```

### remote

Manage remotes within a config.

#### `remote add`

Add a CalDAV remote to a config.

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |
| `--name` | yes | Remote name |
| `--url` | yes | CalDAV server URL |
| `--auth-mode` | yes | `direct` or `passthrough` |
| `--username` | direct only | CalDAV username (required for direct mode) |
| `--password` | direct only | CalDAV password. Use `-` to read from stdin. |
| `--password-env` | direct only | Read password from the named environment variable |

**Direct mode** (username + password):

```bash
caldav-mcp-config --db store.db remote add \
  --config work --name nextcloud \
  --url https://cloud.example.com/remote.php/dav/calendars/alice/ \
  --auth-mode direct \
  --username alice \
  --password s3cret
```

Reading the password from stdin to keep it out of shell history:

```bash
echo -n 's3cret' | caldav-mcp-config --db store.db remote add \
  --config work --name nextcloud \
  --url https://cloud.example.com/remote.php/dav/calendars/alice/ \
  --auth-mode direct \
  --username alice \
  --password -
```

Reading the password from an environment variable:

```bash
export CALDAV_PWD='s3cret'
caldav-mcp-config --db store.db remote add \
  --config work --name nextcloud \
  --url https://cloud.example.com/remote.php/dav/calendars/alice/ \
  --auth-mode direct \
  --username alice \
  --password-env CALDAV_PWD
```

**Passthrough mode** (no stored credentials — the client supplies them at
request time):

```bash
caldav-mcp-config --db store.db remote add \
  --config personal --name shared-server \
  --url https://dav.example.com/ \
  --auth-mode passthrough
```

#### `remote list`

List remotes in a config.

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |

```bash
caldav-mcp-config --db store.db remote list --config work
```

#### `remote delete`

Delete a remote (cascades to its calendars).

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |
| `--name` | yes | Remote name |

```bash
caldav-mcp-config --db store.db remote delete --config work --name nextcloud
```

### calendar

Manage calendars within a remote.

#### `calendar add`

Add a calendar to a remote.

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |
| `--remote` | yes | Remote name |
| `--name` | yes | Calendar name |

```bash
caldav-mcp-config --db store.db calendar add \
  --config work --remote nextcloud --name meetings
```

#### `calendar list`

List calendars (as dotted paths `<remote>.<calendar>`).

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |
| `--remote` | no | Filter by remote name |

```bash
caldav-mcp-config --db store.db calendar list --config work
caldav-mcp-config --db store.db calendar list --config work --remote nextcloud
```

#### `calendar delete`

Delete a calendar.

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | yes | Config name |
| `--remote` | yes | Remote name |
| `--name` | yes | Calendar name |

```bash
caldav-mcp-config --db store.db calendar delete \
  --config work --remote nextcloud --name meetings
```

## Secrets handling

The CLI never prints passwords or key hashes to stdout or stderr.
Prefer one of these methods over bare `--password <value>` to keep
secrets out of shell history:

1. **Stdin** — `--password -` reads one line from stdin.
2. **Environment variable** — `--password-env VAR` reads from the named
   env var. The CLI reads it once and does not persist the value.

## Key hashing

API keys are stored as **PBKDF2-HMAC-SHA256** hashes (600 000 iterations,
16-byte per-user salt) in a self-describing format:

```
pbkdf2_sha256$600000$<salt_b64>$<hash_b64>
```

Two calls with the same key produce different salts (and therefore
different stored strings) but both verify correctly. Verification uses
constant-time comparison. Key verification happens in pro mode (M4).

## Encryption at rest

CalDAV passwords stored in direct-mode remotes are encrypted with
**Fernet** (authenticated encryption from the `cryptography` library).
The encryption key is derived via SHA-256 of the `CALDAV_MCP_CONFIG_SECRET`
environment variable.

- The **CLI encrypts** passwords when writing to the store.
- The **server decrypts** passwords when reading in pro mode (M4).
- User API keys are **hashed** (never encrypted) — see [Key hashing](#key-hashing).

## Examples — end-to-end walkthrough

The following creates a complete store with two configs, two users, and
grant assignments:

```bash
# Use a temporary store for this walkthrough
STORE=/tmp/caldav-mcp-demo.db
rm -f "$STORE"
export CALDAV_MCP_CONFIG_SECRET='demo-secret-do-not-use-in-production'

CLI="caldav-mcp-config --db $STORE"

# 1. Create a config
$CLI config add --name work

# 2. Add a direct remote (password from env)
export CALDAV_PWD='s3cret'
$CLI remote add \
  --config work --name nextcloud \
  --url https://cloud.example.com/remote.php/dav/calendars/alice/ \
  --auth-mode direct \
  --username alice \
  --password-env CALDAV_PWD

# 3. Add calendars to the remote
$CLI calendar add --config work --remote nextcloud --name meetings
$CLI calendar add --config work --remote nextcloud --name personal

# 4. Inspect the config
$CLI config show --name work
# Output:
#   nextcloud.meetings
#   nextcloud.personal

# 5. Add a passthrough remote to a second config
$CLI config add --name personal
$CLI remote add \
  --config personal --name shared \
  --url https://dav.example.com/ \
  --auth-mode passthrough

# 6. Create users
$CLI user add --username alice --key alice-key-123
$CLI user add --username bob --key bob-key-456

# 7. Grant access
$CLI user grant --username alice --config work
$CLI user grant --username alice --config personal
$CLI user grant --username bob --config personal

# 8. Inspect users
$CLI user show --username alice
# Output:
#   alice
#   config: work
#   config: personal

$CLI user show --username bob
# Output:
#   bob
#   config: personal

# 9. Clean up demo store
rm -f "$STORE"
```

## Limitations / non-goals

- **No `remote update`** — to change a remote's URL or credentials,
  delete it and re-add.
- **No key rotation** — there is no built-in flow to rotate
  `CALDAV_MCP_CONFIG_SECRET` or user API keys. Re-add remotes / re-create
  users after a secret change.
- **No hot reload** — the server reads configuration at startup. Changes
  to the store require a server restart (inherited from the singleton
  design of [`caldav_mcp/app_config.py`](../caldav_mcp/app_config.py)).

## Cross-references

- **Environment variables**: see [`docs/configuration.md`](configuration.md) for the full env-var table.
- **Pro mode behavior**: see [`docs/pro-mode.md`](pro-mode.md) and [`docs/architecture.md`](architecture.md) for fan-out, auth, and dotted-path details.
- **Wire format**: see [`docs/api.md`](api.md) for headers, addressing, and the aggregated result shape.
- **MCP client setup**: see [`docs/clients.md`](clients.md) for client-specific configuration.
