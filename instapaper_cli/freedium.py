from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


def should_wrap(url: str, cfg) -> bool:
    """True iff cfg.freedium_enabled and url's host suffix-matches one of
    cfg.freedium_domains, and the url isn't already pointing at the mirror
    (avoids double-wrapping)."""
    if not cfg.freedium_enabled:
        return False

    host = urlparse(url).hostname
    if not host:
        return False
    host = host.lower()

    mirror_host = urlparse(cfg.freedium_mirror).hostname
    if mirror_host and host == mirror_host.lower():
        return False

    for domain in cfg.freedium_domains:
        d = domain.lower()
        if host == d or host.endswith("." + d):
            return True

    return False


def apply(url: str, cfg, override: Optional[bool] = None) -> str:
    """Wrap url behind cfg.freedium_mirror.

    override True  -> wrap unconditionally.
    override False -> return url unchanged.
    override None  -> wrap iff should_wrap(url, cfg).
    """
    if override is False:
        return url

    if override is True or should_wrap(url, cfg):
        return cfg.freedium_mirror.rstrip("/") + "/" + url

    return url
