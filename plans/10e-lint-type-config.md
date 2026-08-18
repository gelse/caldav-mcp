# Plan 10e — Add `ruff` and `mypy`/`pyright` configuration

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Introduce linting (`ruff`) and type-checking (`mypy`/`pyright`) configuration, wire the tool config
into [`pyproject.toml`](../pyproject.toml), and (optionally) add a CI/pre-commit hook.

## Context you must know

- [`pyproject.toml`](../pyproject.toml) currently has no `[tool.ruff]` or `[tool.mypy]` sections.
- The dev dependency group (if 10a has landed) is `[project.optional-dependencies] dev`.
- [`server.py`](../server.py) has no type annotations on most helpers and tools (the tool functions
  take typed parameters but many helpers are untyped). A strict `mypy` run will initially surface
  many errors — the goal here is **configuration**, with error-resolution handled in 10f.

## Chosen mechanism (do not deviate)

- Use `ruff` for linting (it replaces flake8/isort/black) and `mypy` for type checking.
- Add both to the `dev` optional-dependency group.
- Add `[tool.ruff]` (line length, target version, and a minimal `select` set) and a `[tool.mypy]`
  section with a **lenient** baseline (e.g. `ignore_missing_imports = true`) so first runs are
  non-fatal.

## Implementation steps

### Step 1 — Add dev dependencies

In [`pyproject.toml`](../pyproject.toml), extend (or add) the `dev` group:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "ruff>=0.6.0",
    "mypy>=1.10.0",
]
```

### Step 2 — Add `[tool.ruff]` config

Append to [`pyproject.toml`](../pyproject.toml):

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

### Step 3 — Add `[tool.mypy]` config

Append:

```toml
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
```

### Step 4 — Install and run tools

```bash
pip install -e '.[dev]'
ruff check server.py
mypy server.py
```

Note the current output; do **not** fix errors here (that is 10f).

### Step 5 — (Optional) CI / pre-commit

Optionally add a `.github/workflows/ci.yml` (or a `.pre-commit-config.yaml`) running
`pytest -q`, `ruff check .`, and `mypy server.py`. Only add this if the project already uses GitHub
Actions or pre-commit; otherwise skip and note it.

## Definition of done

- [ ] `dev` group includes `ruff` and `mypy`.
- [ ] `[tool.ruff]` and `[tool.mypy]` sections exist in `pyproject.toml`.
- [ ] `ruff check server.py` and `mypy server.py` run and produce output.
- [ ] No code was changed in this sub-plan.

## Constraints / rules

- Do not fix lint/type errors here; capture output for 10f.
- Use lenient (`ignore_missing_imports = true`) mypy baseline.

## Commit

```bash
git add pyproject.toml && git commit -m "Add ruff and mypy configuration"
```
