# Plan 10 — Fix Documentation Drift

> **Status: ✅ Completed** — All three issues resolved.

## Context

Three documentation accuracy issues were identified during codebase audit. This
plan addresses each with minimal-risk changes, ordered from smallest to largest
scope.

---

## Issue 1 — `CALDAV_MCP_PORT` / `CALDAV_MCP_PATH` startup-only semantics

### Problem

[`caldav_mcp/config.py:15-16`](caldav_mcp/config.py:15) reads these env vars at
import time as module-level constants. The README config table and
[`docker-compose.yaml`](docker-compose.yaml:12) present them as env vars without
clarifying they only take effect at process start. Changing them at runtime has no
effect.

### Trade-off analysis

| Option | Pros | Cons |
|--------|------|------|
| **A.** Add comment + README note | Zero code change risk; no re-export impact | Slightly less "obvious" in code |
| **B.** Move resolution into [`main()`](server.py:131) | Semantically cleaner | Requires updating re-exports in [`caldav_mcp/__init__.py:62-63`](caldav_mcp/__init__.py:62) and [`server.py:84-85`](server.py:84); changes public API surface; no tests currently verify these values |

**Decision: Option A.** No tests reference `DEFAULT_PORT` or `DEFAULT_PATH`
directly, so the re-export chain is not under test pressure, but changing it is
unnecessary risk for a documentation fix.

### Steps

1. **[`caldav_mcp/config.py`](caldav_mcp/config.py:15)** — Add a comment on
   lines 15-16 explaining these are resolved once at import time and take effect
   only at process start:

   ```python
   # NOTE: resolved once at import time — env changes after startup are ignored.
   DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
   DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")
   ```

2. **[`README.md:40-41`](README.md:40)** — Update the Config table rows for
   `CALDAV_MCP_PORT` and `CALDAV_MCP_PATH` to add a note:

   | Env | Default | Description |
   |---|---|---|
   | `CALDAV_MCP_PORT` | `8080` | Listen port (inside container). **Startup only** — changing at runtime has no effect. |
   | `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP path. **Startup only** — changing at runtime has no effect. |

### Verification

- Run `make lint` — no new lint errors from the comment change.
- Visual inspection: README config table matches actual behavior.
- `docker compose config` still resolves the env vars correctly (no code change
  to compose or env handling).

---

## Issue 2 — Duplicate dependency sources

### Problem

[`requirements.txt`](requirements.txt) and [`pyproject.toml:6-9`](pyproject.toml:6)
both pin the same three deps with identical versions. The Dockerfile installs from
`requirements.txt`; development installs use `pyproject.toml`. There is no
automated check to catch drift.

### Trade-off analysis

| Option | Pros | Cons |
|--------|------|------|
| **A.** Generate `requirements.txt` from `pyproject.toml` via `pip-compile` | Single source of truth | Adds `pip-tools` as a dev dependency; changes workflow |
| **B.** Add `make deps-check` target | No new dependencies; lightweight; CI-friendly | Still two files to maintain, but drift is caught |

**Decision: Option B.** Lower risk, no new dependencies, and the two-file approach
is common for projects that need both `pyproject.toml` (for editable installs)
and `requirements.txt` (for Docker builds).

### Steps

1. **[`Makefile`](Makefile)** — Add a `deps-check` target that extracts the
   pinned runtime deps from both files and compares them. Add it to the `check`
   target's dependency list.

   ```makefile
   .PHONY: deps-check
   deps-check:
   	@echo "Checking dependency consistency …"
   	@# Extract pinned runtime deps from pyproject.toml (lines inside dependencies = [...])
   	@PY_DEPS=$$(python3 -c "\
   	 import tomllib, json; \
   	 f=open('pyproject.toml','rb'); \
   	 d=tomllib.load(f); \
   	 print(json.dumps(sorted(d['project']['dependencies'])))" 2>/dev/null || \
   	 python3 -c "\
   	 import toml; \
   	 d=toml.load('pyproject.toml'); \
   	 print(json.dumps(sorted(d['project']['dependencies'])))" 2>/dev/null); \
   	REQ_DEPS=$$(python3 -c "import json; lines=[l.strip() for l in open('requirements.txt') if l.strip()]; print(json.dumps(sorted(lines)))"); \
   	if [ "$$PY_DEPS" = "$$REQ_DEPS" ]; then \
   	 echo "✓ dependencies match"; \
   	else \
   	 echo "✗ MISMATCH between pyproject.toml and requirements.txt"; \
   	 echo "  pyproject.toml: $$PY_DEPS"; \
   	 echo "  requirements.txt: $$REQ_DEPS"; \
   	 exit 1; \
   	fi
   ```

   > **Note:** `tomllib` is available in Python 3.11+ (the project minimum).
   > The `toml` fallback handles older interpreters but should not be needed.

2. **[`Makefile`](Makefile:19)** — Update the `check` target to include
   `deps-check`:

   ```makefile
   .PHONY: check
   check: lint typecheck deps-check test
   ```

3. **[`README.md:75-79`](README.md:75)** — Update the dependencies section to
   mention the consistency check:

   ```markdown
   - **Dependencies**: installed from `requirements.txt` (or via the `dependencies`
     list in `pyproject.toml`). … Dependency versions are **pinned** in both files
     for reproducible builds; `make deps-check` verifies they stay in sync.
   ```

### Verification

- Run `make deps-check` — should print `✓ dependencies match`.
- Temporarily edit `requirements.txt` to bump one version, re-run
  `make deps-check` — should fail with a clear mismatch message.
- Run `make check` — should include `deps-check` in the pipeline.

---

## Issue 3 — README test section wording

### Problem

[`README.md:80-91`](README.md:80) mentions both `pytest` and `python -m unittest
discover` as test runners. The suite is pytest-based (the Makefile uses pytest,
CI uses pytest), but the wording implies equal status. The `unittest` command is a
valid fallback but should be clearly secondary.

### Steps

1. **[`README.md:80-91`](README.md:80)** — Rewrite the tests paragraph to
   clarify pytest as the primary runner and unittest as an optional fallback:

   ```markdown
   - **Tests**: unit tests live in [`tests/`](./tests) and run with `pytest` via
     the Makefile:

     ```bash
     make test
     ```

     This uses the project virtual environment at `./.venv`
     (`./.venv/bin/pytest`). The standard library `unittest` runner also works
     but is not used in CI:

     ```bash
     python -m unittest discover -s tests -v
     ```

     The suite covers escaping of special characters (`\`, `,`, `;`, newlines),
     attendees, priority/rrule validation, and edge cases (emoji, empty optional
     fields) for `caldav_create_event`.
   ```

### Verification

- Visual inspection: README reflects actual CI behavior (`make check` → pytest).
- Run `make test` to confirm pytest still works.

---

## Execution order

1. Issue 1 (config comment + README table) — smallest, zero risk
2. Issue 3 (README test wording) — documentation only
3. Issue 2 (Makefile deps-check + README) — adds new make target

Each step should be committed individually:

| Commit | Message |
|--------|---------|
| 1 | `Document startup-only semantics for PORT/PATH env vars` |
| 2 | `Clarify pytest as primary test runner in README` |
| 3 | `Add make deps-check to verify requirements.txt/pyproject.toml consistency` |

---

## Files modified

| File | Changes |
|------|---------|
| [`caldav_mcp/config.py`](caldav_mcp/config.py:15) | Add comment on lines 15-16 |
| [`README.md`](README.md:40) | Config table note (Issue 1), test section rewrite (Issue 3), deps note (Issue 2) |
| [`Makefile`](Makefile:19) | Add `deps-check` target; update `check` dependency list |
