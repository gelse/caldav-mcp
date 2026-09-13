"""CLI for managing users, configs, remotes, and calendars in the SQLite store.

Runnable as ``python -m caldav_mcp.config_cli`` and via the
``caldav-mcp-config`` console-script alias.  All subcommands are
non-interactive (flags only) for scriptability and testability.

Usage::

    python -m caldav_mcp.config_cli --db store.db config add --name work
    caldav-mcp-config --db store.db user add --username bob --key mykey123
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import sys
from collections.abc import Callable

from caldav_mcp.config_crypto import ConfigSecretError, encrypt_secret
from caldav_mcp.config_store import (
    ConfigStore,
    RemoteRecord,
    StoreConflictError,
    StoreError,
    StoreNotFoundError,
    StoreValidationError,
)

# ---------------------------------------------------------------------------
# API-key hashing (PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------------

_HASH_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32
_HASH_PREFIX = "pbkdf2_sha256"


class _CliError(Exception):
    """Internal error carrying a user-facing message.

    Raised for expected CLI-level failures so ``main()`` can print
    ``error: <message>`` and return ``1`` without leaking a traceback or
    raising ``SystemExit`` out of an in-process call.
    """


def hash_api_key(key: str) -> str:
    """Hash an API key with PBKDF2-HMAC-SHA256.

    Returns a self-describing string::

        pbkdf2_sha256$600000$<salt_b64>$<hash_b64>

    Two calls with the same key produce different salts (different hashes)
    but both verify True via :func:`verify_api_key`.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256", key.encode("utf-8"), salt, _HASH_ITERATIONS, dklen=_HASH_BYTES
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"{_HASH_PREFIX}${_HASH_ITERATIONS}${salt_b64}${dk_b64}"


def verify_api_key(key: str, stored: str) -> bool:
    """Verify *key* against a *stored* hash string.

    Uses constant-time comparison.  Returns ``False`` on malformed stored
    strings — never raises.
    """
    try:
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        prefix, iterations_str, salt_b64, hash_b64 = parts
        if prefix != _HASH_PREFIX:
            return False
        iterations = int(iterations_str)
        if iterations <= 0:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        if not salt or not expected:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", key.encode("utf-8"), salt, iterations, dklen=len(expected)
        )
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------------------
# Argparse helpers
# ---------------------------------------------------------------------------


def _password_from_source(
    password_arg: str | None,
    password_env: str | None,
) -> str | None:
    """Resolve the password from ``--password`` or ``--password-env``.

    ``--password -`` reads from stdin.  Returns ``None`` when no password
    source was specified (passthrough mode).
    """
    if password_arg is not None:
        if password_arg == "-":
            line = sys.stdin.readline()
            if not line:
                raise _CliError("no password read from stdin")
            return line.rstrip("\n")
        return password_arg
    if password_env is not None:
        val = os.environ.get(password_env)
        if val is None or val == "":
            raise _CliError(f"environment variable {password_env!r} is not set or empty")
        return val
    return None


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser (no parsing performed)."""
    parser = argparse.ArgumentParser(
        prog="caldav-mcp-config",
        description=(
            "Manage users, configs, remotes, and calendars in the "
            "caldav-mcp SQLite configuration store."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --db store.db config add --name work\n"
            "  %(prog)s --db store.db remote add --config work --name nc "
            "--url https://cal.example/dav --auth-mode direct "
            "--username alice --password s3cret\n"
            "  %(prog)s --db store.db user add --username bob --key mykey123\n"
        ),
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=os.environ.get("CALDAV_MCP_DB_PATH"),
        help=(
            "Path to the SQLite configuration store. "
            "May also be set via CALDAV_MCP_DB_PATH env var; the flag takes precedence."
        ),
    )

    sub = parser.add_subparsers(dest="noun", help="resource type")

    # ---- user ----
    user_p = sub.add_parser("user", help="manage users")
    user_sub = user_p.add_subparsers(dest="verb")

    user_add = user_sub.add_parser("add", help="add a user with an API key")
    user_add.add_argument("--username", required=True, help="username")
    user_add.add_argument("--key", required=True, dest="api_key", help="API key (non-empty)")

    user_sub.add_parser("list", help="list all usernames")

    user_show = user_sub.add_parser("show", help="show user details and configs")
    user_show.add_argument("--username", required=True, help="username")

    user_delete = user_sub.add_parser("delete", help="delete a user (cascades)")
    user_delete.add_argument("--username", required=True, help="username")

    user_grant = user_sub.add_parser("grant", help="grant user access to a config")
    user_grant.add_argument("--username", required=True, help="username")
    user_grant.add_argument("--config", required=True, help="config name")

    user_revoke = user_sub.add_parser("revoke", help="revoke user access to a config")
    user_revoke.add_argument("--username", required=True, help="username")
    user_revoke.add_argument("--config", required=True, help="config name")

    # ---- config ----
    cfg_p = sub.add_parser("config", help="manage configs")
    cfg_sub = cfg_p.add_subparsers(dest="verb")

    cfg_add = cfg_sub.add_parser("add", help="create a new config")
    cfg_add.add_argument("--name", required=True, help="config name")

    cfg_delete = cfg_sub.add_parser("delete", help="delete a config (cascades)")
    cfg_delete.add_argument("--name", required=True, help="config name")
    cfg_delete.add_argument("--force", action="store_true", help="delete even if non-empty")

    cfg_sub.add_parser("list", help="list all config names")

    cfg_show = cfg_sub.add_parser("show", help="show config details (remotes and calendars)")
    cfg_show.add_argument("--name", required=True, help="config name")

    # ---- remote ----
    rem_p = sub.add_parser("remote", help="manage remotes within a config")
    rem_sub = rem_p.add_subparsers(dest="verb")

    rem_add = rem_sub.add_parser("add", help="add a remote to a config")
    rem_add.add_argument("--config", required=True, help="config name")
    rem_add.add_argument("--name", required=True, help="remote name")
    rem_add.add_argument("--url", required=True, help="CalDAV server URL")
    rem_add.add_argument(
        "--auth-mode",
        required=True,
        choices=["direct", "passthrough"],
        help="authentication mode",
    )
    rem_add.add_argument("--username", default=None, help="CalDAV username (direct mode)")
    rem_add.add_argument(
        "--password",
        default=None,
        help="CalDAV password (direct mode). Use '-' to read from stdin.",
    )
    rem_add.add_argument(
        "--password-env",
        default=None,
        metavar="VAR",
        help="Read CalDAV password from environment variable VAR (direct mode).",
    )

    rem_list = rem_sub.add_parser("list", help="list remotes in a config")
    rem_list.add_argument("--config", required=True, help="config name")

    rem_delete = rem_sub.add_parser("delete", help="delete a remote (cascades calendars)")
    rem_delete.add_argument("--config", required=True, help="config name")
    rem_delete.add_argument("--name", required=True, help="remote name")

    # ---- calendar ----
    cal_p = sub.add_parser("calendar", help="manage calendars within a remote")
    cal_sub = cal_p.add_subparsers(dest="verb")

    cal_add = cal_sub.add_parser("add", help="add a calendar to a remote")
    cal_add.add_argument("--config", required=True, help="config name")
    cal_add.add_argument("--remote", required=True, help="remote name")
    cal_add.add_argument("--name", required=True, help="calendar name")

    cal_list = cal_sub.add_parser("list", help="list calendars (dotted paths)")
    cal_list.add_argument("--config", required=True, help="config name")
    cal_list.add_argument("--remote", default=None, help="filter by remote name")

    cal_delete = cal_sub.add_parser("delete", help="delete a calendar")
    cal_delete.add_argument("--config", required=True, help="config name")
    cal_delete.add_argument("--remote", required=True, help="remote name")
    cal_delete.add_argument("--name", required=True, help="calendar name")

    return parser


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_user_add(args: argparse.Namespace, store: ConfigStore) -> int:
    if not args.api_key:
        print("error: --key must not be empty", file=sys.stderr)
        return 1
    key_hash = hash_api_key(args.api_key)
    try:
        store.create_user(args.username, key_hash)
    except (StoreConflictError, StoreValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_user_list(_args: argparse.Namespace, store: ConfigStore) -> int:
    users = store.list_users()
    for u in users:
        print(u.username)
    return 0


def _handle_user_show(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        user = store.get_user(args.username)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(user.username)
    for cn in user.config_names:
        print(f"config: {cn}")
    return 0


def _handle_user_delete(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.delete_user(args.username)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_user_grant(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.grant_config(args.username, args.config)
    except (StoreNotFoundError, StoreValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_user_revoke(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.revoke_config(args.username, args.config)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_config_add(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.create_config(args.name)
    except (StoreConflictError, StoreValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_config_delete(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        snap = store.load_config(args.name)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (snap.remotes or snap.calendars) and not args.force:
        print(
            "error: config is not empty; use --force to delete",
            file=sys.stderr,
        )
        return 1
    try:
        store.delete_config(args.name)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_config_list(_args: argparse.Namespace, store: ConfigStore) -> int:
    for name in store.list_configs():
        print(name)
    return 0


def _handle_config_show(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        snap = store.load_config(args.name)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for remote in snap.remotes:
        cal_names = [c.name for c in snap.calendars if c.remote_name == remote.name]
        if cal_names:
            for cn in cal_names:
                print(f"{remote.name}.{cn}")
        else:
            print(remote.name)
    return 0


def _handle_remote_add(args: argparse.Namespace, store: ConfigStore) -> int:
    # Argparse-level rejection: --password with passthrough
    if args.auth_mode == "passthrough" and args.password is not None:
        print(
            "error: --password cannot be used with --auth-mode passthrough",
            file=sys.stderr,
        )
        return 1

    if args.auth_mode == "passthrough":
        username = ""
        password_enc = ""
    else:
        # direct mode
        if not args.username:
            print("error: --username is required for --auth-mode direct", file=sys.stderr)
            return 1
        password = _password_from_source(args.password, args.password_env)
        if password is None:
            print(
                "error: one of --password, --password-env is required for --auth-mode direct",
                file=sys.stderr,
            )
            return 1
        try:
            password_enc = encrypt_secret(password)
        except ConfigSecretError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        username = args.username

    remote = RemoteRecord(
        config_name=args.config,
        name=args.name,
        url=args.url,
        auth_mode=args.auth_mode,  # type: ignore[arg-type]
        username=username,
        password_enc=password_enc,
    )
    try:
        store.create_remote(args.config, remote)
    except (StoreConflictError, StoreNotFoundError, StoreValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_remote_list(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        snap = store.load_config(args.config)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for remote in snap.remotes:
        print(f"{remote.name}\t{remote.url}\t{remote.auth_mode}")
    return 0


def _handle_remote_delete(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.delete_remote(args.config, args.name)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_calendar_add(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.create_calendar(args.config, args.remote, args.name)
    except (StoreConflictError, StoreNotFoundError, StoreValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_calendar_list(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        snap = store.load_config(args.config)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for cal in snap.calendars:
        if args.remote is not None and cal.remote_name != args.remote:
            continue
        print(f"{cal.remote_name}.{cal.name}")
    return 0


def _handle_calendar_delete(args: argparse.Namespace, store: ConfigStore) -> int:
    try:
        store.delete_calendar(args.config, args.remote, args.name)
    except StoreNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_Handler = Callable[[argparse.Namespace, ConfigStore], int]

_HANDLERS: dict[tuple[str | None, str | None], _Handler] = {
    ("user", "add"): _handle_user_add,
    ("user", "list"): _handle_user_list,
    ("user", "show"): _handle_user_show,
    ("user", "delete"): _handle_user_delete,
    ("user", "grant"): _handle_user_grant,
    ("user", "revoke"): _handle_user_revoke,
    ("config", "add"): _handle_config_add,
    ("config", "delete"): _handle_config_delete,
    ("config", "list"): _handle_config_list,
    ("config", "show"): _handle_config_show,
    ("remote", "add"): _handle_remote_add,
    ("remote", "list"): _handle_remote_list,
    ("remote", "delete"): _handle_remote_delete,
    ("calendar", "add"): _handle_calendar_add,
    ("calendar", "list"): _handle_calendar_list,
    ("calendar", "delete"): _handle_calendar_delete,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m caldav_mcp.config_cli`` and the console script.

    Returns a process exit code: ``0`` on success, ``1`` on typed errors.
    Never raises for expected errors — all store/crypto exceptions are caught,
    printed to stderr, and translated to exit code 1.  Argparse usage errors
    (exit code 2) are also caught and returned as 1 for uniformity.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse calls sys.exit(2) on parse errors — translate to exit 1.
        return 1 if exc.code != 0 else 0

    if args.noun is None or args.verb is None:
        parser.print_help()
        return 0

    db_path = args.db_path
    if not db_path:
        print(
            "error: --db PATH is required (or set CALDAV_MCP_DB_PATH env var)",
            file=sys.stderr,
        )
        return 1

    handler = _HANDLERS.get((args.noun, args.verb))
    if handler is None:
        parser.print_help()
        return 1

    try:
        with ConfigStore(db_path) as store:
            return handler(args, store)
    except (StoreError, ConfigSecretError, _CliError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Unexpected errors — still print a clean message, no traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Allow ``python -m caldav_mcp.config_cli``
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(main())
