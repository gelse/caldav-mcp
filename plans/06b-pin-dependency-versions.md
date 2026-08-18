# Plan 06b — Pin exact versions in requirements.txt and pyproject.toml

> Parent plan: [`06-dependency-pinning.md`](06-dependency-pinning.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Replace the loose `>=` constraints in [`requirements.txt`](requirements.txt:1) and
[`pyproject.toml`](pyproject.toml:6) with exact pinned versions (or `~=` compatible-release) using
the versions captured in 06a. Assumes 06a is complete.

## Context you must know

- The resolved versions from 06a (fill in the concrete values; placeholders below are examples):
  - `icalendar==X.Y.Z`
  - `caldav==X.Y.Z`
  - `fastmcp==X.Y.Z`
- Current constraints:
  - [`requirements.txt`](requirements.txt:1):
    ```
    icalendar>=6.0.0
    caldav>=1.3.7
    fastmcp>=3.4.0
    ```
  - [`pyproject.toml`](pyproject.toml:6) `dependencies` list mirrors these with `>=`.
- Keep the alphabetical ordering already present (`icalendar`, then `caldav`, then `fastmcp`).

## Chosen mechanism (do not deviate)

Use **exact pinning** (`==`) for all three direct dependencies, unless the user/team prefers
compatible-release. Default to `==` for maximum reproducibility as stated in the parent plan's
goal.

## Implementation steps

### Step 1 — Pin `requirements.txt`

Rewrite [`requirements.txt`](requirements.txt:1) to:

```
icalendar==<ver>
caldav==<ver>
fastmcp==<ver>
```

using the three resolved versions from 06a, in the same alphabetical order.

### Step 2 — Pin `pyproject.toml`

Rewrite the `dependencies` list in [`pyproject.toml`](pyproject.toml:6) to the same pinned
versions, keeping the TOML list format:

```toml
dependencies = [
    "icalendar==<ver>",
    "caldav==<ver>",
    "fastmcp==<ver>",
]
```

### Step 3 — Verify consistency

Confirm both files use identical version specifiers. Run:

```bash
python -c "import icalendar, caldav, fastmcp; print('deps import OK')"
```

(Imports must still succeed against the currently-installed environment, which matches these
pinned versions.)

## Definition of done

- [`requirements.txt`](requirements.txt:1) pins all three dependencies with `==` (or `~=`, if
  chosen).
- [`pyproject.toml`](pyproject.toml:6) `dependencies` list matches exactly.
- Dependency import sanity check passes.

## Constraints / rules

- Do NOT remove or reorder dependencies; keep `icalendar`/`caldav`/`fastmcp`.
- Do NOT add new dependencies in this step.
- Do NOT modify the [`Dockerfile`](Dockerfile) yet (that is 06c).
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Pin direct dependency versions
```
