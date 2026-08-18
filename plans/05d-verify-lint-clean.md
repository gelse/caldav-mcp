# Plan 05d — Verify lint-clean state (no unused imports)

> Parent plan: [`05-unused-imports-cleanup.md`](05-unused-imports-cleanup.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Confirm that after 05a–05c the module has no unused imports or dead names, and run the test suite
to verify behavior is unchanged. Add no further code changes; this is a verification/finalization
step.

## Context you must know

- 05a removed the redundant local `icalendar` import in `caldav_update_event`.
- 05b simplified `_get_calendar`.
- 05c removed `_client`.
- The parent plan's acceptance criteria are: "No unused imports/warnings under a linter (e.g.
  `ruff`)" and "`icalendar` declared as a direct dependency."

## Implementation steps

### Step 1 — Check for unused imports

If `ruff` is available, run:

```bash
ruff check server.py
```

If `ruff` is not installed, fall back to:

```bash
python -m py_compile server.py
python -c "import ast, sys; src=open('server.py').read(); tree=ast.parse(src); print('AST parse OK')"
```

and manually confirm there are no obviously unused imports (all top-level imports in
[`server.py:1-10`](server.py:1) — `os`, `uuid`, `asyncio`, `datetime` members, `DAVClient`,
`Calendar`/`Event`, `vCalAddress`/`vText`, `FastMCP`, `get_http_headers` — are referenced
somewhere).

### Step 2 — Confirm `icalendar` is a direct dependency

Verify [`pyproject.toml`](pyproject.toml:7) contains `"icalendar>=6.0.0"` and
[`requirements.txt`](requirements.txt:1) contains `icalendar>=6.0.0`. (These are already present;
do not change them.)

### Step 3 — Run the full suite

```bash
python -m unittest discover -s tests -v
```

Confirm all tests pass with no regressions.

## Definition of done

- No unused-import warnings remain (via `ruff check server.py` if available, or manual AST/import
  confirmation otherwise).
- `icalendar` is declared as a direct dependency in both dependency files.
- Full test suite passes.

## Constraints / rules

- Do NOT introduce new dependencies or dev-dependencies in this step.
- Do NOT change [`server.py`](server.py) beyond what 05a–05c already did.
- If a linter surfaces issues beyond "unused imports" (e.g. style), note them for a later plan but
  do not fix them here.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Verify unused import cleanup and dependency declaration
```
