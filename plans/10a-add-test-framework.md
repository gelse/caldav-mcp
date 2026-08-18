# Plan 10a — Add test framework and `tests/` scaffolding

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Establish the test infrastructure: add development dependencies and confirm the existing `tests/`
directory is wired into the test runner. This is the foundation for the specific parser/serializer
tests added in later sub-plans.

## Context you must know

The project already has a `tests/` directory containing [`tests/test_create_event.py`](../tests/test_create_event.py)
using the stdlib `unittest` framework. The README's `## Development` section documents running tests
with:

```bash
python -m unittest discover -s tests -v
```

[`pyproject.toml`](../pyproject.toml) already declares a `[tool.pytest.ini_options]` section with
`testpaths = ["tests"]` (line 15-16), but `pytest` is not yet listed as a dependency. There is no
dev-dependency group and no linting/typing configuration yet.

## Chosen mechanism (do not deviate)

- Keep `unittest` as the test style (it is already in use and needs no runtime dependency), while
  adding `pytest` as a dev dependency so both `python -m unittest discover -s tests -v` and `pytest`
  work. `pytest` runs `unittest`-style test cases transparently.
- Add a `[project.optional-dependencies]` group (named `dev`) to [`pyproject.toml`](../pyproject.toml),
  rather than creating a separate `requirements-dev.txt`.

## Implementation steps

### Step 1 — Add dev dependencies in `pyproject.toml`

In [`pyproject.toml`](../pyproject.toml), add an optional-dependencies group after the
`dependencies` list (after line 10):

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
]
```

### Step 2 — Install dev dependencies

Run:

```bash
pip install -e '.[dev]'
```

### Step 3 — Confirm both runners work

Run the existing suite with both commands and confirm they pass:

```bash
python -m unittest discover -s tests -v
pytest -q
```

Both should report the existing `test_create_event.py` tests passing.

## Definition of done

- [ ] `[project.optional-dependencies]` with a `dev` group containing `pytest` exists in `pyproject.toml`.
- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `pytest -q` passes against the existing tests.

## Constraints / rules

- Do not rename or move the existing `tests/test_create_event.py`.
- Do not add coverage for new functions yet; that is done in 10b and 10c.
- Keep the stdlib `unittest` style consistent with the existing test file.

## Commit

```bash
git add pyproject.toml && git commit -m "Add dev dependency group with pytest"
```
