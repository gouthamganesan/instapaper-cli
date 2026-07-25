from __future__ import annotations

"""sync.py — the incremental export engine (Wave 2).

Pulls bookmarks + highlights + full text from the Instapaper Full API and
renders one Markdown note per bookmark into an export directory, tracking what
it has already seen in a sync-state file so re-runs only touch what changed.

Design invariants:

- **Read-only against the server.** We send ``have`` as ``id:hash`` pairs ONLY
  and never push reading progress or timestamps back — this tool exports, it
  does not sync your position upstream.
- Incremental: the ``have`` / ``highlights`` params let the server tell us the
  minimal delta (changed bookmarks, new highlights, deletions). We only
  re-render what the server flags as dirty.
- Library-clean: never calls sys.exit / print. Errors on a single bookmark are
  collected into the result rather than aborting the whole run.

State file: ``.instapaper-sync.json`` INSIDE the export directory.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from instapaper_cli import htmlmd, render, transport
from instapaper_cli.transport import ApiError, NetworkError

__all__ = ["load_state", "save_state", "SyncResult", "sync"]

STATE_FILENAME = ".instapaper-sync.json"
STATE_VERSION = 1

# Folders iterated (in order) when folder="all".
_ALL_FOLDERS = ["unread", "starred", "archive"]


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state(export_dir) -> dict:
    """Load the sync state from ``export_dir``; missing file → empty state."""
    path = os.path.join(export_dir, STATE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": STATE_VERSION, "bookmarks": {}}
    except (ValueError, OSError):
        # Corrupt/unreadable state: start fresh rather than crash the export.
        return {"version": STATE_VERSION, "bookmarks": {}}
    if not isinstance(data, dict):
        return {"version": STATE_VERSION, "bookmarks": {}}
    data.setdefault("version", STATE_VERSION)
    if not isinstance(data.get("bookmarks"), dict):
        data["bookmarks"] = {}
    return data


def save_state(export_dir, state) -> None:
    """Atomically write the sync state (temp file in-dir + os.replace)."""
    os.makedirs(export_dir, exist_ok=True)
    path = os.path.join(export_dir, STATE_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class SyncResult:
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    pruned: list = field(default_factory=list)
    unchanged: int = 0
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_to_meta(bid: str, entry: dict) -> dict:
    """Reconstruct an API-shaped bookmark dict from a stored state entry.

    Used when a bookmark is dirty because a new highlight landed on it but its
    own hash is unchanged, so the server omits it from bookmarks[] — the
    metadata we already have is authoritative.
    """
    return {
        "bookmark_id": int(bid),
        "url": entry.get("url", ""),
        "title": entry.get("title", ""),
        "time": entry.get("time", 0),
        "progress": entry.get("progress", 0),
        "progress_timestamp": entry.get("progress_timestamp", 0),
        # render_note expects the raw "0"/"1" string form.
        "starred": "1" if entry.get("starred") else "0",
        "tags": [{"name": t} for t in (entry.get("tags") or [])],
        "hash": entry.get("hash", ""),
    }


def _meta_to_entry(meta: dict, filename: str, highlight_ids: list) -> dict:
    """Build a state entry dict from resolved bookmark metadata."""
    tags = [t.get("name") for t in (meta.get("tags") or []) if isinstance(t, dict)]
    return {
        "hash": meta.get("hash", ""),
        "url": meta.get("url", ""),
        "title": meta.get("title", ""),
        "time": meta.get("time", 0),
        "progress": meta.get("progress", 0),
        "progress_timestamp": meta.get("progress_timestamp", 0),
        "starred": bool(int(meta.get("starred", "0") or "0")),
        "tags": tags,
        "file": filename,
        "highlight_ids": list(highlight_ids),
        "synced": _now_iso(),
    }


def _normalize_list_response(resp):
    """Split a /bookmarks/list response into (bookmarks, highlights, delete_ids).

    The build contract frames the response as a dict
    ``{user, bookmarks[], highlights[], delete_ids[]}``. Real Instapaper returns
    a flat list of typed objects; we tolerate both so this works against the
    live API and the contract's dict shape.
    """
    if isinstance(resp, dict):
        bookmarks = resp.get("bookmarks") or []
        highlights = resp.get("highlights") or []
        delete_ids = resp.get("delete_ids") or []
        if isinstance(delete_ids, str):
            delete_ids = [d for d in delete_ids.split(",") if d.strip()]
        return list(bookmarks), list(highlights), [str(d) for d in delete_ids]

    bookmarks, highlights, delete_ids = [], [], []
    if isinstance(resp, list):
        for item in resp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "bookmark":
                bookmarks.append(item)
            elif itype == "highlight":
                highlights.append(item)
            elif itype == "meta":
                raw = item.get("delete_ids", "")
                if isinstance(raw, str):
                    delete_ids.extend(d for d in raw.split(",") if d.strip())
                elif isinstance(raw, list):
                    delete_ids.extend(str(d) for d in raw)
    return bookmarks, highlights, [str(d) for d in delete_ids]


def _fetch_highlights(bid: str, creds, timeout=None, retries=0) -> list:
    """Fetch authoritative highlights for a bookmark (Full API 1.1)."""
    resp = transport.api_call(
        "/bookmarks/{}/highlights".format(bid),
        {},
        creds,
        base=transport.API_BASE_11,
        timeout=timeout,
        retries=retries,
    )
    out = []
    if isinstance(resp, list):
        for h in resp:
            if isinstance(h, dict) and h.get("type", "highlight") == "highlight":
                out.append(h)
    return out


def _fetch_text(bid: str, creds, log, timeout=None, retries=0) -> str:
    """Fetch and convert the article body; empty string on a no-text ApiError.

    A transport failure (timeout / network error) is *not* swallowed here — it
    propagates so the caller can retry-then-skip the bookmark rather than
    silently recording an empty article for a fetch that never completed.
    """
    try:
        body = transport.api_call(
            "/bookmarks/get_text",
            {"bookmark_id": bid},
            creds,
            raw=True,
            timeout=timeout,
            retries=retries,
        )
    except ApiError as e:
        # 400 (and, defensively, any ApiError) means "no extractable text" —
        # still render the note with its highlights.
        log("  no article text for {} ({}); rendering highlights only".format(bid, e))
        return ""
    if isinstance(body, (bytes, bytearray)):
        html = body.decode("utf-8", errors="replace")
    else:
        html = body or ""
    return htmlmd.html_to_markdown(html)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
def sync(
    export_dir,
    creds,
    *,
    folder="archive",
    limit=500,
    refresh_highlights=False,
    prune=False,
    dry_run=False,
    log=lambda m: None,
    timeout=None,
    retries=0,
) -> SyncResult:
    """Incrementally export bookmarks + highlights + text to ``export_dir``.

    Returns a :class:`SyncResult` aggregating created / updated / pruned ids,
    an unchanged count, and any per-bookmark error messages.

    ``timeout`` / ``retries`` are forwarded to every Full-API call so a slow or
    flaky server (the SSL-read timeouts get_text is prone to) is retried with
    backoff and, if it still fails, skips *that* bookmark instead of aborting
    the whole run — the failure is recorded in :attr:`SyncResult.errors`.
    """
    state = load_state(export_dir)
    bookmarks_state = state["bookmarks"]

    # Snapshot the ids present before this run so we can classify each write as
    # a create vs an update regardless of in-memory mutations along the way.
    preexisting_ids = set(bookmarks_state.keys())

    result = SyncResult()
    created_ids: list = []
    updated_ids: list = []
    pruned_ids: list = []

    if not dry_run:
        os.makedirs(export_dir, exist_ok=True)

    folders = _ALL_FOLDERS if folder == "all" else [folder]

    for folder_id in folders:
        # --- Step 1: incremental request params ---------------------------
        have = ",".join(
            "{}:{}".format(bid, entry.get("hash", ""))
            for bid, entry in bookmarks_state.items()
        )
        known_highlight_ids = []
        for entry in bookmarks_state.values():
            for hid in entry.get("highlight_ids", []):
                known_highlight_ids.append(str(hid))
        highlights_param = "-".join(known_highlight_ids)

        params = {"folder_id": folder_id, "limit": limit}
        if have:
            params["have"] = have
        if highlights_param:
            params["highlights"] = highlights_param

        # --- Step 3: list call --------------------------------------------
        try:
            resp = transport.api_call(
                "/bookmarks/list", params, creds, timeout=timeout, retries=retries
            )
        except (ApiError, NetworkError) as e:
            result.errors.append("list({}): {}".format(folder_id, e))
            continue

        resp_bookmarks, resp_highlights, delete_ids = _normalize_list_response(resp)

        if len(resp_bookmarks) == limit:
            log(
                "warning: folder '{}' returned {} bookmarks (== limit); "
                "results may be truncated — raise --limit".format(folder_id, limit)
            )

        resp_bmap = {}
        for b in resp_bookmarks:
            try:
                resp_bmap[str(b["bookmark_id"])] = b
            except (KeyError, TypeError):
                continue

        # --- Step 4: dirty set --------------------------------------------
        dirty = set(resp_bmap.keys())
        highlight_dirty = set()
        for h in resp_highlights:
            if isinstance(h, dict) and "bookmark_id" in h:
                highlight_dirty.add(str(h["bookmark_id"]))
        dirty |= highlight_dirty
        if refresh_highlights:
            dirty |= set(bookmarks_state.keys())

        delete_set = set(delete_ids)
        # Guard: a bookmark that is both dirty and deleted — delete wins.
        dirty -= delete_set

        # --- Step 5: process each dirty bookmark --------------------------
        for bid in sorted(dirty, key=lambda x: int(x) if x.isdigit() else x):
            in_resp = bid in resp_bmap
            in_state = bid in bookmarks_state
            has_new_highlight = bid in highlight_dirty

            # Resolve metadata: response wins, else fall back to stored entry.
            if in_resp:
                meta = resp_bmap[bid]
            elif in_state:
                meta = _state_to_meta(bid, bookmarks_state[bid])
            else:
                # Referenced by a highlight but we have no metadata for it and
                # the server didn't send it — nothing we can render.
                result.errors.append(
                    "bookmark {}: no metadata available (skipped)".format(bid)
                )
                continue

            new_hash = meta.get("hash", "")
            old_hash = bookmarks_state.get(bid, {}).get("hash") if in_state else None

            # Skip a genuinely-unchanged bookmark (same hash, no new highlight,
            # not a forced refresh). Counted as unchanged at the end.
            if (
                in_state
                and not refresh_highlights
                and not has_new_highlight
                and old_hash == new_hash
            ):
                continue

            try:
                highlights = _fetch_highlights(bid, creds, timeout, retries)
                article_md = _fetch_text(bid, creds, log, timeout, retries)

                note = render.render_note(meta, highlights, article_md)

                existing_file = bookmarks_state.get(bid, {}).get("file")
                filename = existing_file or render.note_filename(meta)
                path = os.path.join(export_dir, filename)

                highlight_ids = [
                    h["highlight_id"] for h in highlights if "highlight_id" in h
                ]

                if dry_run:
                    log(
                        "would write {} ({} highlight(s), {} article chars)".format(
                            filename, len(highlights), len(article_md)
                        )
                    )
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(note)

                bookmarks_state[bid] = _meta_to_entry(meta, filename, highlight_ids)

                if bid in preexisting_ids:
                    if bid not in updated_ids:
                        updated_ids.append(bid)
                else:
                    if bid not in created_ids:
                        created_ids.append(bid)
            except (ApiError, NetworkError) as e:
                result.errors.append("bookmark {}: {}".format(bid, e))
                continue

        # --- Step 6: deletions --------------------------------------------
        for bid in delete_ids:
            entry = bookmarks_state.pop(bid, None)
            if bid not in pruned_ids:
                pruned_ids.append(bid)
            if entry is not None and prune:
                filename = entry.get("file")
                if filename:
                    fpath = os.path.join(export_dir, filename)
                    if dry_run:
                        log("would delete {}".format(filename))
                    else:
                        try:
                            os.remove(fpath)
                        except FileNotFoundError:
                            pass
                        except OSError as e:
                            log("could not delete {}: {}".format(filename, e))

    # --- Step 7: persist ---------------------------------------------------
    result.created = created_ids
    result.updated = updated_ids
    result.pruned = pruned_ids
    result.unchanged = sum(
        1
        for bid in bookmarks_state
        if bid not in created_ids and bid not in updated_ids
    )

    if not dry_run:
        save_state(export_dir, state)
    else:
        log(
            "dry-run: {} created, {} updated, {} pruned, {} unchanged (no writes)".format(
                len(created_ids), len(updated_ids), len(pruned_ids), result.unchanged
            )
        )

    return result
