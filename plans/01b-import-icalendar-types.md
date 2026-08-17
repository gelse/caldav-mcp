# Plan 01b: Import `icalendar` types in `server.py`

## Context

This is **sub-step 01b** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). It adds the module-level
imports required by the subsequent sub-steps (01c-01g) that build VEVENT payloads via the
`icalendar` API.

## Current state

[`server.py`](../server.py:1) currently imports:

```python
import os
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

from caldav import DAVClient
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
```

Note that [`caldav_update_event`](../server.py:325) already does a **local** import
`from icalendar import Calendar, Event as IEvent` inside the function body. This sub-step
is **not** about that import; leave it as-is for now.

## Change

Add a module-level import for the `icalendar` classes needed to build events. Place it with the
other third-party imports, after the `caldav` import and before the `fastmcp` imports (or grouped
alphabetically with them). Use the following imports:

```python
from icalendar import Calendar, Event
from icalendar import vCalAddress, vText
```

## Definition of done

- [`server.py`](../server.py) has module-level imports for `Calendar`, `Event`, `vCalAddress`,
  and `vText` from `icalendar`.
- The module still imports successfully: `python -c "import server"` does not raise
  `ImportError` (credentials are not resolved at import time, so no CalDAV connection is made).
- No other code is changed in this sub-step.

## Constraints

- Do **not** yet replace any string-building logic in [`caldav_create_event`](../server.py:275).
- Do **not** remove or relocate the local import inside
  [`caldav_update_event`](../server.py:325) in this sub-step.
