# Plan 06a — Determine current resolved dependency versions

> Parent plan: [`06-dependency-pinning.md`](06-dependency-pinning.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Capture the currently working, resolved versions of the project's direct dependencies so later
sub-steps (06b, 06c) can pin exact versions. This step is read-only: no files are modified.

## Context you must know

- [`requirements.txt`](requirements.txt:1) currently declares loose constraints:
  ```
  icalendar>=6.0.0
  caldav>=1.3.7
  fastmcp>=3.4.0
  ```
- [`pyproject.toml`](pyproject.toml:6) mirrors these in the `dependencies` list:
  ```toml
  dependencies = [
      "icalendar>=6.0.0",
      "caldav>=1.3.7",
      "fastmcp>=3.4.0",
  ]
  ```
- The goal is reproducibility: pin the exact installed versions (or `~=` compatible-release),
  and optionally produce a lockfile (`pip freeze` output) later.

## Implementation steps

### Step 1 — Resolve installed versions

From the workspace root, run:

```bash
python -m pip freeze
```

This emits the full environment. Record the exact versions for the three direct dependencies:

- `icalendar`
- `caldav`
- `fastmcp`

(If `pip freeze` is unavailable, fall back to:

```bash
python -c "import icalendar, caldav, fastmcp; print(icalendar.__version__); print(caldav.__version__); print(fastmcp.__version__)"
```

using whichever version attributes those packages expose.)

### Step 2 — Record the versions

Record the three exact `name==version` lines in your notes (e.g. in a scratch file or the
output you will use in 06b). Do **not** write to `requirements.txt` or `pyproject.toml` in this
step.

## Definition of done

- Exact current versions of `icalendar`, `caldav`, and `fastmcp` are captured (via `pip freeze`
  or per-package `__version__`).
- No repository file has been modified.

## Constraints / rules

- This step is observation only; do not edit any file.
- Do not install or upgrade anything (no `pip install`).
- Do not deviate from this plan.

## Commit

This is a read-only step; no commit is needed (nothing changed). Proceed to 06b.
