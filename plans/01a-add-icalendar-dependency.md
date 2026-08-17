# Plan 01a: Add `icalendar` as an explicit dependency

## Context

This is **sub-step 01a** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). Its purpose is to
declare the `icalendar` library as a direct dependency so the escaping fix (sub-steps 01b-01g)
can import it safely. Currently `icalendar` is only present transitively through `caldav`, so
it must be pinned explicitly.

## Current state

[`pyproject.toml`](../pyproject.toml:6) declares only:

```toml
dependencies = [
    "caldav>=1.3.7",
    "fastmcp>=3.4.0",
]
```

[`requirements.txt`](../requirements.txt:1) declares only:

```
caldav>=1.3.7
fastmcp>=3.4.0
```

## Change

Add `icalendar` to both files. Pin to a concrete version that is already installed transitively.

1. Determine the currently installed `icalendar` version by running:

   ```bash
   python -c "import icalendar; print(icalendar.__version__)"
   ```

2. Add `icalendar` as a new dependency line in
   [`pyproject.toml`](../pyproject.toml:6), inside the `dependencies` list, sorted
   alphabetically (so it appears before `caldav`):

   ```toml
   dependencies = [
       "icalendar>=6.0.0",
       "caldav>=1.3.7",
       "fastmcp>=3.4.0",
   ]
   ```

   Replace `>=6.0.0` with the actual installed major.minor version observed in step 1 if it
   differs.

3. Add `icalendar` as a new line in [`requirements.txt`](../requirements.txt:1), sorted
   alphabetically:

   ```
   icalendar>=6.0.0
   caldav>=1.3.7
   fastmcp>=3.4.0
   ```

   Keep the same version specifier used in step 2.

## Definition of done

- Both [`pyproject.toml`](../pyproject.toml) and [`requirements.txt`](../requirements.txt)
  contain an explicit `icalendar` dependency with a consistent, pinned version specifier.
- `python -c "import icalendar"` succeeds without error.

## Constraints

- Do **not** modify [`server.py`](../server.py) in this sub-step.
- Do **not** introduce any new dependencies beyond `icalendar`.
- Do not deviate from this plan; changes here are limited to the two dependency files.
