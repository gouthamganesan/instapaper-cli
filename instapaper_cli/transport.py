from __future__ import annotations

"""transport.py — the HTTP/OAuth seam.

Every network-touching call in instapaper-cli funnels through here. Standard
library only: json, urllib.request, urllib.parse, urllib.error.

Two API surfaces are served:

- The **Simple API** (add/authenticate): unsigned form POST, credentials in the
  body. :func:`simple_call` mirrors the existing ``instapaper`` shim's ``call``
  exactly — it never raises HTTPError (it returns the error code/headers) and
  turns URLError into :class:`NetworkError`.
- The **Full API** (OAuth 1.0a signed): :func:`api_call` signs via
  :mod:`instapaper_cli.oauth`, POSTs a form-encoded body, and either json-parses
  the response or returns raw bytes. Errors are surfaced as :class:`ApiError`.

Library invariant: this module never calls sys.exit / print. It raises.
"""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from instapaper_cli import oauth
from instapaper_cli.creds import OAuthCreds

__all__ = [
    "SIMPLE_ADD",
    "SIMPLE_AUTH",
    "API_BASE",
    "API_BASE_11",
    "NetworkError",
    "ApiError",
    "ERROR_HINTS",
    "simple_call",
    "api_call",
    "xauth_access_token",
]

SIMPLE_ADD = "https://www.instapaper.com/api/add"
SIMPLE_AUTH = "https://www.instapaper.com/api/authenticate"
API_BASE = "https://www.instapaper.com/api/1"
API_BASE_11 = "https://www.instapaper.com/api/1.1"

_USER_AGENT = "instapaper-cli/2.0"
_TIMEOUT = 30

# Retry policy for api_call. Default 0 keeps every existing caller at exactly
# one attempt (no behaviour change); callers that want resilience — export —
# opt in by passing retries=N. Only retryable failures (timeouts, network
# errors, 5xx, rate-limit) are retried; a real 4xx/error-code is raised at once.
_DEFAULT_RETRIES = 0
_BACKOFF_BASE = 0.5  # seconds — the first retry waits this long
_BACKOFF_CAP = 8.0   # seconds — ceiling on the exponential backoff


class NetworkError(Exception):
    """The request never reached the server (DNS, connection, timeout)."""


class ApiError(Exception):
    """The server (or a Full API error object) reported a failure.

    ``http_status`` is the HTTP status (or 200 when the error rides inside an
    otherwise-OK Full API response body). ``error_code`` is the Instapaper
    error code (an int) or None when the body was not JSON. ``message`` is a
    human-readable description.
    """

    def __init__(self, http_status: int, error_code, message: str):
        self.http_status = http_status
        self.error_code = error_code
        self.message = message
        self._nonjson = False
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return (
            (self.http_status is not None and self.http_status >= 500)
            or self.error_code == 1040
            or self._nonjson
        )


# Hints for known Instapaper error codes, surfaced by cli.py alongside the raw
# message. Not exhaustive — Instapaper's error space is larger.
ERROR_HINTS = {
    1040: "rate limited, retry later",
    1041: "Instapaper Premium required",
    1220: "domain requires full page HTML — re-run with --content FILE",
    1221: "domain opted out of Instapaper saving",
    1245: "private source requires --content",
}


def simple_call(endpoint: str, params: dict) -> tuple:
    """POST form-encoded ``params`` to a Simple API endpoint.

    Returns ``(status, headers)`` where headers is a plain dict. Mirrors the
    legacy ``call`` helper: an HTTPError is caught and its code/headers are
    returned (the Simple API signals outcomes via status codes, not bodies);
    a URLError becomes a :class:`NetworkError`.
    """
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("User-Agent", _USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)
    except (socket.timeout, TimeoutError):
        raise NetworkError("timed out after {}s".format(_TIMEOUT))
    except urllib.error.URLError as e:
        raise NetworkError("network error: {}".format(e.reason))


def _find_error_object(parsed):
    """Return the first ``{"type":"error", ...}`` dict in a parsed response.

    The Full API returns errors as an object inside the standard array; the
    bookmarks/list endpoint returns a dict. Handle both shapes.
    """
    if isinstance(parsed, dict):
        if parsed.get("type") == "error":
            return parsed
        return None
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get("type") == "error":
                return item
    return None


def _retry_delay(attempt: int) -> float:
    """Exponential backoff (seconds) for a 1-based retry ``attempt``, capped."""
    return min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (attempt - 1)))


def api_call(
    path: str,
    params: dict,
    creds: OAuthCreds,
    *,
    base: str = API_BASE,
    raw: bool = False,
    timeout: float = None,
    retries: int = _DEFAULT_RETRIES,
):
    """Make an OAuth-signed Full API POST and return the parsed result.

    - ``raw=True`` returns the response body bytes undecoded (get_text HTML,
      the xauth querystring line) — no JSON parsing.
    - Otherwise the body is json-parsed. A Full API error object anywhere in
      the result raises :class:`ApiError`. The parsed list (standard endpoints)
      or dict (bookmarks/list) is returned as-is.
    - HTTPError with a JSON error body → :class:`ApiError` carrying its code.
      A non-JSON error body is treated as a transient server error (retryable).
    - A socket timeout (connect *or* SSL read) and any URLError → a retryable
      :class:`NetworkError`.
    - ``timeout`` overrides the per-request socket timeout (default 30s).
    - ``retries`` bounds automatic retries on *retryable* failures only
      (timeouts, network errors, 5xx, rate-limit) with exponential backoff.
      A real 4xx / Instapaper error code is raised on the first attempt.
      Default 0 → a single attempt, unchanged from callers that don't opt in.
    """
    timeout = _TIMEOUT if timeout is None else timeout
    attempt = 0
    while True:
        try:
            return _api_call_once(
                path, params, creds, base=base, raw=raw, timeout=timeout
            )
        except (NetworkError, ApiError) as e:
            can_retry = isinstance(e, NetworkError) or e.retryable
            if not can_retry or attempt >= retries:
                raise
            attempt += 1
            time.sleep(_retry_delay(attempt))


def _api_call_once(path, params, creds, *, base, raw, timeout):
    """One Full-API attempt. Retry/backoff lives in :func:`api_call`."""
    params = params or {}
    url = base + path

    auth = oauth.authorization_header(
        "POST",
        url,
        params,
        creds.consumer_key,
        creds.consumer_secret,
        creds.oauth_token,
        creds.oauth_token_secret,
    )

    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", _USER_AGENT)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            err = ApiError(e.code, None, "server error; retry later")
            err._nonjson = True
            raise err
        err_obj = _find_error_object(parsed)
        if err_obj is not None:
            raise ApiError(
                e.code, err_obj.get("error_code"), err_obj.get("message", "")
            )
        # HTTP error status but a well-formed non-error JSON body: still a failure.
        raise ApiError(e.code, None, "server error; retry later")
    except (socket.timeout, TimeoutError):
        # A read-phase (SSL) timeout raises a bare socket.timeout that is NOT a
        # URLError, so it would otherwise escape uncaught and abort a batch.
        raise NetworkError("timed out after {}s".format(timeout))
    except urllib.error.URLError as e:
        raise NetworkError("network error: {}".format(e.reason))

    if raw:
        return body

    parsed = json.loads(body)
    err_obj = _find_error_object(parsed)
    if err_obj is not None:
        raise ApiError(
            status, err_obj.get("error_code"), err_obj.get("message", "")
        )
    return parsed


def xauth_access_token(
    consumer_key: str, consumer_secret: str, username: str, password: str
) -> tuple:
    """Exchange username/password for an OAuth access token via xAuth.

    POSTs to ``API_BASE + "/oauth/access_token"`` with the x_auth_* body,
    signed with an EMPTY token/token_secret (there is no token yet). The
    response is a querystring line; ``oauth_token`` and ``oauth_token_secret``
    are parsed out and returned. The password exists only inside this frame —
    it is never stashed on the instance or logged.
    """
    url = API_BASE + "/oauth/access_token"
    body_params = {
        "x_auth_username": username,
        "x_auth_password": password,
        "x_auth_mode": "client_auth",
    }

    auth = oauth.authorization_header(
        "POST",
        url,
        body_params,
        consumer_key,
        consumer_secret,
        token="",
        token_secret="",
    )

    data = urllib.parse.urlencode(body_params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", _USER_AGENT)

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            err = ApiError(e.code, None, "server error; retry later")
            err._nonjson = True
            raise err
        err_obj = _find_error_object(parsed)
        if err_obj is not None:
            raise ApiError(
                e.code, err_obj.get("error_code"), err_obj.get("message", "")
            )
        raise ApiError(e.code, None, "login failed")
    except (socket.timeout, TimeoutError):
        raise NetworkError("timed out after {}s".format(_TIMEOUT))
    except urllib.error.URLError as e:
        raise NetworkError("network error: {}".format(e.reason))

    text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    fields = urllib.parse.parse_qs(text)
    token = fields.get("oauth_token", [""])[0]
    secret = fields.get("oauth_token_secret", [""])[0]
    if not token or not secret:
        raise ApiError(200, None, "login failed: no token in response")
    return token, secret
