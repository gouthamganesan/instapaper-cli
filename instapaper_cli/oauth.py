from __future__ import annotations

"""Pure OAuth 1.0a HMAC-SHA1 request signer.

Zero I/O, zero network. Standard library only. The single public entry point
is :func:`authorization_header`, which produces the value of an HTTP
``Authorization`` header for an OAuth 1.0a signed request.

Design notes / correctness invariants:

- Percent-encoding is RFC 3986 unreserved: ``urllib.parse.quote(str(s),
  safe="~")``. The unreserved set is ``A-Z a-z 0-9 - _ . ~``; ``quote`` already
  leaves ``- _ . `` alone and we additionally spare ``~``. Everything else,
  including ``/``, is encoded. This encoding is applied EVERYWHERE: base string
  components, parameter keys, parameter values, and header values.
- The signature base string is ``METHOD & quote(url) & quote(normalized)``.
- ``normalized`` is every oauth_* parameter (except oauth_signature) plus every
  body parameter, each key and value percent-encoded, sorted by encoded key then
  encoded value, joined ``key=value`` with ``&``.
- The signing key is ``quote(consumer_secret) & quote(token_secret)``.
- The signature is ``base64(HMAC-SHA1(signing_key, base_string))``.
- The header carries ONLY oauth_* parameters (including oauth_signature),
  percent-encoded and rendered ``key="value"``, comma+space joined.
"""

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

__all__ = ["authorization_header", "percent_encode"]


def percent_encode(value) -> str:
    """RFC 3986 percent-encode a value, leaving only unreserved chars intact."""
    return urllib.parse.quote(str(value), safe="~")


def _signature_base_string(method: str, url: str, params: dict) -> str:
    """Build the OAuth 1.0a signature base string.

    ``params`` is the union of the oauth_* parameters (without oauth_signature)
    and the body parameters. Keys and values are percent-encoded, sorted by
    encoded key then encoded value, and joined ``k=v`` with ``&``.
    """
    encoded_pairs = [
        (percent_encode(k), percent_encode(v)) for k, v in params.items()
    ]
    encoded_pairs.sort()  # sort by encoded key, then encoded value
    normalized = "&".join("{}={}".format(k, v) for k, v in encoded_pairs)
    return "&".join(
        [method.upper(), percent_encode(url), percent_encode(normalized)]
    )


def _signing_key(consumer_secret: str, token_secret: str) -> str:
    return percent_encode(consumer_secret) + "&" + percent_encode(token_secret)


def _sign(base_string: str, signing_key: str) -> str:
    digest = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def authorization_header(
    method: str,
    url: str,
    body_params: dict,
    consumer_key: str,
    consumer_secret: str,
    token: str = "",
    token_secret: str = "",
    *,
    timestamp: str | None = None,
    nonce: str | None = None,
    extra_oauth: dict | None = None,
) -> str:
    """Return the full ``Authorization`` header value for an OAuth 1.0a request.

    Only the oauth_* parameters appear in the returned header; ``body_params``
    participate in the signature but are transmitted separately (as the request
    body). ``timestamp`` and ``nonce`` may be injected for deterministic tests.
    """
    if timestamp is None:
        timestamp = str(int(time.time()))
    if nonce is None:
        nonce = secrets.token_hex(16)

    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp,
        "oauth_version": "1.0",
    }
    if token:
        oauth_params["oauth_token"] = token
    if extra_oauth:
        oauth_params.update(extra_oauth)

    # Base string parameters: all oauth_* params + body params (no signature).
    signature_params = dict(oauth_params)
    signature_params.update(body_params or {})

    base_string = _signature_base_string(method, url, signature_params)
    signing_key = _signing_key(consumer_secret, token_secret)
    oauth_params["oauth_signature"] = _sign(base_string, signing_key)

    # Header carries only oauth_* params, percent-encoded, key="value", sorted
    # for stable output.
    parts = [
        '{}="{}"'.format(percent_encode(k), percent_encode(v))
        for k, v in sorted(oauth_params.items())
    ]
    return "OAuth " + ", ".join(parts)
