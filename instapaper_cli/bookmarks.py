from __future__ import annotations

"""bookmarks.py — Full-API bookmark mutation verbs.

The thin library seam for the state-changing bookmark operations the CLI
exposes: archive (reversible — moves to the Archive folder) and delete
(**permanent, no undo**). Each wraps one ``/bookmarks/*`` endpoint.

Like every library module here, this one *raises* and never prints or exits —
transport failures surface as :class:`~instapaper_cli.transport.ApiError` /
``NetworkError``, and cli.py maps them to exit codes. Stdlib only.
"""

from instapaper_cli import transport

__all__ = [
    "list_bookmarks",
    "archive",
    "unarchive",
    "star",
    "unstar",
    "move",
    "delete",
]


def list_bookmarks(oc, folder_id="unread", limit=500):
    """List bookmarks in a folder (``POST /bookmarks/list``).

    Returns just the bookmark objects. The endpoint's response shape varies —
    some accounts return the documented ``{user, bookmarks, ...}`` object,
    others the plain array of typed objects — so we normalise both here.
    """
    res = transport.api_call(
        "/bookmarks/list", {"folder_id": str(folder_id), "limit": str(limit)}, oc
    )
    if isinstance(res, dict):
        res = res.get("bookmarks", [])
    return [b for b in res if isinstance(b, dict) and b.get("type") == "bookmark"]


def archive(oc, bookmark_id):
    """Move a bookmark to the Archive folder (``POST /bookmarks/archive``).

    Reversible: :func:`unarchive` puts it back. Returns the parsed response.
    """
    return transport.api_call(
        "/bookmarks/archive", {"bookmark_id": str(bookmark_id)}, oc
    )


def unarchive(oc, bookmark_id):
    """Move a bookmark out of the Archive back to unread (``/bookmarks/unarchive``)."""
    return transport.api_call(
        "/bookmarks/unarchive", {"bookmark_id": str(bookmark_id)}, oc
    )


def star(oc, bookmark_id):
    """Star a bookmark (``POST /bookmarks/star``)."""
    return transport.api_call("/bookmarks/star", {"bookmark_id": str(bookmark_id)}, oc)


def unstar(oc, bookmark_id):
    """Remove a bookmark's star (``POST /bookmarks/unstar``)."""
    return transport.api_call("/bookmarks/unstar", {"bookmark_id": str(bookmark_id)}, oc)


def move(oc, bookmark_id, folder_id):
    """Move a bookmark into a folder (``POST /bookmarks/move``).

    ``folder_id`` is a numeric folder id (resolve a display name to one via
    :func:`instapaper_cli.folders.resolve` first). Reversible — move it again.
    """
    return transport.api_call(
        "/bookmarks/move",
        {"bookmark_id": str(bookmark_id), "folder_id": str(folder_id)},
        oc,
    )


def delete(oc, bookmark_id):
    """Permanently delete a bookmark (``POST /bookmarks/delete``).

    There is no undo — the API destroys the bookmark, its highlights, and its
    read progress. Callers must confirm intent before calling this.
    """
    return transport.api_call(
        "/bookmarks/delete", {"bookmark_id": str(bookmark_id)}, oc
    )
