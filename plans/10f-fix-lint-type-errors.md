# Plan 10f — Fix lint and type errors surfaced by `ruff`/`mypy`

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Resolve the lint and type-checking errors reported by `ruff` and `mypy` against [`server.py`](../server.py),
without changing runtime behavior. This is the final step (step 6) of the parent plan.

## Context you must know

This sub-plan depends on 10e (config in place) and should run only after a `ruff check` and
`mypy` baseline exists. The fixes must be behavior-preserving: clean up unused imports, sort imports,
normalize `Optional`/`Union` annotations, add missing return types, and address any genuine
undefined-name or type errors — but do **not** refactor working logic.

Note: plans 04 (typed exceptions/logging), 05 (unused-import cleanup, simplified helpers), and 07/08
may already have removed some lint/type issues. Re-run `ruff`/`mypy` against the current
[`server.py`](../server.py) and fix only what remains.

## Chosen mechanism (do not deviate)

- Add type annotations to helpers (`_resolve_credentials`, `_client`, `_get_calendar`, `_parse_dt`,
  `_format_ical_dt`, `_text`, `_text_single`, `_comp`, `_event_to_dict`, `_attendee_str`) and clarify
  tool return types where mypy flags them.
- Fix import ordering/removal per `ruff` (`E`, `F`, `I`, `UP` rules).
- Use `ignore`/`# type: ignore` sparingly and only where the library's untyped surface forces it.

## Implementation steps

### Step 1 — Capture baseline

Run and record current output:

```bash
ruff check server.py
mypy server.py
```

### Step 2 — Fix `ruff` issues

Apply fixes for unused imports, import ordering, and any `UP`-suggested modernizations. Run
`ruff check --fix server.py` where safe, then manually resolve the rest.

### Step 3 — Fix `mypy` issues

Add/repair type annotations on the helpers listed above, and on any tool function whose return type
annotation is mismatched (they are declared `-> str`). Add `# type: ignore` only where a
third-party (caldav/icalendar) call is genuinely untyped and cannot be annotated.

### Step 4 — Verify tests still pass

```bash
ruff check server.py
mypy server.py
python -m unittest discover -s tests -v
pytest -q
```

All of the above must be clean/passing.

## Definition of done

- [ ] `ruff check server.py` passes with no errors.
- [ ] `mypy server.py` passes (or only third-party untyped entries remain, with `# type: ignore`).
- [ ] Full test suite still passes (no runtime behavior change).
- [ ] Any `# type: ignore` comments are limited and justified.

## Constraints / rules

- Behavior-preserving only. Do not alter function logic or outputs.
- If a mypy error reveals a real type bug, report it in attempt_completion rather than changing
  semantics.

## Commit

```bash
git add server.py && git commit -m "Fix ruff and mypy findings in server.py"
```
