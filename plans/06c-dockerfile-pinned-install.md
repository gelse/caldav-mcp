# Plan 06c — Update Dockerfile install and verify build

> Parent plan: [`06-dependency-pinning.md`](06-dependency-pinning.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Ensure the Docker build installs the pinned dependency set reproducibly, and verify `docker build`
succeeds against the pinned [`requirements.txt`](requirements.txt:1). Assumes 06b is complete.

## Context you must know

- [`Dockerfile`](Dockerfile:5) currently does:
  ```dockerfile
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  ```
  This already installs from [`requirements.txt`](requirements.txt:1), so no functional change is
  strictly required — the pinning in 06b makes it reproducible.
- The parent plan optionally mentions adding a lockfile (e.g. `uv.lock`/`pip-tools`) and
  referencing it in the Dockerfile. This sub-step keeps scope minimal: do **not** introduce a new
  lockfile toolchain unless a lockfile already exists in the repo.

## Implementation steps

### Step 1 — Review the install step

Confirm [`Dockerfile`](Dockerfile:6) references [`requirements.txt`](requirements.txt:1) and uses
`--no-cache-dir`. If it already does (current state), leave it unchanged. Do **not** switch to a
lockfile-based install (`uv sync`, `pip-tools`) in this step.

### Step 2 — (Optional, only if a lockfile already exists)

If a committed lockfile (e.g. `uv.lock` or `requirements.lock`) already exists in the repo, add a
`COPY` of it and adjust the `RUN` accordingly. If no lockfile exists, skip — do not create one.

### Step 3 — Build the image

Run:

```bash
docker build -t caldav-mcp:pin-test .
```

Confirm the build resolves the pinned versions without error.

### Step 4 — Verify reproducibility (optional but recommended)

Run the build a second time and confirm the installed set is identical (cache aside, the resolved
versions must match the pinned `==` specifiers). Record the result.

## Definition of done

- [`Dockerfile`](Dockerfile) still installs from [`requirements.txt`](requirements.txt:1) with
  `--no-cache-dir` (unchanged, unless a pre-existing lockfile warranted a minimal change).
- `docker build -t caldav-mcp:pin-test .` succeeds against the pinned dependencies.
- No lockfile toolchain was introduced unless one already existed.

## Constraints / rules

- Do NOT change [`server.py`](server.py) code.
- Do NOT add a new lockfile generation tool or CI workflow in this step.
- Keep changes to [`Dockerfile`](Dockerfile) minimal (ideally none).
- Do not deviate from this plan.

## Commit

If [`Dockerfile`](Dockerfile) changed, commit with a short message, e.g.:

```text
Ensure Docker install uses pinned requirements
```

Otherwise, note in the attempt_completion summary that no file changed and the build verified.
