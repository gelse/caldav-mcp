"""Re-export shared helpers from the root conftest so that ``from conftest
import ...`` works when tests live under ``tests/unit/``.
"""

import sys
from pathlib import Path

# Make the root ``tests/`` directory importable so ``conftest`` resolves.
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

# Re-export everything the unit tests expect.
from conftest import (  # noqa: F401, E402
    FakeCalendar,
    FakeClient,
    FakeEvent,
    FakePrincipal,
    attendees_of,
    make_event,
    patch_caldav,
    patch_caldav_move,
)
