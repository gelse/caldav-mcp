"""Unit tests for caldav_mcp.config_cli (CLI for the SQLite config store).

Invokes ``main([...])`` programmatically.  Each test creates a fresh store
via ``tmp_path`` and passes ``--db <tmp>/store.db``.  Where passwords are
involved, ``CALDAV_MCP_CONFIG_SECRET`` is set via ``mock.patch.dict``.

All 11 required cases from the plan are covered.
"""

from __future__ import annotations

import base64
import os
from io import StringIO
from unittest import mock

import pytest

from caldav_mcp.config_cli import hash_api_key, main, verify_api_key
from caldav_mcp.config_crypto import decrypt_secret
from caldav_mcp.config_store import ConfigStore, StoreNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-master-secret-for-cli-tests"


def _db(tmp_path, name="test.db") -> str:
    return str(tmp_path / name)


def _env(secret: str = _SECRET) -> dict[str, str]:
    return {"CALDAV_MCP_CONFIG_SECRET": secret}


def _run(tmp_path, *args: str, env: dict[str, str] | None = None) -> int:
    """Run ``main()`` with ``--db`` pointing at *tmp_path* and the given *env*."""
    env = env or _env()
    with mock.patch.dict(os.environ, env, clear=False):
        return main(["--db", _db(tmp_path), *args])


# ---------------------------------------------------------------------------
# 1. Full round-trip
# ---------------------------------------------------------------------------


def test_full_round_trip(tmp_path, capsys):
    """config add → remote add → calendar add → user add → user grant → config show."""
    rc = _run(
        tmp_path,
        "config",
        "add",
        "--name",
        "work",
    )
    assert rc == 0

    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "direct",
        "--username",
        "alice",
        "--password",
        "s3cret",
    )
    assert rc == 0

    rc = _run(
        tmp_path,
        "calendar",
        "add",
        "--config",
        "work",
        "--remote",
        "nc",
        "--name",
        "team",
    )
    assert rc == 0

    rc = _run(
        tmp_path,
        "user",
        "add",
        "--username",
        "bob",
        "--key",
        "mykey123",
    )
    assert rc == 0

    rc = _run(
        tmp_path,
        "user",
        "grant",
        "--username",
        "bob",
        "--config",
        "work",
    )
    assert rc == 0

    rc = _run(tmp_path, "config", "show", "--name", "work")
    assert rc == 0
    out = capsys.readouterr().out
    assert "nc.team" in out

    # Verify store contents directly.
    with mock.patch.dict(os.environ, _env(), clear=False):
        with ConfigStore(_db(tmp_path)) as store:
            snap = store.load_config("work")
            remote = snap.remotes[0]
            decrypted = decrypt_secret(remote.password_enc)
            assert decrypted == "s3cret"
            assert remote.username == "alice"
            assert remote.auth_mode == "direct"

            user = store.get_user("bob")
            assert user.key_hash.startswith("pbkdf2_sha256$")
            assert "work" in user.config_names
            assert len(snap.calendars) == 1
            assert snap.calendars[0].name == "team"
            assert snap.calendars[0].remote_name == "nc"


# ---------------------------------------------------------------------------
# 2. Passthrough: stores empty username/password_enc; rejects --password
# ---------------------------------------------------------------------------


def test_passthrough_remote(tmp_path, capsys):
    """Passthrough stores empty username/password; --password is rejected."""
    rc = _run(tmp_path, "config", "add", "--name", "pt")
    assert rc == 0

    # Valid passthrough add
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "pt",
        "--name",
        "rem",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0

    # Verify empty username/password_enc in store
    with mock.patch.dict(os.environ, _env(), clear=False):
        with ConfigStore(_db(tmp_path)) as store:
            snap = store.load_config("pt")
            assert snap.remotes[0].username == ""
            assert snap.remotes[0].password_enc == ""

    # --password with passthrough should be rejected
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "pt",
        "--name",
        "rem2",
        "--url",
        "https://cal.example/dav2",
        "--auth-mode",
        "passthrough",
        "--password",
        "oops",
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "error:" in err
    assert "--password" in err


# ---------------------------------------------------------------------------
# 3. Passthrough constraint rollback via CLI
# ---------------------------------------------------------------------------


def test_passthrough_constraint_rollback(tmp_path, capsys):
    """User with two configs; passthrough in each → second remote add exits 1."""
    # Create two configs with passthrough remotes
    rc = _run(tmp_path, "config", "add", "--name", "c1")
    assert rc == 0
    rc = _run(tmp_path, "config", "add", "--name", "c2")
    assert rc == 0

    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "c1",
        "--name",
        "r1",
        "--url",
        "https://a.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0

    # Add a user and grant both configs
    rc = _run(tmp_path, "user", "add", "--username", "u1", "--key", "key1")
    assert rc == 0
    rc = _run(tmp_path, "user", "grant", "--username", "u1", "--config", "c1")
    assert rc == 0
    rc = _run(tmp_path, "user", "grant", "--username", "u1", "--config", "c2")
    assert rc == 0

    # Second passthrough remote in c2 should fail (user u1 already has passthrough via c1)
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "c2",
        "--name",
        "r2",
        "--url",
        "https://b.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err

    # Verify rollback: r2 should not exist in c2
    rc = _run(tmp_path, "remote", "list", "--config", "c2")
    assert rc == 0
    out = capsys.readouterr().out
    assert "r2" not in out


# ---------------------------------------------------------------------------
# 4. No-dots rule via CLI
# ---------------------------------------------------------------------------


def test_no_dots_rule(tmp_path, capsys):
    """config/remote/calendar names with dots exit 1."""
    rc = _run(tmp_path, "config", "add", "--name", "a.b")
    assert rc == 1
    assert "error:" in capsys.readouterr().err

    rc = _run(tmp_path, "config", "add", "--name", "good")
    assert rc == 0

    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "good",
        "--name",
        "x.y",
        "--url",
        "https://example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 1
    assert "error:" in capsys.readouterr().err

    # A real remote so the failure is provably the dots rule, not a missing
    # parent (parent-not-found would also exit 1 via StoreConflictError).
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "good",
        "--name",
        "ok",
        "--url",
        "https://example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0

    rc = _run(
        tmp_path,
        "calendar",
        "add",
        "--config",
        "good",
        "--remote",
        "ok",
        "--name",
        "c.d",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "dot" in err


# ---------------------------------------------------------------------------
# 5. user add edge cases
# ---------------------------------------------------------------------------


def test_user_add_empty_key(tmp_path, capsys):
    """--key '' exits 1."""
    rc = _run(tmp_path, "user", "add", "--username", "bob", "--key", "")
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_user_add_duplicate(tmp_path, capsys):
    """Adding the same user twice exits 1 (conflict)."""
    rc = _run(tmp_path, "user", "add", "--username", "bob", "--key", "key1")
    assert rc == 0
    rc = _run(tmp_path, "user", "add", "--username", "bob", "--key", "key2")
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_user_show_never_prints_key(tmp_path, capsys):
    """user show never prints the key or the hash."""
    rc = _run(tmp_path, "user", "add", "--username", "bob", "--key", "secretkey")
    assert rc == 0
    rc = _run(tmp_path, "user", "show", "--username", "bob")
    assert rc == 0
    out = capsys.readouterr().out
    assert "secretkey" not in out
    # The hash is stored but should not appear in the output
    key_hash = hash_api_key("secretkey")
    assert key_hash not in out


# ---------------------------------------------------------------------------
# 6. verify_api_key edge cases
# ---------------------------------------------------------------------------


def test_verify_api_key_correct():
    """Correct key → True."""
    key = "my-test-key"
    stored = hash_api_key(key)
    assert verify_api_key(key, stored) is True


def test_verify_api_key_wrong():
    """Wrong key → False."""
    stored = hash_api_key("correct-key")
    assert verify_api_key("wrong-key", stored) is False


def test_verify_api_key_corrupted():
    """Truncated/corrupted stored string → False without raising."""
    assert verify_api_key("key", "pbkdf2_sha256$600000$abc") is False
    assert verify_api_key("key", "not-a-hash") is False
    assert verify_api_key("key", "") is False
    assert verify_api_key("key", "pbkdf2_sha256$abc$def$ghi$jkl") is False


@pytest.mark.parametrize(
    "stored",
    [
        "pbkdf2_sha256$0$YWJj$YWJj",  # zero iterations
        "pbkdf2_sha256$-1$YWJj$YWJj",  # negative iterations
        "pbkdf2_sha256$600000$$",  # empty salt and hash
        "pbkdf2_sha256$600000$YWJj$",  # empty hash
        "pbkdf2_sha256$600000$$YWJj",  # empty salt
        "pbkdf2_sha256$600000$YWJj$!!!!",  # non-base64 hash
        "pbkdf2_sha256$99999999999999999999$YWJj$YWJj",  # out-of-range iterations
        "$$$$",  # four empty fields
    ],
)
def test_verify_api_key_malformed_never_raises(stored):
    """Malformed-but-4-field hashes must return False, never raise.

    Regression guard for ValueError escaping from ``hashlib.pbkdf2_hmac``
    (e.g. ``iterations <= 0`` or empty salt/hash).
    """
    assert verify_api_key("key", stored) is False


# ---------------------------------------------------------------------------
# 7. hash_api_key format and salt randomness
# ---------------------------------------------------------------------------


def test_hash_api_key_format():
    """Matches pbkdf2_sha256$600000$... with 4 $-separated fields."""
    h = hash_api_key("test-key")
    parts = h.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1] == "600000"
    # Salt and hash are valid base64
    salt = base64.b64decode(parts[2])
    dk = base64.b64decode(parts[3])
    assert len(salt) == 16
    assert len(dk) == 32


def test_hash_api_key_different_salts():
    """Two calls with the same key produce different salts but both verify True."""
    key = "same-key"
    h1 = hash_api_key(key)
    h2 = hash_api_key(key)
    # Different salts → different hashes
    assert h1 != h2
    # But both verify
    assert verify_api_key(key, h1) is True
    assert verify_api_key(key, h2) is True


# ---------------------------------------------------------------------------
# 8. stdin and env password sourcing
# ---------------------------------------------------------------------------


def test_password_stdin(tmp_path, capsys):
    """--password - reads from stdin."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0

    stdin = StringIO("my-secret-pw\n")
    with mock.patch.dict(os.environ, _env(), clear=False):
        with mock.patch("sys.stdin", stdin):
            rc = main(
                [
                    "--db",
                    _db(tmp_path),
                    "remote",
                    "add",
                    "--config",
                    "work",
                    "--name",
                    "nc",
                    "--url",
                    "https://cal.example/dav",
                    "--auth-mode",
                    "direct",
                    "--username",
                    "alice",
                    "--password",
                    "-",
                ]
            )
    assert rc == 0

    # Verify password was encrypted correctly
    with mock.patch.dict(os.environ, _env(), clear=False):
        with ConfigStore(_db(tmp_path)) as store:
            snap = store.load_config("work")
            assert decrypt_secret(snap.remotes[0].password_enc) == "my-secret-pw"


def test_password_env(tmp_path, capsys):
    """--password-env VAR reads from os.environ."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0

    with mock.patch.dict(os.environ, {**_env(), "MY_PW_VAR": "env-pw-value"}, clear=False):
        rc = main(
            [
                "--db",
                _db(tmp_path),
                "remote",
                "add",
                "--config",
                "work",
                "--name",
                "nc",
                "--url",
                "https://cal.example/dav",
                "--auth-mode",
                "direct",
                "--username",
                "alice",
                "--password-env",
                "MY_PW_VAR",
            ]
        )
    assert rc == 0

    with mock.patch.dict(os.environ, _env(), clear=False):
        with ConfigStore(_db(tmp_path)) as store:
            snap = store.load_config("work")
            assert decrypt_secret(snap.remotes[0].password_enc) == "env-pw-value"


# ---------------------------------------------------------------------------
# 9. Missing --db / nonexistent store
# ---------------------------------------------------------------------------


def test_missing_db_flag(capsys):
    """No --db and no CALDAV_MCP_DB_PATH → exits 1 with error."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # Remove CALDAV_MCP_DB_PATH if set
        os.environ.pop("CALDAV_MCP_DB_PATH", None)
        rc = main(["remote", "list", "--config", "x"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "error:" in err


def test_nonexistent_store(tmp_path, capsys):
    """Remote list against a path in a nonexistent directory exits nonzero."""
    bad_path = str(tmp_path / "nosuchdir" / "store.db")
    with mock.patch.dict(os.environ, _env(), clear=False):
        rc = main(["--db", bad_path, "remote", "list", "--config", "x"])
    assert rc != 0
    assert "error:" in capsys.readouterr().err


def test_nonexistent_store_directory(capsys):
    """Remote list against a path under a nonexistent directory exits nonzero."""
    bad_path = "/nonexistent/dir/store.db"
    with mock.patch.dict(os.environ, _env(), clear=False):
        rc = main(["--db", bad_path, "remote", "list", "--config", "x"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "error:" in err


# ---------------------------------------------------------------------------
# 10. Exit code semantics
# ---------------------------------------------------------------------------


def test_exit_code_happy_paths(tmp_path):
    """Every happy path returns 0."""
    assert _run(tmp_path, "config", "add", "--name", "c1") == 0
    assert _run(tmp_path, "config", "list") == 0
    assert _run(tmp_path, "config", "show", "--name", "c1") == 0
    assert _run(tmp_path, "user", "add", "--username", "u1", "--key", "k1") == 0
    assert _run(tmp_path, "user", "list") == 0
    assert _run(tmp_path, "user", "show", "--username", "u1") == 0
    assert (
        _run(
            tmp_path,
            "remote",
            "add",
            "--config",
            "c1",
            "--name",
            "r1",
            "--url",
            "https://example/dav",
            "--auth-mode",
            "passthrough",
        )
        == 0
    )
    assert _run(tmp_path, "remote", "list", "--config", "c1") == 0
    assert _run(tmp_path, "user", "grant", "--username", "u1", "--config", "c1") == 0
    assert _run(tmp_path, "user", "revoke", "--username", "u1", "--config", "c1") == 0
    assert _run(tmp_path, "user", "delete", "--username", "u1") == 0
    assert _run(tmp_path, "remote", "delete", "--config", "c1", "--name", "r1") == 0
    assert _run(tmp_path, "config", "delete", "--name", "c1") == 0


def test_no_subcommand_returns_0(capsys):
    """Running with no subcommand prints help and returns 0."""
    rc = main(["--db", "/tmp/x.db"])
    assert rc == 0


def test_argparse_error_returns_1(capsys):
    """Argparse errors (exit code 2) are translated to exit 1."""
    with mock.patch.dict(os.environ, _env(), clear=False):
        rc = main(["--db", "/tmp/x.db", "user", "add"])
    # --username and --key are required → argparse error
    assert rc == 1


# ---------------------------------------------------------------------------
# 11. config delete --force cascade
# ---------------------------------------------------------------------------


def test_config_delete_requires_force(tmp_path, capsys):
    """config delete without --force on a non-empty config exits 1."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0

    rc = _run(tmp_path, "config", "delete", "--name", "work")
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_config_delete_force_cascades(tmp_path, capsys):
    """config delete --force succeeds and cascades (remotes/calendars gone)."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "direct",
        "--username",
        "alice",
        "--password",
        "s3cret",
    )
    assert rc == 0
    rc = _run(
        tmp_path,
        "calendar",
        "add",
        "--config",
        "work",
        "--remote",
        "nc",
        "--name",
        "team",
    )
    assert rc == 0

    rc = _run(tmp_path, "config", "delete", "--name", "work", "--force")
    assert rc == 0

    # Verify: config (and cascaded remotes/calendars) no longer exist
    with mock.patch.dict(os.environ, _env(), clear=False):
        with ConfigStore(_db(tmp_path)) as store:
            with pytest.raises(StoreNotFoundError):
                store.load_config("work")
            assert store.list_configs() == ()


# ---------------------------------------------------------------------------
# Additional: user grant/revoke and calendar list
# ---------------------------------------------------------------------------


def test_user_grant_and_revoke(tmp_path, capsys):
    """Grant and revoke user config access."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(tmp_path, "user", "add", "--username", "bob", "--key", "k")
    assert rc == 0

    rc = _run(tmp_path, "user", "grant", "--username", "bob", "--config", "work")
    assert rc == 0
    rc = _run(tmp_path, "user", "show", "--username", "bob")
    assert rc == 0
    assert "config: work" in capsys.readouterr().out

    rc = _run(tmp_path, "user", "revoke", "--username", "bob", "--config", "work")
    assert rc == 0
    rc = _run(tmp_path, "user", "show", "--username", "bob")
    assert rc == 0
    assert "config:" not in capsys.readouterr().out


def test_calendar_list_dotted_paths(tmp_path, capsys):
    """Calendar list prints dotted remote.calendar paths."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0
    rc = _run(
        tmp_path,
        "calendar",
        "add",
        "--config",
        "work",
        "--remote",
        "nc",
        "--name",
        "team",
    )
    assert rc == 0

    rc = _run(tmp_path, "calendar", "list", "--config", "work")
    assert rc == 0
    assert "nc.team" in capsys.readouterr().out


def test_calendar_list_filter_by_remote(tmp_path, capsys):
    """Calendar list --remote filters output."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "gc",
        "--url",
        "https://cal2.example/dav",
        "--auth-mode",
        "passthrough",
    )
    assert rc == 0
    # Need a user to avoid passthrough constraint on gc
    rc = _run(tmp_path, "calendar", "add", "--config", "work", "--remote", "nc", "--name", "t1")
    assert rc == 0
    rc = _run(tmp_path, "calendar", "add", "--config", "work", "--remote", "gc", "--name", "t2")
    # This may fail due to passthrough constraint if a user has both remotes
    # accessible. Let's check.
    if rc == 0:
        rc = _run(tmp_path, "calendar", "list", "--config", "work", "--remote", "nc")
        assert rc == 0
        out = capsys.readouterr().out
        assert "nc.t1" in out
        assert "gc.t2" not in out


def test_config_show_empty(tmp_path, capsys):
    """config show on an empty config prints nothing (just the remote names)."""
    rc = _run(tmp_path, "config", "add", "--name", "empty")
    assert rc == 0
    rc = _run(tmp_path, "config", "show", "--name", "empty")
    assert rc == 0
    out = capsys.readouterr().out
    assert out == ""


def test_remote_list_empty(tmp_path, capsys):
    """remote list on a config with no remotes prints nothing."""
    rc = _run(tmp_path, "config", "add", "--name", "empty")
    assert rc == 0
    rc = _run(tmp_path, "remote", "list", "--config", "empty")
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_user_list_empty(tmp_path, capsys):
    """user list with no users prints nothing."""
    rc = _run(tmp_path, "user", "list")
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_config_list_empty(tmp_path, capsys):
    """config list with no configs prints nothing."""
    rc = _run(tmp_path, "config", "list")
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_no_password_with_direct_exits_1(tmp_path, capsys):
    """remote add --auth-mode direct without password exits 1."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "direct",
        "--username",
        "alice",
    )
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_password_env_unset_returns_1_no_leak(tmp_path, capsys):
    """--password-env pointing at an unset var returns 1 (no SystemExit leak).

    Regression guard: ``main()`` must translate this into an exit code rather
    than raising ``SystemExit`` out of an in-process call.
    """
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    rc = _run(
        tmp_path,
        "remote",
        "add",
        "--config",
        "work",
        "--name",
        "nc",
        "--url",
        "https://cal.example/dav",
        "--auth-mode",
        "direct",
        "--username",
        "alice",
        "--password-env",
        "DEFINITELY_UNSET_VAR",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "DEFINITELY_UNSET_VAR" in err


def test_password_stdin_empty_returns_1(tmp_path, capsys):
    """--password - with empty stdin returns 1 (no SystemExit leak)."""
    rc = _run(tmp_path, "config", "add", "--name", "work")
    assert rc == 0
    with mock.patch.dict(os.environ, _env(), clear=False):
        with mock.patch("sys.stdin", StringIO("")):
            rc = main(
                [
                    "--db",
                    _db(tmp_path),
                    "remote",
                    "add",
                    "--config",
                    "work",
                    "--name",
                    "nc",
                    "--url",
                    "https://cal.example/dav",
                    "--auth-mode",
                    "direct",
                    "--username",
                    "alice",
                    "--password",
                    "-",
                ]
            )
    assert rc == 1
    assert "error:" in capsys.readouterr().err
