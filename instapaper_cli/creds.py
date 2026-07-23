from __future__ import annotations

"""creds.py — the only place secrets are touched.

Resolution order: environment variables first, then the macOS Keychain (for
the password) / on-disk files (for username, OAuth creds). Library code here
never calls sys.exit or print — it raises CredsError, and callers (cli.py)
are responsible for turning that into user-facing output.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass

CONFIG_DIR = os.path.expanduser("~/.config/instapaper")
USERNAME_FILE = os.path.join(CONFIG_DIR, "username")
CREDS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
KEYCHAIN_SERVICE = "instapaper"

_OAUTH_FIELDS = ("consumer_key", "consumer_secret", "oauth_token", "oauth_token_secret")
_OAUTH_ENV_VARS = {
    "consumer_key": "INSTAPAPER_CONSUMER_KEY",
    "consumer_secret": "INSTAPAPER_CONSUMER_SECRET",
    "oauth_token": "INSTAPAPER_OAUTH_TOKEN",
    "oauth_token_secret": "INSTAPAPER_OAUTH_SECRET",
}


class CredsError(Exception):
    """Raised when credentials cannot be resolved. Message includes remediation."""


def get_username() -> str:
    """Resolve the Instapaper username.

    INSTAPAPER_USERNAME env var wins if set (and non-empty); otherwise read
    USERNAME_FILE and strip whitespace. CredsError if neither yields a value.
    """
    env = os.environ.get("INSTAPAPER_USERNAME")
    if env:
        return env

    try:
        with open(USERNAME_FILE) as f:
            username = f.read().strip()
    except FileNotFoundError:
        raise CredsError(
            f"no username found. Write it to {USERNAME_FILE} or set INSTAPAPER_USERNAME."
        )
    if not username:
        raise CredsError(f"{USERNAME_FILE} is empty.")
    return username


def get_password(username: str) -> str:
    """Resolve the password: env var first, then the macOS Keychain.

    An empty password is valid — many Instapaper accounts have none. Use
    `is not None` (never truthiness) so INSTAPAPER_PASSWORD="" is honored.
    INSTAPAPER_PASSWORD exists so this works off macOS (Linux, CI, containers),
    where there is no Keychain to read from.
    """
    env = os.environ.get("INSTAPAPER_PASSWORD")
    if env is not None:
        return env

    if sys.platform != "darwin":
        raise CredsError(
            "no password source. Set INSTAPAPER_PASSWORD — Keychain lookup is "
            "macOS-only. Use an empty string if your account has no password."
        )

    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", username, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CredsError(
            f"no Keychain entry for service '{KEYCHAIN_SERVICE}' account '{username}'.\n"
            f"  Create it with:\n"
            f"    security add-generic-password -s {KEYCHAIN_SERVICE} -a {username} -w"
        )
    return result.stdout.rstrip("\n")


def simple_creds() -> dict:
    """Return {"username": ..., "password": ...} for the Simple API."""
    username = get_username()
    return {"username": username, "password": get_password(username)}


@dataclass(frozen=True)
class OAuthCreds:
    consumer_key: str
    consumer_secret: str
    oauth_token: str
    oauth_token_secret: str


def load_oauth_creds() -> OAuthCreds:
    """Resolve OAuth creds for the Full API.

    Each field is individually overridable via env var (INSTAPAPER_CONSUMER_KEY,
    INSTAPAPER_CONSUMER_SECRET, INSTAPAPER_OAUTH_TOKEN, INSTAPAPER_OAUTH_SECRET).
    Remaining fields come from CREDS_FILE (JSON). Any field still missing
    raises CredsError with "run: instapaper login" guidance.
    """
    file_data = {}
    try:
        with open(CREDS_FILE) as f:
            file_data = json.load(f)
    except FileNotFoundError:
        file_data = {}
    except (json.JSONDecodeError, OSError):
        file_data = {}

    values = {}
    missing = []
    for field in _OAUTH_FIELDS:
        env_val = os.environ.get(_OAUTH_ENV_VARS[field])
        if env_val:
            values[field] = env_val
            continue
        file_val = file_data.get(field) if isinstance(file_data, dict) else None
        if file_val:
            values[field] = file_val
        else:
            missing.append(field)

    if missing:
        raise CredsError(
            "missing OAuth credentials: " + ", ".join(missing) + " — run: instapaper login"
        )

    return OAuthCreds(
        consumer_key=values["consumer_key"],
        consumer_secret=values["consumer_secret"],
        oauth_token=values["oauth_token"],
        oauth_token_secret=values["oauth_token_secret"],
    )


def save_oauth_creds(creds: OAuthCreds) -> None:
    """Persist OAuth creds to CREDS_FILE with 0600 permissions (mode set at creation)."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

    payload = {
        "consumer_key": creds.consumer_key,
        "consumer_secret": creds.consumer_secret,
        "oauth_token": creds.oauth_token,
        "oauth_token_secret": creds.oauth_token_secret,
    }
    data = json.dumps(payload, indent=2).encode("utf-8")

    fd = os.open(CREDS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
