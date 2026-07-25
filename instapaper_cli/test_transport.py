"""Unit tests for transport.py. Stdlib unittest + unittest.mock, no network.

urllib.request.urlopen is patched in the transport module namespace so the
real socket layer is never touched.
"""

import io
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.parse
from unittest import mock

# Put the repo root (parent of instapaper_cli/) on sys.path so the absolute
# package import scheme works when running this file directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from instapaper_cli import transport
from instapaper_cli.creds import OAuthCreds


CREDS = OAuthCreds(
    consumer_key="ck",
    consumer_secret="cs",
    oauth_token="tok",
    oauth_token_secret="toksec",
)


class _FakeResponse:
    """Context-manager stand-in for the object urlopen yields."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://www.instapaper.com/api/1/x",
        code=code,
        msg="err",
        hdrs=None,
        fp=io.BytesIO(body),
    )


class ApiCallTests(unittest.TestCase):
    def test_signs_and_returns_parsed_dict(self):
        """api_call sends an OAuth Authorization header + correct form body and
        returns a parsed dict for a bookmarks/list-shaped response."""
        payload = {
            "user": {"type": "user", "user_id": 1},
            "bookmarks": [{"type": "bookmark", "bookmark_id": 99, "url": "u"}],
            "highlights": [],
            "delete_ids": [],
        }
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeResponse(json.dumps(payload).encode("utf-8"), status=200)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            result = transport.api_call(
                "/bookmarks/list",
                {"folder_id": "archive", "limit": 500},
                CREDS,
            )

        self.assertEqual(result, payload)
        self.assertIsInstance(result, dict)

        req = captured["req"]
        auth = req.get_header("Authorization")
        self.assertIsNotNone(auth)
        self.assertTrue(auth.startswith("OAuth "))
        self.assertIn('oauth_consumer_key="ck"', auth)
        self.assertIn("oauth_signature=", auth)
        self.assertEqual(
            req.get_header("Content-type"),
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(req.get_header("User-agent"), "instapaper-cli/2.0")
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(req.get_method(), "POST")

        body = req.data.decode("utf-8")
        parsed_body = urllib.parse.parse_qs(body)
        self.assertEqual(parsed_body["folder_id"], ["archive"])
        self.assertEqual(parsed_body["limit"], ["500"])

    def test_error_object_in_body_raises_apierror(self):
        """A {"type":"error", ...} object in a 200 body raises ApiError with the
        right error_code."""
        body = json.dumps(
            [{"type": "error", "error_code": 1220, "message": "needs full HTML"}]
        ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(body, status=200)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.ApiError) as ctx:
                transport.api_call("/bookmarks/add", {"url": "u"}, CREDS)

        self.assertEqual(ctx.exception.error_code, 1220)
        self.assertEqual(ctx.exception.message, "needs full HTML")

    def test_raw_returns_bytes(self):
        """raw=True returns the response body bytes undecoded."""
        raw_body = b"<html><body>hello</body></html>"

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(raw_body, status=200)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            result = transport.api_call(
                "/bookmarks/get_text",
                {"bookmark_id": 99},
                CREDS,
                raw=True,
            )

        self.assertIsInstance(result, bytes)
        self.assertEqual(result, raw_body)

    def test_nonjson_httperror_is_retryable_apierror(self):
        """A non-JSON HTTPError body raises a retryable ApiError."""

        def fake_urlopen(req, timeout=None):
            raise _http_error(503, b"<html>Service Unavailable</html>")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.ApiError) as ctx:
                transport.api_call("/bookmarks/list", {}, CREDS)

        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertIsNone(ctx.exception.error_code)

    def test_urlerror_becomes_networkerror(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.NetworkError):
                transport.api_call("/bookmarks/list", {}, CREDS)

    def test_json_error_httperror_carries_code(self):
        body = json.dumps(
            [{"type": "error", "error_code": 1041, "message": "premium"}]
        ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            raise _http_error(400, body)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.ApiError) as ctx:
                transport.api_call("/bookmarks/add", {"url": "u"}, CREDS)

        self.assertEqual(ctx.exception.error_code, 1041)
        self.assertEqual(ctx.exception.http_status, 400)


class _ReadTimeoutResponse:
    """A urlopen result whose body read() times out (the SSL-read case)."""

    status = 200

    def read(self):
        raise socket.timeout("timed out")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TimeoutAndRetryTests(unittest.TestCase):
    def test_read_timeout_becomes_networkerror(self):
        """A socket.timeout raised during resp.read() (not a URLError) is
        converted to NetworkError instead of escaping uncaught."""

        def fake_urlopen(req, timeout=None):
            return _ReadTimeoutResponse()

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.NetworkError):
                transport.api_call(
                    "/bookmarks/get_text", {"bookmark_id": 1}, CREDS, raw=True
                )

    def test_connect_timeout_via_urlerror_becomes_networkerror(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(socket.timeout("timed out"))

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.NetworkError):
                transport.api_call("/bookmarks/list", {}, CREDS)

    def test_retries_until_success(self):
        """Retryable failures are retried with backoff, then succeed."""
        payload = [{"type": "bookmark", "bookmark_id": 1}]
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("temporary")
            return _FakeResponse(json.dumps(payload).encode("utf-8"), status=200)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(transport.time, "sleep") as sleep:
            result = transport.api_call("/bookmarks/list", {}, CREDS, retries=3)

        self.assertEqual(result, payload)
        self.assertEqual(calls["n"], 3)       # failed twice, third succeeded
        self.assertEqual(sleep.call_count, 2)  # slept once before each retry

    def test_retries_exhausted_reraises(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("down")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(transport.time, "sleep"):
            with self.assertRaises(transport.NetworkError):
                transport.api_call("/bookmarks/list", {}, CREDS, retries=2)

    def test_non_retryable_error_is_not_retried(self):
        """A real Instapaper error code (1220) is raised on the first attempt,
        never retried, no matter how high --retries is."""
        body = json.dumps(
            [{"type": "error", "error_code": 1220, "message": "needs HTML"}]
        ).encode("utf-8")
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise _http_error(400, body)

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(transport.time, "sleep") as sleep:
            with self.assertRaises(transport.ApiError):
                transport.api_call("/bookmarks/add", {"url": "u"}, CREDS, retries=5)

        self.assertEqual(calls["n"], 1)
        sleep.assert_not_called()

    def test_default_is_single_attempt(self):
        """Without opting in, a retryable failure is still a single attempt —
        no behaviour change for existing callers."""
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("down")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(transport.time, "sleep") as sleep:
            with self.assertRaises(transport.NetworkError):
                transport.api_call("/bookmarks/list", {}, CREDS)

        self.assertEqual(calls["n"], 1)
        sleep.assert_not_called()


class RetryableTests(unittest.TestCase):
    def test_retryable_matrix(self):
        self.assertTrue(transport.ApiError(500, None, "m").retryable)
        self.assertTrue(transport.ApiError(503, None, "m").retryable)
        self.assertTrue(transport.ApiError(200, 1040, "m").retryable)
        self.assertFalse(transport.ApiError(200, 1220, "m").retryable)
        self.assertFalse(transport.ApiError(400, 1041, "m").retryable)


class SimpleCallTests(unittest.TestCase):
    def test_httperror_returns_code_and_headers(self):
        err = urllib.error.HTTPError(
            url="https://www.instapaper.com/api/add",
            code=403,
            msg="forbidden",
            hdrs={"X-Test": "1"},
            fp=io.BytesIO(b""),
        )

        def fake_urlopen(req, timeout=None):
            raise err

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            status, headers = transport.simple_call(
                transport.SIMPLE_ADD, {"url": "u"}
            )

        self.assertEqual(status, 403)
        self.assertEqual(headers.get("X-Test"), "1")

    def test_urlerror_becomes_networkerror(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("down")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(transport.NetworkError):
                transport.simple_call(transport.SIMPLE_ADD, {"url": "u"})


class XAuthTests(unittest.TestCase):
    def test_parses_token_and_signs_with_empty_token(self):
        """xauth_access_token parses oauth_token/oauth_token_secret and signs
        the request with an empty token."""
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return _FakeResponse(b"oauth_token=abc&oauth_token_secret=def")

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            token, secret = transport.xauth_access_token(
                "ck", "cs", "user@example.com", "pw"
            )

        self.assertEqual(token, "abc")
        self.assertEqual(secret, "def")

        auth = captured["req"].get_header("Authorization")
        self.assertTrue(auth.startswith("OAuth "))
        # Signed with an empty token → no oauth_token param emitted in header.
        self.assertNotIn("oauth_token=", auth)

        # x_auth body was sent form-encoded.
        body = urllib.parse.parse_qs(captured["req"].data.decode("utf-8"))
        self.assertEqual(body["x_auth_mode"], ["client_auth"])
        self.assertEqual(body["x_auth_username"], ["user@example.com"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
