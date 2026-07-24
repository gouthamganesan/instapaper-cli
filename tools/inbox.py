#!/usr/bin/env python3
"""inbox.py — triage helper for the Instapaper unread queue.

Fills the two gaps `instapaper` itself deliberately does not cover: listing
bookmarks with their saved timestamps, and archiving an existing bookmark.
Both are read-mostly; the only mutation is /bookmarks/archive, which MOVES a
bookmark to the Archive folder. Nothing here can delete.

This lives beside the package rather than inside the CLI because it breaks the
CLI's stated contract on purpose (see README, "What this can and can't do") —
`instapaper` only ever adds and reads. Keeping the one mutating operation in a
separate script that must be invoked by path, with a dry run by default, is the
seam that keeps that promise honest.

Usage:
    python3 tools/inbox.py list [--folder unread] [--before YYYY-MM-DD] [--json]
    python3 tools/inbox.py archive --before YYYY-MM-DD [--apply]
    python3 tools/inbox.py archive --ids 123,456 [--apply]

`archive` is a dry run unless --apply is passed.

Requires `instapaper login` to have been run — it borrows the package's
credential loading and OAuth transport rather than reimplementing the signer.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# The package sits one level up from this script. Resolve it relative to
# __file__ so the helper works from any cwd and from a symlinked checkout.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instapaper_cli import creds, transport  # noqa: E402


def fetch(folder="unread"):
    c = creds.load_oauth_creds()
    res = transport.api_call(
        "/bookmarks/list", {"folder_id": folder, "limit": "500"}, c
    )
    if isinstance(res, dict):
        res = res.get("bookmarks", [])
    rows = [b for b in res if isinstance(b, dict) and b.get("type") == "bookmark"]
    rows.sort(key=lambda b: b.get("time") or 0)
    return c, rows


def cutoff_ts(datestr):
    return (
        datetime.strptime(datestr, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def saved_str(b):
    t = b.get("time")
    return (
        datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        if t
        else "?"
    )


def cmd_list(a):
    _, rows = fetch(a.folder)
    if a.before:
        cut = cutoff_ts(a.before)
        rows = [b for b in rows if (b.get("time") or 0) < cut]
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "id": b["bookmark_id"],
                        "saved": saved_str(b),
                        "title": b.get("title", ""),
                        "url": b.get("url", ""),
                    }
                    for b in rows
                ],
                indent=1,
                ensure_ascii=False,
            )
        )
    else:
        for b in rows:
            print(
                "{}  {}  {}".format(
                    saved_str(b), b["bookmark_id"], (b.get("title") or "")[:70]
                )
            )
    print("\n{} bookmark(s) in {}".format(len(rows), a.folder), file=sys.stderr)


def cmd_archive(a):
    if not a.before and not a.ids:
        sys.exit("archive needs --before YYYY-MM-DD or --ids 1,2,3")
    c, rows = fetch("unread")
    if a.ids:
        wanted = {int(x) for x in a.ids.split(",") if x.strip()}
        targets = [b for b in rows if b["bookmark_id"] in wanted]
    else:
        cut = cutoff_ts(a.before)
        targets = [b for b in rows if (b.get("time") or 0) < cut]

    print("unread total: {}   selected: {}".format(len(rows), len(targets)))
    failed = 0
    for b in targets:
        label = "{}  {}  {}".format(
            saved_str(b), b["bookmark_id"], (b.get("title") or "")[:60]
        )
        if not a.apply:
            print("  [dry-run] would archive:", label)
            continue
        try:
            transport.api_call(
                "/bookmarks/archive", {"bookmark_id": str(b["bookmark_id"])}, c
            )
            print("  archived:", label)
        except Exception as e:
            failed += 1
            print("  FAILED:", label, "->", e)
    if not a.apply:
        print("\nnothing changed. re-run with --apply to archive.")
    sys.exit(1 if failed else 0)


p = argparse.ArgumentParser(description=__doc__)
sub = p.add_subparsers(dest="cmd", required=True)

pl = sub.add_parser("list")
pl.add_argument("--folder", default="unread")
pl.add_argument("--before", help="only show saves strictly older than YYYY-MM-DD")
pl.add_argument("--json", action="store_true")
pl.set_defaults(func=cmd_list)

pa = sub.add_parser("archive")
pa.add_argument("--before", help="archive unread saves strictly older than YYYY-MM-DD")
pa.add_argument("--ids", help="comma-separated bookmark ids to archive")
pa.add_argument("--apply", action="store_true", help="actually archive (default: dry run)")
pa.set_defaults(func=cmd_archive)

args = p.parse_args()
args.func(args)
