# Plan: Pin dependencies for reproducible builds

## Problem

[`requirements.txt`](../requirements.txt:1) and [`pyproject.toml`](../pyproject.toml:6) use loose
`>=` constraints (`caldav>=1.3.7`, `fastmcp>=3.4.0`). Both projects have breaking changes between
minor versions, so Docker builds are not reproducible and could break on a `docker build`.

## Goal

Pin to known-good versions (ideally with a lockfile) so builds are reproducible.

## Steps

1. Determine the currently working versions (e.g. `pip freeze`).
2. Pin exact versions (or `~=` compatible-release) in [`requirements.txt`](../requirements.txt:1)
   and [`pyproject.toml`](../pyproject.toml:6).
3. Add `icalendar` at the pinned version (see issue #01/#05).
4. Optionally add a lockfile (e.g. `uv.lock`/`pip-tools`) and reference it in the Dockerfile.
5. Add a `Dockerfile` note or CI step to test the pinned install.

## Affected files

- `requirements.txt`, `pyproject.toml`
- optional: lockfile, `Dockerfile`

## Acceptance criteria

- Dependencies are pinned to specific versions.
- `docker build` produces the same dependency set on repeated builds.
