# Plan 09a — Add explicit "Security" section with exposure warning

> Parent plan: [`09-readme-security-docs.md`](./09-readme-security-docs.md)

## Objective

Add a new "Security" section to [`README.md`](../README.md) that warns against exposing the MCP
endpoint publicly and recommends a reverse proxy / TLS. This is a documentation-only change.

## Context you must know

The current README states "[`No authentication`](../README.md:12) on the MCP endpoint" but does not
tell users the risks of exposing a CalDAV proxy with read/write access. This sub-plan covers only
step 1 of the parent plan (the warning + reverse-proxy/TLS guidance). The auth-token documentation
is a separate sub-plan (09b) because it depends on issue #03 landing.

The README is structured as follows:

- `# caldav-mcp` — intro
- `## Design` (lines 9-17) — transport + credentials
- `## Config` (lines 19-24) — env table
- `## Tools` (26-43) — tool table
- `## Development` (45-58)
- `## Docker Compose` (60-74)
- `## MCP client (Streamable HTTP)` (76-78)
- `## Example call` (80-89)
- `## License` (91-93)

## Chosen mechanism (do not deviate)

Insert a new top-level `## Security` section immediately after the `## Design` section (i.e. between
line 17 and line 19, before `## Config`). Do not reorder or modify any other section's content.

## Implementation steps

### Step 1 — Open the README

Edit [`README.md`](../README.md).

### Step 2 — Insert the "Security" section

Place the following block directly after the `## Design` section and before `## Config`:

```markdown
## Security

The MCP endpoint has **no built-in authentication** and grants read/write access to any
CalDAV calendar you configure. **Do not expose it directly to the public internet.**

- Put the server behind a reverse proxy (e.g. Traefik, Caddy, nginx) that terminates **TLS**.
- Restrict access at the network/firewall layer to trusted hosts or a private VPN.
- Prefer binding the container port to `127.0.0.1` unless you explicitly need remote access.
- Never place CalDAV app passwords or the endpoint in public configuration or logs.
```

### Step 3 — Verify

Confirm the new section sits between `## Design` and `## Config`, and that no existing section was
altered. Re-read the file top-to-bottom to confirm the headings order is unchanged except for the
insertion.

## Definition of done

- [ ] [`README.md`](../README.md) has a `## Security` section.
- [ ] The section warns against public exposure and recommends a reverse proxy with TLS.
- [ ] The section mentions network/firewall restriction and not binding to `0.0.0.0`.
- [ ] No other section of the README was modified.

## Constraints / rules

- Documentation only — do **not** change `server.py` or any other source/configuration file.
- Do **NOT** document the auth token in this sub-plan; that is 09b.
- Match existing Markdown style (ATX headings, bullet lists).

## Commit

After the file change completes, run:

```bash
git add README.md && git commit -m "Add security section to README"
```
