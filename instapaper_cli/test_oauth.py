"""Self-contained unittest suite for the pure OAuth 1.0a signer.

Run either as::

    python3 instapaper_cli/test_oauth.py       # from repo root
    python3 -m pytest instapaper_cli/test_oauth.py

The tests do NOT trust oauth.py's private helpers. They rebuild the signature
base string and signing key from first principles, recompute the HMAC-SHA1
independently, and assert the implementation agrees. The header assertion is
therefore self-verifying rather than circular.
"""

import base64
import hashlib
import hmac
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oauth  # noqa: E402


def q(value):
    """Independent RFC 3986 unreserved percent-encode (mirrors the spec)."""
    return urllib.parse.quote(str(value), safe="~")


def expected_signature(method, url, all_params, consumer_secret, token_secret):
    """Recompute the OAuth signature from first principles, in the test."""
    encoded = sorted((q(k), q(v)) for k, v in all_params.items())
    normalized = "&".join("{}={}".format(k, v) for k, v in encoded)
    base = "&".join([method.upper(), q(url), q(normalized)])
    key = q(consumer_secret) + "&" + q(token_secret)
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii"), base, normalized


class DeterministicVectorTest(unittest.TestCase):
    def setUp(self):
        self.consumer_key = "ck-abc123"
        self.consumer_secret = "cs-secret-XYZ"
        self.token = "tok-777"
        self.token_secret = "toksec-999"
        self.method = "POST"
        self.url = "https://www.instapaper.com/api/1/bookmarks/list"
        self.body_params = {"folder_id": "archive", "limit": "10"}
        self.timestamp = "1721000000"
        self.nonce = "0123456789abcdef0123456789abcdef"

    def _all_params(self, extra=None):
        params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": self.nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": self.timestamp,
            "oauth_version": "1.0",
            "oauth_token": self.token,
        }
        params.update(self.body_params)
        if extra:
            params.update(extra)
        return params

    def test_exact_header_string(self):
        sig, _, _ = expected_signature(
            self.method,
            self.url,
            self._all_params(),
            self.consumer_secret,
            self.token_secret,
        )
        oauth_only = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": self.nonce,
            "oauth_signature": sig,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": self.timestamp,
            "oauth_token": self.token,
            "oauth_version": "1.0",
        }
        expected_header = "OAuth " + ", ".join(
            '{}="{}"'.format(q(k), q(v)) for k, v in sorted(oauth_only.items())
        )

        got = oauth.authorization_header(
            self.method,
            self.url,
            self.body_params,
            self.consumer_key,
            self.consumer_secret,
            self.token,
            self.token_secret,
            timestamp=self.timestamp,
            nonce=self.nonce,
        )
        self.assertEqual(got, expected_header)

    def test_header_shape(self):
        got = oauth.authorization_header(
            self.method,
            self.url,
            self.body_params,
            self.consumer_key,
            self.consumer_secret,
            self.token,
            self.token_secret,
            timestamp=self.timestamp,
            nonce=self.nonce,
        )
        self.assertTrue(got.startswith("OAuth "))
        self.assertIn('oauth_signature="', got)
        self.assertIn('oauth_consumer_key="ck-abc123"', got)
        # Body params must NOT leak into the header.
        self.assertNotIn("folder_id", got)
        self.assertNotIn("limit", got)

    def test_oauth_token_omitted_when_empty(self):
        got = oauth.authorization_header(
            self.method,
            self.url,
            self.body_params,
            self.consumer_key,
            self.consumer_secret,
            timestamp=self.timestamp,
            nonce=self.nonce,
        )
        self.assertNotIn("oauth_token=", got)

        # And the signature must be computed with an empty token_secret,
        # i.e. signing key ends with a bare "&".
        params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": self.nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": self.timestamp,
            "oauth_version": "1.0",
        }
        params.update(self.body_params)
        sig, _, _ = expected_signature(
            self.method, self.url, params, self.consumer_secret, ""
        )
        self.assertIn('oauth_signature="{}"'.format(q(sig)), got)


class EncodingTortureTest(unittest.TestCase):
    """A body value with & = space and a unicode char must be encoded right."""

    def test_special_and_unicode_body_param(self):
        consumer_key = "ck"
        consumer_secret = "cs"
        token = "tk"
        token_secret = "ts"
        method = "POST"
        url = "https://www.instapaper.com/api/add"
        tricky = "a & b=café"  # 'e' + combining acute -> unicode
        body_params = {"selection": tricky, "url": "https://x.io/p?q=1&r=2"}
        ts = "1700000000"
        nonce = "abcdef0123456789abcdef0123456789"

        all_params = {
            "oauth_consumer_key": consumer_key,
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": ts,
            "oauth_version": "1.0",
            "oauth_token": token,
        }
        all_params.update(body_params)
        sig, base, normalized = expected_signature(
            method, url, all_params, consumer_secret, token_secret
        )

        # In the (singly-encoded) normalized param string the special chars in
        # the value must be percent-encoded, never passed raw.
        self.assertNotIn(" ", normalized)
        self.assertIn("selection=a%20%26%20b%3D", normalized)  # space, &, =
        # unicode: derive the expected encoding from the SAME literal used as
        # input, so the test is robust to NFC vs NFD source normalization.
        self.assertIn("selection=" + q(tricky), normalized)
        self.assertNotIn(tricky, normalized)  # not passed through raw
        # The base string then encodes that normalized string a second time,
        # so a space becomes %2520, '&' -> %2526, '=' -> %253D.
        self.assertIn("%2520", base)
        self.assertIn("%2526", base)
        self.assertIn("%253D", base)

        got = oauth.authorization_header(
            method,
            url,
            body_params,
            consumer_key,
            consumer_secret,
            token,
            token_secret,
            timestamp=ts,
            nonce=nonce,
        )
        self.assertIn('oauth_signature="{}"'.format(q(sig)), got)
        # Body param values never appear in the header.
        self.assertNotIn("selection", got)
        self.assertNotIn("cafe", got)


class EncodingUnitTest(unittest.TestCase):
    def test_percent_encode_unreserved(self):
        self.assertEqual(oauth.percent_encode("~-._"), "~-._")
        self.assertEqual(oauth.percent_encode("/"), "%2F")
        self.assertEqual(oauth.percent_encode(" "), "%20")
        self.assertEqual(oauth.percent_encode("&=+"), "%26%3D%2B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
