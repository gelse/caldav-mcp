"""Type aliases and protocols for the caldav-mcp package.

Defines the ``CalDAVClient`` Protocol so static analysers understand the
interface that tool handlers expect, without importing the concrete
``caldav.DAVClient`` class (which has no type stubs).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CalDAVClient(Protocol):
    """Minimal Protocol describing the DAVClient interface used by tools."""

    def principal(self): ...

    def close(self) -> None: ...
