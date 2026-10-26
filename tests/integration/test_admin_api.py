"""Admin API integration tests (M2.3 — read-only cases).

Requires the compose test stack from ``docker-compose.test.yaml`` (the
``make test-integration`` target builds the pro store and starts it):

* ``mcp-pro`` (http://localhost:8080) — ``DB_CONFIG_ENABLED=true`` **and**
  ``CALDAV_MCP_ADMIN_KEY=admin-test-key`` → admin routes registered.
* ``mcp-simple`` (http://localhost:8081) — no DB mode → the admin API does
  not exist (framework 404).

Read-only by contract: no users are created, mutated, or deleted here —
the write endpoints are M3 and the store mount stays ``:ro``.
"""

from __future__ import annotations

import os

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_PRO_URL = os.environ.get("MCP_PRO_URL", "http://localhost:8080")
_SIMPLE_URL = os.environ.get("MCP_SIMPLE_URL", "http://localhost:8081")

# Must match CALDAV_MCP_ADMIN_KEY on the mcp-pro service in docker-compose.test.yaml.
_ADMIN_KEY = "admin-test-key"

_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}"}


# ---------------------------------------------------------------------------
# Pro mode: 200 with admin key / 401 without
# ---------------------------------------------------------------------------


class TestAdminReadProMode:
    """``GET /admin/users…`` on the pro-mode service."""

    def test_list_users_200_with_admin_key(self) -> None:
        """Valid admin key → 200 with store users; no key material exposed."""
        resp = requests.get(f"{_PRO_URL}/admin/users", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 200
        users = resp.json()["users"]
        names = {u["username"] for u in users}
        assert {"alice", "bob"} <= names, f"Expected store users in {names}"
        for user in users:
            assert set(user) == {"username", "configs"}
            assert isinstance(user["configs"], list)
        assert "key_hash" not in resp.text and '"key"' not in resp.text

    def test_get_user_200_with_admin_key(self) -> None:
        """Valid admin key + known user → 200 with the {username, configs} object."""
        resp = requests.get(f"{_PRO_URL}/admin/users/alice", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "alice"
        assert isinstance(body["configs"], list)
        assert set(body) == {"username", "configs"}
        assert "key_hash" not in resp.text and '"key"' not in resp.text

    def test_list_users_401_without_key(self) -> None:
        """No admin key → 401 with an error body."""
        resp = requests.get(f"{_PRO_URL}/admin/users", timeout=10)
        assert resp.status_code == 401
        assert "error" in resp.json()

    def test_get_user_401_with_wrong_key(self) -> None:
        """Wrong admin key → 401 with an error body."""
        resp = requests.get(
            f"{_PRO_URL}/admin/users/alice",
            headers={"Authorization": "Bearer definitely-not-the-key"},
            timeout=10,
        )
        assert resp.status_code == 401
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Simple mode: admin API does not exist → 404
# ---------------------------------------------------------------------------


class TestAdminAbsentInSimpleMode:
    """The simple-mode service never registers the admin routes."""

    def test_list_users_404_simple_mode(self) -> None:
        """GET /admin/users on mcp-simple → 404 (even with a key presented)."""
        resp = requests.get(f"{_SIMPLE_URL}/admin/users", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 404

    def test_get_user_404_simple_mode(self) -> None:
        """GET /admin/users/{username} on mcp-simple → 404."""
        resp = requests.get(f"{_SIMPLE_URL}/admin/users/alice", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 404
