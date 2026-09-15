"""Multi-remote integration tests.

Requires the following Docker Compose services running via:
    docker compose -f docker-compose.test.yaml up -d

* **radicale** (port 5232) — first Radicale server (testuser, testuser2, userC, userA, userB)
* **radicale2** (port 5233) — second Radicale server (testuser)
* **mcp-pro** (port 8080) — pro-mode MCP server (DB_CONFIG_ENABLED)
* **mcp-simple** (port 8081) — simple-mode header server (no CALDAV_URL)
* **mcp-env** (port 8082) — simple-mode env server (CALDAV_URL set)

Pro-mode store topology (built by build_pro_store.py).  ``mcp-pro`` is shared
with ``test_pro_mode.py`` (M4.5), so the store carries the union of both
suites' requirements:

* config ``main`` → remote ``radicale`` (direct: userA) → calendars ``personal``, ``work``
* config ``main`` → remote ``rad1`` (direct: testuser) → calendar ``personal``
* config ``mirror`` → remote ``radicale-mirror`` (direct: userB) → calendar ``shared``
* config ``second`` → remote ``rad2`` (direct: radicale2, testuser) → calendar ``work``
* config ``broken`` → remote ``dead`` (direct: localhost:59999, unreachable)
* config ``passthrough`` → remote ``relay`` (passthrough: radicale1) → calendar ``work``

Users:

* **alice** → granted ``["main", "mirror", "second", "broken", "passthrough"]``
* **bob** → granted ``["main", "second"]`` only

Test cases:

1. list_calendars as alice → entries from rad1 AND rad2, distinct URLs
2. Event written to rad2 visible via fan-out get_events as alice
3. Isolation: rad1 data never in rad2 entries
4. alice with broken: get_events → Status.OK; dead entry carries a connection
   error; rendered message ok + error lines
5. Same as bob (no broken) → all ok, no error lines
6. User granted only broken → top-level Status.ERROR
7. Same-host distinct identities → independent entries, no cache cross-contamination
   (see the class docstring: a *wrong password* cannot surface as ``auth`` here)
8. Header mode no config: all three headers → success; missing any → typed auth error
9. Simple env mode: wrong-identity headers ignored, env data returned
10. Passthrough pro mode: with headers → succeeds; without → auth failure
11. Stored credentials win: X-Caldav-Url pointing at server 2 ignored, data from server 1
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager

import pytest
import requests
from caldav import DAVClient

from caldav_mcp.config_store import RemoteRecord
from tests.integration.conftest_pro import ALICE_KEY, BOB_KEY

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Host-side URLs, used by the test process itself (e.g. direct DAVClient calls).
_RADICALE_URL = os.environ.get("RADICALE_URL", "http://localhost:5232")
_RADICALE2_URL = os.environ.get("RADICALE2_URL", "http://localhost:5233")

# Container-side URLs.  The MCP servers run in their own container, so a URL
# handed to them through a request header (header mode / passthrough) must
# resolve from *inside* that container, where "localhost" is not the Docker
# host.  These mirror the RADICALE_URL defaults but use the compose DNS name.
_RADICALE_CONTAINER_URL = os.environ.get("RADICALE_CONTAINER_URL", "http://radicale:5232")
_RADICALE2_CONTAINER_URL = os.environ.get("RADICALE2_CONTAINER_URL", "http://radicale2:5232")

_MCP_PORT = int(os.environ.get("MCP_PORT", "8080"))
_MCP_URL = f"http://localhost:{_MCP_PORT}/mcp"

_SIMPLE_PORT = int(os.environ.get("SIMPLE_MCP_PORT", "8081"))
_SIMPLE_URL = f"http://localhost:{_SIMPLE_PORT}/mcp"
_SIMPLE_KEY = "simple-test-key"

_ENV_PORT = int(os.environ.get("ENV_MCP_PORT", "8082"))
_ENV_URL = f"http://localhost:{_ENV_PORT}/mcp"

_ALICE_KEY = ALICE_KEY
_BOB_KEY = BOB_KEY

_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# Calendar topology declared in the store — these collections must exist on
# the Radicale servers for dotted-path addressing to resolve.
_RADICALE1_TOPOLOGY = (
    ("userA", "testpassA", ("personal", "work")),
    ("userB", "testpassB", ("shared",)),
    ("testuser", "testpass", ("personal",)),
    # testuser2 is the passthrough remote's identity (case 10).
    ("testuser2", "testpass2", ("work",)),
    # userC exists solely so the bad-credentials remote (case 7) can use a
    # username that no healthy remote uses.  The DAVClient cache is keyed on
    # (url, username) and direct-mode hits deliberately skip password
    # verification, so a wrong-password remote sharing a username with any
    # working remote would silently reuse that remote's cached session.
    ("userC", "testpassC", ("work",)),
)
_RADICALE2_TOPOLOGY = (("testuser", "testpass", ("work",)),)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provision_radicale_calendars() -> None:
    """Create the calendars declared in the store on both Radicale servers.

    The store declares calendar *names*; Radicale only knows collections that
    have actually been created.  Without this step dotted-path addressing
    resolves to a calendar the server principal does not have.
    """
    # Server 1
    for username, password, names in _RADICALE1_TOPOLOGY:
        client = DAVClient(url=_RADICALE_URL, username=username, password=password)  # type: ignore[operator]
        principal = client.principal()
        existing = {c.get_display_name() for c in principal.calendars()}
        for name in names:
            if name not in existing:
                principal.make_calendar(name=name)

    # Server 2
    for username, password, names in _RADICALE2_TOPOLOGY:
        client = DAVClient(url=_RADICALE2_URL, username=username, password=password)  # type: ignore[operator]
        principal = client.principal()
        existing = {c.get_display_name() for c in principal.calendars()}
        for name in names:
            if name not in existing:
                principal.make_calendar(name=name)


def _mcp_init(session: requests.Session, mcp_url: str) -> str:
    """Send the MCP ``initialize`` + ``notifications/initialized`` handshake.

    Captures the ``mcp-session-id`` returned by the transport and installs it
    on *session* so every later request is part of the same MCP session.
    """
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "integration-test", "version": "0.1.0"},
        },
    }
    resp = session.post(mcp_url, json=init_payload, headers=_REQ_HEADERS)
    resp.raise_for_status()

    session_id = resp.headers.get("mcp-session-id", "")
    if session_id:
        session.headers["mcp-session-id"] = session_id

    notif_payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    session.post(mcp_url, json=notif_payload, headers=_REQ_HEADERS)
    return session_id


def _parse_rpc_response(resp: requests.Response) -> dict[str, object]:
    """Parse a JSON-RPC response that may arrive as an SSE ``data:`` frame."""
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :].strip())
                assert isinstance(payload, dict), f"Expected dict payload, got {payload!r}"
                return payload
        raise AssertionError(f"No 'data:' frame in SSE response: {resp.text!r}")
    result: dict[str, object] = resp.json()
    return result


def _mcp_call(
    session: requests.Session,
    tool: str,
    arguments: dict[str, object],
    mcp_url: str = _MCP_URL,
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, object]:
    """Call an MCP tool and return the parsed JSON-RPC response.

    Performs the ``initialize`` handshake, then the ``tools/call`` request.
    """
    _mcp_init(session, mcp_url)

    headers = {**_REQ_HEADERS, **(extra_headers or {})}
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    resp = session.post(mcp_url, json=payload, headers=headers)
    resp.raise_for_status()
    return _parse_rpc_response(resp)


def _extract_result_text(response: dict[str, object]) -> str:
    """Pull the text from a successful ``tools/call`` JSON-RPC result."""
    result = response.get("result")
    assert isinstance(result, dict), f"Expected result dict, got {type(result)}"
    content = result.get("content")
    assert isinstance(content, list) and len(content) > 0, (
        f"Expected non-empty content list, got {content!r}"
    )
    return str(content[0].get("text", ""))


def _auth_headers(username: str, key: str) -> dict[str, str]:
    """Build the auth header dict for a pro-mode request."""
    return {
        "X-Mcp-Username": username,
        "Authorization": f"Bearer {key}",
    }


def _caldav_headers(url: str, username: str, password: str) -> dict[str, str]:
    """Build the CalDAV credential header dict for header-mode or passthrough."""
    return {
        "X-Caldav-Url": url,
        "X-Caldav-Username": username,
        "X-Caldav-Password": password,
    }


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check whether a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _free_port() -> int:
    """Return a currently-free TCP port on the loopback interface.

    The temporary single-purpose pro servers below bind a port for the
    duration of one test; asking the OS for a free one avoids clashing with
    anything already listening (and would otherwise make the readiness probe
    succeed against the wrong process).
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _config_secret(secret: str) -> Generator[None, None, None]:
    """Set ``CALDAV_MCP_CONFIG_SECRET`` for encryption and child processes.

    ``encrypt_secret`` reads the master key from the environment, and the
    temporary pro servers below are started with an explicit
    ``CALDAV_MCP_CONFIG_SECRET`` too, so both must observe the same value.
    """
    previous = os.environ.get("CALDAV_MCP_CONFIG_SECRET")
    os.environ["CALDAV_MCP_CONFIG_SECRET"] = secret
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CALDAV_MCP_CONFIG_SECRET", None)
        else:
            os.environ["CALDAV_MCP_CONFIG_SECRET"] = previous


@contextmanager
def _simple_mcp_server(
    port: int,
    env_vars: dict[str, str],
    *,
    start_timeout: float = 15.0,
) -> Generator[int, None, None]:
    """Start a local MCP server subprocess and wait for it to be ready.

    Yields the port number once the server is accepting connections.
    """
    base_env = dict(os.environ)
    base_env.update(env_vars)

    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=base_env,
    )

    deadline = time.monotonic() + start_timeout
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                raise RuntimeError(
                    f"Simple MCP server exited with code {proc.returncode}: {stderr}"
                )
            if _is_port_open("127.0.0.1", port):
                break
            time.sleep(0.2)
        else:
            proc.kill()
            raise TimeoutError(f"Simple MCP server on port {port} did not start in time")

        yield port
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _ensure_calendars() -> None:
    """Provision the store-declared calendars on both Radicale servers."""
    _provision_radicale_calendars()


# ---------------------------------------------------------------------------
# 1. list_calendars as alice → entries from rad1 AND rad2
# ---------------------------------------------------------------------------


class TestMultiRemoteListCalendars:
    """Fan-out list_calendars across two distinct Radicale servers."""

    def test_alice_sees_both_servers(self) -> None:
        """``alice`` calls ``caldav_list_calendars`` → entries from rad1 AND rad2.

        Each entry carries a distinct URL (from its respective Radicale server)
        and ``status="ok"``.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"
            entries = payload.get("data", [])
            assert isinstance(entries, list), f"Expected entries list, got {type(entries)}"

            # Collect remote names and the calendar URLs reported by each entry.
            # ``caldav_list_calendars`` merges the handler payload (a list of
            # calendar dicts) into the entry under ``data`` — it does not lift
            # ``url`` to the entry's top level.
            remote_urls: dict[str, str] = {}
            for entry in entries:
                rname = entry.get("remote_name", "")
                if not rname:
                    continue
                cals = entry.get("data", [])
                urls = [c["url"] for c in cals if isinstance(c, dict) and c.get("url")]
                if urls:
                    remote_urls[rname] = urls[0]

            # Both rad1 and rad2 should be present
            assert "rad1" in remote_urls, f"rad1 not in remote names: {list(remote_urls)}"
            assert "rad2" in remote_urls, f"rad2 not in remote names: {list(remote_urls)}"

            # URLs should be distinct (from different servers)
            assert remote_urls["rad1"] != remote_urls["rad2"], (
                f"rad1 and rad2 URLs should differ: {remote_urls}"
            )
            # Each URL should identify its own server.
            assert "/testuser/" in remote_urls["rad1"]
            assert "/testuser/" in remote_urls["rad2"]


# ---------------------------------------------------------------------------
# 2. Event written to rad2 visible via fan-out get_events as alice
# ---------------------------------------------------------------------------


class TestCrossServerEventPropagation:
    """Events written directly to one server are visible through fan-out."""

    def test_event_on_rad2_visible_via_fanout(self) -> None:
        """An event created on rad2 (server 2) appears in fan-out get_events."""
        uid = f"multi-remote-test-{int(time.time() * 1000)}@caldav-mcp-test"

        # Write event directly to radicale2 via caldav client
        client = DAVClient(  # type: ignore[operator]
            url=_RADICALE2_URL,
            username="testuser",
            password="testpass",
        )
        principal = client.principal()
        cal = None
        for c in principal.calendars():
            if c.get_display_name() == "work":
                cal = c
                break
        assert cal is not None, "Calendar 'work' not found on radicale2"

        from datetime import datetime

        from icalendar import Calendar
        from icalendar import Event as ICalEvent

        ev = ICalEvent()
        ev.add("uid", uid)
        ev.add("summary", "Multi-remote cross-server test event")
        ev.add("dtstart", datetime(2026, 6, 15, 10, 0))
        ev.add("dtend", datetime(2026, 6, 15, 11, 0))
        ical = Calendar()
        ical.add_component(ev)
        cal.save_event(ical.to_ical())

        try:
            # Fan-out get_events as alice should see this event
            with requests.Session() as s:
                resp = _mcp_call(
                    s,
                    "caldav_get_events",
                    {"start": "2026-06-15T00:00:00", "end": "2026-06-16T00:00:00"},
                    extra_headers=_auth_headers("alice", _ALICE_KEY),
                )
                text = _extract_result_text(resp)
                payload = json.loads(text)
                entries = payload.get("data", [])

                # Collect all event UIDs across entries
                all_uids: list[str] = []
                for entry in entries:
                    events = entry.get("data", [])
                    if isinstance(events, list):
                        for ev_data in events:
                            if isinstance(ev_data, dict) and ev_data.get("uid"):
                                all_uids.append(ev_data["uid"])

                assert uid in all_uids, f"Event {uid} not found in fan-out results: {all_uids}"
        finally:
            # Cleanup: delete the event.  ``Event.data`` is the raw iCalendar
            # string in the installed caldav version, so parse it with
            # icalendar rather than calling ``walk`` on the value directly.
            from icalendar import Calendar as ICalCalendar

            for ev in cal.events():
                parsed = ICalCalendar.from_ical(ev.data)
                vevent = parsed.walk("VEVENT")
                if vevent and str(vevent[0].get("uid")) == uid:
                    ev.delete()
                    break


# ---------------------------------------------------------------------------
# 3. Isolation: rad1 data never in rad2 entries
# ---------------------------------------------------------------------------


class TestRemoteIsolation:
    """Data on one server never appears in another server's entries."""

    def test_rad1_and_rad2_entries_are_distinct(self) -> None:
        """Entries from rad1 and rad2 carry data from their respective servers.

        Calendar names should be distinct (personal on rad1, work on rad2).
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_get_events",
                {"start": "2026-01-01T00:00:00", "end": "2027-01-01T00:00:00"},
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            entries = payload.get("data", [])

            rad1_cals: set[str] = set()
            rad2_cals: set[str] = set()
            for entry in entries:
                rname = entry.get("remote_name", "")
                cal_name = entry.get("calendar_name", "")
                if rname == "rad1":
                    rad1_cals.add(cal_name)
                elif rname == "rad2":
                    rad2_cals.add(cal_name)

            # Calendar names should not overlap
            overlap = rad1_cals & rad2_cals
            assert not overlap, (
                f"Calendar names overlap between rad1 and rad2: {overlap} "
                f"(rad1={rad1_cals}, rad2={rad2_cals})"
            )


# ---------------------------------------------------------------------------
# 4. Partial failure: alice with broken → mixed ok/error entries
# ---------------------------------------------------------------------------


class TestPartialFailureAlice:
    """One remote failing leaves others intact with per-remote status."""

    def test_alice_with_broken_get_events(self) -> None:
        """``alice`` (broken granted): ``caldav_get_events`` → top-level OK.

        Entries for rad1 and rad2 carry data; entry for dead carries
        ``status="error"`` with connection-error text.  Rendered message
        contains ``[error]`` line naming ``broken.dead`` and ``ok`` lines.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_get_events",
                {"start": "2026-06-15T00:00:00", "end": "2026-06-16T00:00:00"},
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"

            # Top-level status should be OK (at least one scope succeeded)
            status = payload.get("status", "")
            assert status == "ok", f"Expected top-level status 'ok', got {status!r}"

            # Check entries
            entries = payload.get("data", [])
            assert isinstance(entries, list), f"Expected entries list, got {type(entries)}"

            # Find the dead entry.  A failing scope carries the failure in
            # ``error``; the per-entry ``status`` vocabulary lives on the
            # rendered message line (the JSON entry surfaces the error text,
            # not a status field).
            dead_entries = [e for e in entries if e.get("remote_name") == "dead"]
            assert dead_entries, "No entry for remote 'dead' found"
            dead_entry = dead_entries[0]
            dead_error = dead_entry.get("error", "")
            assert dead_error, "Dead entry has no error text"
            assert "refused" in dead_error.lower() or "connection" in dead_error.lower(), (
                f"Expected a connection-error text for dead remote, got {dead_error!r}"
            )

            # The other remotes must still carry their (non-error) entries.
            good_remotes = {
                e.get("remote_name") for e in entries if e.get("remote_name") in ("rad1", "rad2")
            }
            assert good_remotes == {"rad1", "rad2"}, (
                f"Expected rad1 and rad2 entries to survive the dead remote, got {good_remotes}"
            )
            assert all("error" not in e for e in entries if e.get("remote_name") == "rad1"), (
                "rad1 entry must not be degraded by the dead remote"
            )

            # Check rendered message: one error line for the dead remote and
            # at least one ok line for the healthy remotes.
            message = payload.get("message", "")
            assert "- [error] broken.dead" in message, (
                f"Expected an [error] line naming broken.dead, got:\n{message}"
            )
            assert "- [ok] main.rad1" in message, (
                f"Expected an [ok] line for main.rad1, got:\n{message}"
            )
            assert "- [ok] second.rad2" in message, (
                f"Expected an [ok] line for second.rad2, got:\n{message}"
            )
            # 5 healthy scopes (main.radicale, main.rad1, mirror, second.rad2 ...
            # counting per calendar) against 2 failures — the summary must show
            # both, proving the failure did not mask the successes.
            assert "2 error" in message, f"Expected the summary to count 2 errors, got:\n{message}"
            assert "5 ok" in message, f"Expected the summary to count 5 ok, got:\n{message}"


# ---------------------------------------------------------------------------
# 5. Same as bob (no broken) → all ok
# ---------------------------------------------------------------------------


class TestPartialFailureBob:
    """Bob only has main and second — no broken remote, all ok."""

    def test_bob_no_error_lines(self) -> None:
        """``bob`` (no broken): ``caldav_get_events`` → all entries ok.

        No error lines in the rendered message.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_get_events",
                {"start": "2026-06-15T00:00:00", "end": "2026-06-16T00:00:00"},
                extra_headers=_auth_headers("bob", _BOB_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"

            status = payload.get("status", "")
            assert status == "ok", f"Expected top-level status 'ok', got {status!r}"

            # Check entries: only rad1 and rad2, no dead
            entries = payload.get("data", [])
            remote_names = {e.get("remote_name") for e in entries}
            assert "dead" not in remote_names, (
                f"bob should not see 'dead' remote, got: {remote_names}"
            )
            assert "rad1" in remote_names, f"Expected rad1 in {remote_names}"
            assert "rad2" in remote_names, f"Expected rad2 in {remote_names}"

            # Check rendered message: no error lines
            message = payload.get("message", "")
            assert "[error]" not in message, f"Expected no [error] lines for bob, got:\n{message}"
            # Every accessible scope must be reported ok, and the rendered
            # message must carry a per-remote ok line for each.
            assert "4 ok" in message, f"Expected '4 ok' summary, got:\n{message}"
            assert "- [ok] main.rad1" in message, f"Missing main.rad1 ok line:\n{message}"
            assert "- [ok] second.rad2" in message, f"Missing second.rad2 ok line:\n{message}"
            assert "- [ok] main.radicale" in message, f"Missing main.radicale ok line:\n{message}"


# ---------------------------------------------------------------------------
# 6. User granted only broken → top-level ERROR
# ---------------------------------------------------------------------------


class TestAllFailed:
    """User with only a broken remote sees top-level ERROR."""

    def test_only_broken_granted(self) -> None:
        """Create a temporary user granted only the ``broken`` config.

        ``caldav_list_calendars`` → top-level ``Status.ERROR`` naming
        the failing remote.
        """
        import tempfile

        from caldav_mcp.config_crypto import encrypt_secret
        from caldav_mcp.config_store import ConfigStore
        from caldav_mcp.key_hash import hash_api_key

        secret = "test-temp-secret"
        # Build a minimal store with only the broken config
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name
        temp_key = "only-broken-user-key"
        temp_hash = hash_api_key(temp_key)

        try:
            with _config_secret(secret):
                enc = encrypt_secret("dead-pass")
                with ConfigStore(db_path) as store:
                    store.create_config("broken")
                    store.create_remote(
                        config_name="broken",
                        remote=RemoteRecord(
                            config_name="broken",
                            name="dead",
                            url="http://localhost:59999",
                            auth_mode="direct",
                            username="nobody",
                            password_enc=enc,
                        ),
                    )
                    store.create_user(username="onlybroken", key_hash=temp_hash)
                    store.grant_config(username="onlybroken", config_name="broken")

                # Start a temporary pro-mode server with this store
                port = _free_port()
                env_vars = {
                    "DB_CONFIG_ENABLED": "true",
                    "CALDAV_MCP_DB_PATH": db_path,
                    "CALDAV_MCP_CONFIG_SECRET": secret,
                    "CALDAV_MCP_PORT": str(port),
                    "CALDAV_MCP_PATH": "/mcp",
                    "CALDAV_MCP_READ_ONLY": "false",
                    "CALDAV_MCP_CALDAV_VERIFY_SSL": "true",
                    "CALDAV_MCP_LOG_FORMAT": "text",
                    "TZ": "UTC",
                }

                with _simple_mcp_server(port, env_vars) as p:
                    url = f"http://localhost:{p}/mcp"
                    with requests.Session() as s:
                        resp = _mcp_call(
                            s,
                            "caldav_list_calendars",
                            {},
                            mcp_url=url,
                            extra_headers={
                                "X-Mcp-Username": "onlybroken",
                                "Authorization": f"Bearer {temp_key}",
                            },
                        )
                        text = _extract_result_text(resp)
                        payload = json.loads(text)

                        # All scopes failed → top-level ERROR.
                        assert payload.get("status") == "error", (
                            f"Expected top-level 'error', got {payload.get('status')!r}"
                        )
                        assert "ERROR" in text, f"Expected ERROR tag in text, got: {text}"
                        # Should name the failing remote in the rendered message.
                        assert "broken.dead" in text, (
                            f"Expected failing remote name in text, got: {text}"
                        )
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# 7. Distinct-identity failure domains on one host
# ---------------------------------------------------------------------------


class TestBadCredentials:
    """Per-identity isolation for two remotes hosted on the same Radicale.

    **Deviation from the plan (documented, not silently weakened).**  The plan
    asks for a store remote with a *valid URL but wrong password* to surface as
    ``status="auth"``.  That assertion is not reachable against this harness:

    * ``caldav_mcp`` raises :class:`AuthError` only when credentials are
      *absent*; it has no HTTP 401/403 → auth mapping (see
      ``_resolve_pro_client_for_scope``, which raises solely on missing
      ``X-Caldav-*`` headers, and ``_render_error`` in ``errors.py``).
    * This Radicale deployment does not reject credentials for read
      ``PROPFIND``: a wrong password, *no* credentials, and even a nonexistent
      user all return HTTP 207 with a populated ``multistatus``.  A wrong
      password therefore produces a *successful* query, not an auth failure.

    What *is* verifiable — and is the property that matters for fan-out — is
    that two remotes on the same host with distinct identities remain
    independent failure domains: the bad remote must never be served another
    remote's cached ``DAVClient`` (the cache keys on ``(url, username)`` and
    direct-mode hits skip password verification), and the healthy remote's
    entry must stay intact.  Producing a genuine per-entry ``"auth"`` would
    require a store-level ``AuthError`` or an auth-enforcing server.
    """

    def test_bad_remote_does_not_disturb_good_remote(self) -> None:
        """Same URL, distinct identities → independent entries, no cross-contamination.

        The bad-credential remote must resolve to *its own* collection rather
        than silently reusing the healthy remote's cached session, and the
        healthy remote's entry must be unaffected.
        """
        import tempfile

        from caldav_mcp.config_crypto import encrypt_secret
        from caldav_mcp.config_store import ConfigStore, RemoteRecord
        from caldav_mcp.key_hash import hash_api_key

        secret = "test-temp-secret"
        # Build a store: one config with wrong password, one working
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name
        temp_key = "bad-cred-test-key"
        temp_hash = hash_api_key(temp_key)

        try:
            with _config_secret(secret):
                enc_wrong = encrypt_secret("wrong-password")
                enc_good = encrypt_secret("testpass")

                with ConfigStore(db_path) as store:
                    store.create_config("good")
                    store.create_remote(
                        config_name="good",
                        remote=RemoteRecord(
                            config_name="good",
                            name="rad1",
                            url=_RADICALE_URL,
                            auth_mode="direct",
                            username="testuser",
                            password_enc=enc_good,
                        ),
                    )
                    store.create_calendar(
                        config_name="good",
                        remote_name="rad1",
                        name="personal",
                    )

                    store.create_config("badcred")
                    store.create_remote(
                        config_name="badcred",
                        remote=RemoteRecord(
                            config_name="badcred",
                            name="rad1-wrong",
                            url=_RADICALE_URL,
                            auth_mode="direct",
                            # A *dedicated* username is required: the DAVClient
                            # cache is keyed on (url, username) and direct-mode
                            # hits skip password verification, so any username
                            # shared with a working remote would silently serve
                            # that remote's cached session and mask the bad
                            # password.
                            username="userC",
                            password_enc=enc_wrong,
                        ),
                    )
                    store.create_calendar(
                        config_name="badcred",
                        remote_name="rad1-wrong",
                        name="work",
                    )

                    store.create_user(username="badcreduser", key_hash=temp_hash)
                    store.grant_config(username="badcreduser", config_name="good")
                    store.grant_config(username="badcreduser", config_name="badcred")

                port = _free_port()
                env_vars = {
                    "DB_CONFIG_ENABLED": "true",
                    "CALDAV_MCP_DB_PATH": db_path,
                    "CALDAV_MCP_CONFIG_SECRET": secret,
                    "CALDAV_MCP_PORT": str(port),
                    "CALDAV_MCP_PATH": "/mcp",
                    "CALDAV_MCP_READ_ONLY": "false",
                    "CALDAV_MCP_CALDAV_VERIFY_SSL": "true",
                    "CALDAV_MCP_LOG_FORMAT": "text",
                    "TZ": "UTC",
                }

                with _simple_mcp_server(port, env_vars) as p:
                    url = f"http://localhost:{p}/mcp"
                    with requests.Session() as s:
                        resp = _mcp_call(
                            s,
                            "caldav_list_calendars",
                            {},
                            mcp_url=url,
                            extra_headers={
                                "X-Mcp-Username": "badcreduser",
                                "Authorization": f"Bearer {temp_key}",
                            },
                        )
                        text = _extract_result_text(resp)
                        payload = json.loads(text)

                        # Top-level status should be OK (good remote succeeded)
                        status = payload.get("status", "")
                        assert status == "ok", f"Expected top-level status 'ok', got {status!r}"

                        entries = payload.get("data", [])
                        by_remote = {
                            e.get("remote_name"): e
                            for e in entries
                            if e.get("remote_name") in ("rad1", "rad1-wrong")
                        }
                        assert set(by_remote) == {"rad1", "rad1-wrong"}, (
                            f"Expected both remotes in the fan-out, got {set(by_remote)}"
                        )

                        # Each remote resolved against its *own* identity: the
                        # calendars reported must name that identity in the URL.
                        # A wrong-password remote served from the healthy remote's
                        # cache entry would report the other user's collection.
                        good_cals = by_remote["rad1"].get("data", [])
                        bad_cals = by_remote["rad1-wrong"].get("data", [])
                        assert good_cals and bad_cals, (
                            f"Both remotes must report calendars: "
                            f"good={good_cals!r}, bad={bad_cals!r}"
                        )
                        assert all("/testuser/" in c.get("url", "") for c in good_cals), (
                            f"Healthy remote did not use its own identity: {good_cals}"
                        )
                        assert all("/userC/" in c.get("url", "") for c in bad_cals), (
                            f"Bad remote was cross-contaminated with another remote's "
                            f"cached session: {bad_cals}"
                        )

                        # The healthy remote's entry is intact.
                        assert "error" not in by_remote["rad1"], (
                            f"Good remote entry degraded: {by_remote['rad1']!r}"
                        )

                        # Rendered message reports both scopes independently.
                        message = payload.get("message", "")
                        assert "- [ok] good.rad1" in message, (
                            f"Expected an [ok] line for good.rad1, got:\n{message}"
                        )
                        assert "- [ok] badcred.rad1-wrong" in message, (
                            f"Expected a per-remote line for badcred.rad1-wrong, got:\n{message}"
                        )
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# 8. Header mode: all three headers → success; missing any → ERROR
# ---------------------------------------------------------------------------
# NOTE: header-supplied URLs must be container-reachable (compose DNS), never
# "localhost" — the MCP server resolves them from inside its own container.
# ---------------------------------------------------------------------------


class TestHeaderMode:
    """Header mode (no CALDAV_URL): X-Caldav-* headers required per request."""

    def test_all_headers_success(self) -> None:
        """Server without CALDAV_URL: all three ``X-Caldav-*`` headers → success."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                mcp_url=_SIMPLE_URL,
                extra_headers={
                    "Authorization": f"Bearer {_SIMPLE_KEY}",
                    **_caldav_headers(_RADICALE_CONTAINER_URL, "testuser", "testpass"),
                },
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert payload.get("status") == "ok", (
                f"Expected status 'ok' from header-mode request, got: {text}"
            )
            # The calendars returned must come from the header-supplied URL —
            # proving the headers (not a stored/env config) drove the request.
            names = {c.get("name") for c in payload.get("data", [])}
            assert "personal" in names, f"Expected the header-mode 'personal' calendar, got {names}"

    def test_missing_header_error(self) -> None:
        """Missing any ``X-Caldav-*`` header → ERROR:[auth] listing headers."""
        for missing_header in ("X-Caldav-Url", "X-Caldav-Username", "X-Caldav-Password"):
            with requests.Session() as s:
                headers: dict[str, str] = {
                    "Authorization": f"Bearer {_SIMPLE_KEY}",
                }
                # Add only two of the three headers
                if missing_header != "X-Caldav-Url":
                    headers["X-Caldav-Url"] = _RADICALE_CONTAINER_URL
                if missing_header != "X-Caldav-Username":
                    headers["X-Caldav-Username"] = "testuser"
                if missing_header != "X-Caldav-Password":
                    headers["X-Caldav-Password"] = "testpass"

                resp = _mcp_call(
                    s,
                    "caldav_list_calendars",
                    {},
                    mcp_url=_SIMPLE_URL,
                    extra_headers=headers,
                )
                text = _extract_result_text(resp)
                payload = json.loads(text)
                # The rejection must be a typed auth failure that names the
                # required header set, not a downstream connection error.
                assert payload.get("status") == "auth", (
                    f"Missing {missing_header}: expected status 'auth', "
                    f"got {payload.get('status')!r} — {text}"
                )
                assert "X-Caldav-Url" in text and "X-Caldav-Username" in text, (
                    f"Expected the required header names to be listed, got: {text}"
                )


# ---------------------------------------------------------------------------
# 9. Simple env mode: X-Caldav-Url header ignored, data from env server
# ---------------------------------------------------------------------------


class TestEnvModeHeadersIgnored:
    """Env-mode service: request headers are ignored, env creds used."""

    def test_wrong_identity_headers_ignored(self) -> None:
        """Env-mode server (CALDAV_URL set): request carrying wrong-identity
        ``X-Caldav-Url/Username/Password`` → results come from the
        **env-configured** server, not from the headers.

        Assert via data that could only come from the env user (testuser on
        server 1), not from the header-supplied identity.
        """
        with requests.Session() as s:
            # Headers name server 2 with credentials that do not exist there.
            # If they were honoured the call would fail outright; because they
            # are ignored the env-configured server answers.
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                mcp_url=_ENV_URL,
                extra_headers={
                    **_caldav_headers(_RADICALE2_CONTAINER_URL, "wronguser", "wrongpass"),
                },
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)

            assert payload.get("status") == "ok", (
                f"Expected 'ok' (env credentials used), got: {text}"
            )
            # Every returned URL must point at the env-configured server
            # (server 1).  A URL naming server 2 — or an auth failure against
            # it — would prove the headers were honoured.
            urls = [c.get("url", "") for c in payload.get("data", [])]
            assert urls, "Expected at least one calendar from the env server"
            assert all("radicale:5232" in u for u in urls), (
                f"Expected data from the env-configured server only, got {urls}"
            )
            assert not any("radicale2" in u for u in urls), (
                f"Header-supplied server URL leaked into the results: {urls}"
            )


# ---------------------------------------------------------------------------
# 10. Passthrough pro mode: with headers → succeeds; without → auth error
# ---------------------------------------------------------------------------


class TestPassthroughProMode:
    """Passthrough remote in pro mode: credentials from X-Caldav-* headers."""

    def test_alice_passthrough_with_headers(self) -> None:
        """``alice`` with passthrough remote: ``X-Caldav-Username: testuser2``
        / ``X-Caldav-Password: testpass2`` → succeeds as testuser2 against
        server 1 (the passthrough remote's URL).
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_get_events",
                {
                    # testuser2's collection; the passthrough remote carries
                    # these credentials, so the calendar resolves.
                    "calendar_name": "passthrough.relay.work",
                    "start": "2026-01-01T00:00:00",
                    "end": "2027-01-01T00:00:00",
                },
                extra_headers={
                    **_auth_headers("alice", _ALICE_KEY),
                    **_caldav_headers(_RADICALE_CONTAINER_URL, "testuser2", "testpass2"),
                },
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"

            # ``caldav_get_events`` addresses a single calendar, so this is a
            # full fan-out across every accessible scope (not just the
            # passthrough one).  The passthrough scope must succeed and the
            # healthy direct remotes must be unaffected.
            status = payload.get("status", "")
            assert status in ("ok", "empty"), (
                f"Expected passthrough success (ok/empty), got status={status!r}"
            )
            entries = payload.get("data", [])
            assert entries, f"Expected fan-out entries, got {payload!r}"
            relay_entries = [
                e
                for e in entries
                if e.get("config_name") == "passthrough" and e.get("remote_name") == "relay"
            ]
            assert relay_entries, (
                f"Expected a passthrough.relay entry, got "
                f"{[(e.get('config_name'), e.get('remote_name')) for e in entries]}"
            )
            # With credentials supplied the passthrough remote must not report a
            # failure — the header credentials were accepted by server 1.
            assert all("error" not in e for e in relay_entries), (
                f"Passthrough scope errored despite valid headers: {relay_entries}"
            )
            # Rendered message must not report an auth failure for the
            # passthrough remote when credentials were supplied.
            message = payload.get("message", "")
            assert "- [auth] passthrough.relay" not in message, (
                f"Passthrough rejected valid header credentials:\n{message}"
            )

    def test_alice_passthrough_without_headers(self) -> None:
        """``alice`` with passthrough remote but no ``X-Caldav-*`` headers →
        entry ``status="auth"``, other remotes unaffected.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_get_events",
                {
                    "start": "2026-06-15T00:00:00",
                    "end": "2026-06-16T00:00:00",
                },
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"

            # Top-level status: should be OK (rad1 and rad2 succeeded)
            status = payload.get("status", "")
            assert status == "ok", f"Expected top-level OK, got {status!r}"

            # The passthrough relay entry must carry the missing-credential
            # failure, while the healthy remotes stay intact.
            entries = payload.get("data", [])
            relay_entries = [
                e
                for e in entries
                if e.get("config_name") == "passthrough" and e.get("remote_name") == "relay"
            ]
            assert relay_entries, "No passthrough.relay entry in the response"
            relay_error = str(relay_entries[0].get("error", ""))
            assert "X-Caldav-Url" in relay_error, (
                f"Expected a missing-credentials error, got {relay_error!r}"
            )
            # rad1/rad2 must be unaffected.
            healthy = {
                e.get("remote_name") for e in entries if e.get("remote_name") in ("rad1", "rad2")
            }
            assert healthy == {"rad1", "rad2"}, (
                f"Expected rad1 and rad2 to be unaffected, got {healthy}"
            )
            # Rendered message: [auth] line for the passthrough remote plus the
            # ok lines for the others.
            message = payload.get("message", "")
            assert "- [auth] passthrough.relay" in message, (
                f"Expected an [auth] passthrough.relay line, got:\n{message}"
            )
            assert "- [ok] main.rad1" in message, (
                f"Expected an [ok] main.rad1 line, got:\n{message}"
            )


# ---------------------------------------------------------------------------
# 11. Stored credentials win: X-Caldav-Url pointing at server 2 ignored
# ---------------------------------------------------------------------------


class TestStoredCredentialsWin:
    """In pro mode, stored direct-remote credentials override request headers."""

    def test_direct_remote_ignores_x_caldav_url(self) -> None:
        """``alice`` calls with dotted path ``main.rad1.personal`` while
        sending ``X-Caldav-Url`` pointing at server 2 → data comes from
        server 1 (stored direct credentials used; headers ignored).

        The proof is data-based: a uniquely-named event is written to
        server 1's ``testuser/personal`` collection and must come back.  Were
        the request headers honoured, the call would be issued against server 2
        (where neither that calendar nor that event exists).
        """
        from datetime import datetime

        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        uid = f"stored-creds-win-{int(time.time() * 1000)}@caldav-mcp-test"

        client = DAVClient(  # type: ignore[operator]
            url=_RADICALE_URL, username="testuser", password="testpass"
        )
        principal = client.principal()
        cal = next(
            (c for c in principal.calendars() if c.get_display_name() == "personal"),
            None,
        )
        assert cal is not None, "Calendar 'personal' not found on radicale (server 1)"

        ev = ICalEvent()
        ev.add("uid", uid)
        ev.add("summary", "Stored-credentials-win probe")
        ev.add("dtstart", datetime(2026, 6, 20, 10, 0))
        ev.add("dtend", datetime(2026, 6, 20, 11, 0))
        ical = ICalCalendar()
        ical.add_component(ev)
        cal.save_event(ical.to_ical())

        try:
            with requests.Session() as s:
                resp = _mcp_call(
                    s,
                    "caldav_get_events",
                    {
                        "calendar_name": "main.rad1.personal",
                        "start": "2026-06-20T00:00:00",
                        "end": "2026-06-21T00:00:00",
                    },
                    extra_headers={
                        **_auth_headers("alice", _ALICE_KEY),
                        # This header must be ignored — stored creds point at
                        # server 1, where the probe event lives.
                        "X-Caldav-Url": _RADICALE2_CONTAINER_URL,
                    },
                )
                text = _extract_result_text(resp)
                payload = json.loads(text)
                assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"

                # Top-level status should be OK (server 1 answered).
                status = payload.get("status", "")
                assert status in ("ok", "empty"), (
                    f"Expected OK (data from server 1), got status={status!r}. Text: {text}"
                )

                # The entry must come from main.rad1 and expose the probe event.
                rad1_entries = [
                    e
                    for e in payload.get("data", [])
                    if e.get("remote_name") == "rad1" and e.get("config_name") == "main"
                ]
                got_scopes = [
                    (e.get("config_name"), e.get("remote_name")) for e in payload.get("data", [])
                ]
                assert rad1_entries, (
                    f"Expected rad1 entry (server 1 data), got entries: {got_scopes}"
                )
                assert "error" not in rad1_entries[0], (
                    f"Stored-credential remote errored: {rad1_entries[0]!r}"
                )
                returned_uids = [
                    e.get("uid") for e in rad1_entries[0].get("data", []) if isinstance(e, dict)
                ]
                assert uid in returned_uids, (
                    f"Probe event {uid} missing — data did not come from server 1. "
                    f"Got {returned_uids}"
                )

                # Rendered message marks the stored-credential remote ok.
                message = payload.get("message", "")
                assert "- [ok] main.rad1" in message, (
                    f"Expected an [ok] main.rad1 line, got:\n{message}"
                )
        finally:
            for ev_obj in cal.events():
                parsed = ICalCalendar.from_ical(ev_obj.data)
                vevent = parsed.walk("VEVENT")
                if vevent and str(vevent[0].get("uid")) == uid:
                    ev_obj.delete()
                    break
