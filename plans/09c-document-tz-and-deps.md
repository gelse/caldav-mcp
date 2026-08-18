# Plan 09c — Document timezone env var and dependency/version notes

> Parent plan: [`09-readme-security-docs.md`](./09-readme-security-docs.md)

## Objective

Update [`README.md`](../README.md) to document the timezone environment variable (from issue #02)
and any dependency/version notes (from issue #06).

## Context you must know

This sub-plan corresponds to step 3 of the parent plan and **depends on issues #02 and #06** having
landed:

- **Issue #02 (timezone)** — plan [`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md).
  The implementation introduces a server timezone. Use the exact env var name introduced there
  (e.g. `CALDAV_TZ` or `SERVER_TZ` — confirm against the implemented `server.py` before writing).
- **Issue #06 (dependency pinning)** — plan [`06-dependency-pinning.md`](./06-dependency-pinning.md).
  The implementation pins exact versions in `requirements.txt` and `pyproject.toml`. The README's
  `## Development` section already refers to dependencies (lines 46-58); update it to note that
  versions are pinned.

Relevant existing README content:

- `## Development` section at [`README.md:45`](../README.md:45).
- The config table at [`README.md:21`](../README.md:21).

## Chosen mechanism (do not deviate)

- Add the timezone env var to the `## Config` table and briefly describe its effect.
- Add a short note in `## Development` that dependency versions are pinned; do not enumerate exact
  versions (they are maintained in `requirements.txt` / `pyproject.toml`).

## Implementation steps

### Step 1 — Confirm the exact env var name

Open [`server.py`](../server.py) and confirm the exact environment variable name used for the server
timezone (search for `os.environ` / a `*_TZ` constant). Use that literal in the README.

### Step 2 — Update the "Config" table

In [`README.md:21`](../README.md:21), add a row, e.g.:

```markdown
| `CALDAV_TZ` | `UTC` | Server timezone used to interpret date-only input and day boundaries |
```

(Substitute the exact var name and default confirmed in Step 1.)

### Step 3 — Add a dependency-pinning note

In the `## Development` section, after the existing dependency sentence, add:

```markdown
  Dependency versions are **pinned** in `requirements.txt` and `pyproject.toml` for reproducible
  builds; update both files together when bumping a dependency.
```

### Step 4 — Verify

Re-read the README and confirm the timezone env var name and the pinning note match the
implemented code and the pinned files.

## Definition of done

- [ ] `## Config` table documents the timezone env var with its correct name/default.
- [ ] `## Development` notes that dependency versions are pinned and both files must be updated.
- [ ] Names match the implemented `server.py` and the pinned `requirements.txt`/`pyproject.toml`.

## Constraints / rules

- Documentation only. Do not modify source or config files.
- Only proceed if issues #02 (timezone) and #06 (pinning) have landed; otherwise mark blocked.

## Commit

```bash
git add README.md && git commit -m "Document timezone env and dependency pinning"
```
