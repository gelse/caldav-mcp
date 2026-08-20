# Contributing Guide

## Development Setup

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd caldav-mcp
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env` and configure your CalDAV credentials.

## Running Checks

```bash
make check       # lint + typecheck + deps-check + tests
make lint        # ruff check + format check
make typecheck   # mypy
make test        # pytest
```

## Code Style

- **Linting**: Ruff with rules `E`, `F`, `I`, `UP`. Line length 100.
- **Type checking**: mypy with `python_version = "3.11"`.
- **Docstrings**: Google or NumPy style. All public functions must have docstrings.
- **Comments**: Explain *why*, not *what*. Add inline comments for non-obvious logic.

## Testing

- Tests live in `tests/`.
- Use the fakes in `tests/conftest.py` (`FakeClient`, `FakeCalendar`, `FakeEvent`) to avoid network calls.
- Patch shared state via `mock.patch.object(server, "<name>")` — this is how the existing test suite works.
- Run the full suite before submitting: `make check`.

## Architecture Rules

1. **No new circular imports** — import from source modules, not from `server.py`.
2. **All tool handlers return `ToolResult`** — never return raw strings.
3. **Route shared state through `server.*`** in tool handlers so tests can patch it.
4. **Cache clients via `client_cache`** — never create a new `DAVClient` per call.
