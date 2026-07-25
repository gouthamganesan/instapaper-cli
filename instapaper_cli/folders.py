from __future__ import annotations

"""folders.py — Full-API folder operations and folder-name → id resolution.

The Full API addresses folders by numeric ``folder_id``, but humans think in
display names ("Reading list", "Work"). This module bridges the two: it lists
and creates folders, matches a name to a folder object, and resolves a
``--folder`` value (id, system literal, or name) down to the id string that
``bookmarks/add`` wants.

Architecture: like every library module here, this one *raises* and never
prints or exits. :func:`resolve` raises :class:`FolderError` when a name can't
be satisfied; transport failures surface as the usual
:class:`~instapaper_cli.transport.ApiError` / ``NetworkError``. Stdlib only.
"""

from instapaper_cli import transport

__all__ = [
    "SPECIAL_FOLDERS",
    "FolderError",
    "list_folders",
    "add_folder",
    "delete_folder",
    "find_by_name",
    "get_or_create",
    "resolve",
    "resolve_existing",
]

# System locations that are valid folder_id values but are not real folders you
# can create or that appear in folders/list. bookmarks/add accepts these as-is.
SPECIAL_FOLDERS = ("unread", "starred", "archive")


class FolderError(Exception):
    """A folder name could not be resolved to an id (and wasn't auto-created)."""


def _folder_objects(result):
    """Extract folder dicts from a Full-API array response.

    Prefer objects that self-identify as ``type == "folder"``; fall back to any
    dict carrying a ``folder_id`` so we stay tolerant of shape drift.
    """
    typed = [f for f in result if isinstance(f, dict) and f.get("type") == "folder"]
    if typed:
        return typed
    return [f for f in result if isinstance(f, dict) and "folder_id" in f]


def list_folders(oc):
    """Return the account's user-created folders (``POST /folders/list``)."""
    result = transport.api_call("/folders/list", {}, oc)
    return _folder_objects(result)


def add_folder(oc, title):
    """Create a folder and return its object (``POST /folders/add``)."""
    result = transport.api_call("/folders/add", {"title": title}, oc)
    objs = _folder_objects(result)
    if not objs:
        raise transport.ApiError(200, None, "folders/add returned no folder object")
    return objs[0]


def delete_folder(oc, folder_id):
    """Delete a folder by id (``POST /folders/delete``).

    The folder's bookmarks are not destroyed — Instapaper moves them out of the
    folder (back to the unfiled/unread queue). Returns the parsed response.
    """
    return transport.api_call("/folders/delete", {"folder_id": str(folder_id)}, oc)


def find_by_name(folders, name):
    """Return the first folder whose title/display_title matches ``name``.

    Match is case-insensitive and whitespace-trimmed. Returns None on no match.
    """
    wanted = name.strip().lower()
    for f in folders:
        for key in ("title", "display_title"):
            val = f.get(key)
            if isinstance(val, str) and val.strip().lower() == wanted:
                return f
    return None


def get_or_create(oc, title):
    """Return ``(folder_obj, created)`` for ``title``, creating it if absent.

    Idempotent: an existing folder (matched by name) is returned untouched with
    ``created=False``; only a genuine miss triggers ``folders/add``.
    """
    match = find_by_name(list_folders(oc), title)
    if match is not None:
        return match, False
    return add_folder(oc, title), True


def resolve(oc, folder, *, create=False):
    """Resolve a ``--folder`` value to a ``folder_id`` string (or None).

    - ``None``/empty        -> None (no folder specified).
    - all-digits            -> itself, treated as an existing id (no lookup).
    - unread/starred/archive -> itself, a system location.
    - anything else         -> a display name: looked up via ``folders/list``.
      A hit returns its ``folder_id``. A miss raises :class:`FolderError`
      unless ``create`` is set, in which case the folder is created and its new
      id returned.
    """
    if folder is None:
        return None
    folder = folder.strip()
    if not folder:
        return None
    if folder.isdigit():
        return folder
    if folder in SPECIAL_FOLDERS:
        return folder

    existing = list_folders(oc)
    match = find_by_name(existing, folder)
    if match is not None:
        return str(match.get("folder_id"))

    if not create:
        names = ", ".join(sorted(f.get("title", "") for f in existing)) or "(none)"
        raise FolderError(
            "folder {!r} not found. Existing folders: {}. "
            "Re-run with --create-folder to create it, "
            "or create it first with: instapaper folder add {!r}".format(
                folder, names, folder
            )
        )

    created = add_folder(oc, folder)
    return str(created.get("folder_id"))


def resolve_existing(oc, folder):
    """Resolve a name or id to an EXISTING folder object (for delete).

    Unlike :func:`resolve`, this never creates and never accepts the system
    literals ``unread``/``starred``/``archive`` — they aren't folders you can
    delete. Matches a numeric id first, then a display name. Raises
    :class:`FolderError` on a miss (with the list of what does exist).
    """
    folder = (folder or "").strip()
    if not folder:
        raise FolderError("no folder given.")
    if folder in SPECIAL_FOLDERS:
        raise FolderError(
            "{!r} is a system location, not a deletable folder.".format(folder)
        )

    existing = list_folders(oc)
    if folder.isdigit():
        for f in existing:
            if str(f.get("folder_id")) == folder:
                return f
    match = find_by_name(existing, folder)
    if match is not None:
        return match

    names = ", ".join(sorted(f.get("title", "") for f in existing)) or "(none)"
    raise FolderError("folder {!r} not found. Existing folders: {}".format(folder, names))
