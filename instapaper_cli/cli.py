#!/usr/bin/env python3
"""cli.py — the assembly layer (Wave 3).

argparse command tree + thin handlers. This is the ONLY module that maps
exceptions to exit codes and prints to stdout/stderr. Library modules raise;
we translate. Stdlib only.
"""

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone

from instapaper_cli import creds, config, bookmarks, folders, freedium, transport, sync

# Simple-API HTTP-status → human message map (preserved from the v1 tool).
STATUS_MESSAGES = {
    400: "Bad request (malformed or missing URL)",
    403: "Invalid username or password",
    500: "Instapaper server error, try again later",
}


def die(msg, code=1):
    """Print a fatal message and exit. The ONLY place that calls sys.exit."""
    print("instapaper: {}".format(msg), file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _hint_for(e):
    """Return the ERROR_HINTS hint for an ApiError with a known code, else None."""
    if isinstance(e, transport.ApiError):
        return transport.ERROR_HINTS.get(e.error_code)
    return None


def _print_error_with_hint(prefix, e):
    """Emit an error line plus its hint (if any) to stderr."""
    print("{} — {}".format(prefix, e), file=sys.stderr)
    hint = _hint_for(e)
    if hint:
        print("  hint: {}".format(hint), file=sys.stderr)


def _extract_title(result):
    """Pull a bookmark title out of a Full-API bookmarks/add response."""
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and item.get("type") == "bookmark":
                return item.get("title") or ""
        for item in result:
            if isinstance(item, dict) and "title" in item:
                return item.get("title") or ""
    elif isinstance(result, dict):
        return result.get("title") or ""
    return ""


def _extract_username(resp):
    """Pull the username out of a verify_credentials response."""
    items = resp if isinstance(resp, list) else [resp]
    for item in items:
        if isinstance(item, dict) and item.get("type") == "user":
            return item.get("username") or item.get("user_id") or "?"
    for item in items:
        if isinstance(item, dict) and "username" in item:
            return item.get("username") or "?"
    return "?"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------
def cmd_add(args):
    urls = list(args.urls)
    if args.stdin:
        if args.content == "-":
            die("--stdin and '--content -' both read stdin; use only one.")
        urls += [line.strip() for line in sys.stdin if line.strip()]
    if not urls:
        die("no URLs given. Pass them as arguments or use --stdin.")

    single_only = args.title or args.selection or args.description or args.content
    if len(urls) > 1 and single_only:
        die("--title, --selection, --description, and --content only apply to a single URL.")

    if args.create_folder and not args.folder:
        die("--create-folder needs a folder to create: pass --folder NAME.")

    if args.content is not None and args.stdin and args.content == "-":
        die("--stdin and '--content -' both read stdin; use only one.")

    # Read --content (a path, or '-' for stdin). Single-URL only (enforced above).
    content = None
    if args.content is not None:
        if args.content == "-":
            content = sys.stdin.read()
        else:
            try:
                with open(args.content, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                die("could not read --content file {!r}: {}".format(args.content, e))

    # Any full-only flag routes the whole command through the Full API.
    full_mode = bool(
        args.content or args.folder or args.tag or args.archive or args.description
    )

    cfg = config.load_config()
    override = args.freedium  # True / False / None

    saved = []
    failed = []

    if full_mode:
        oc = creds.load_oauth_creds()  # CredsError → "run: instapaper login"
        try:
            folder_id = folders.resolve(oc, args.folder, create=args.create_folder)
        except folders.FolderError as e:
            die(str(e))
        for url in urls:
            # Never wrap when we're uploading our own --content HTML.
            send_url = url if args.content is not None else freedium.apply(url, cfg, override)
            params = {"url": send_url}
            if args.title:
                params["title"] = args.title
            if args.description:
                params["description"] = args.description
            if content is not None:
                params["content"] = content
            if folder_id is not None:
                params["folder_id"] = folder_id
            if args.tag:
                params["tags"] = json.dumps([{"name": t} for t in args.tag])
            if args.archive:
                params["archived"] = 1
            try:
                result = transport.api_call("/bookmarks/add", params, oc)
                title = _extract_title(result)
                saved.append({"url": send_url, "title": title})
                if not args.json:
                    print("saved: {}".format(title or send_url))
            except (transport.ApiError, transport.NetworkError) as e:
                failed.append({"url": send_url, "error": str(e)})
                if not args.json:
                    _print_error_with_hint("failed: {}".format(send_url), e)
    else:
        sc = creds.simple_creds()
        for url in urls:
            send_url = freedium.apply(url, cfg, override)
            params = dict(sc, url=send_url)
            if args.title:
                params["title"] = args.title
            if args.selection:
                params["selection"] = args.selection
            status, headers = transport.simple_call(transport.SIMPLE_ADD, params)
            if status == 201:
                title = headers.get("X-Instapaper-Title", "").strip()
                saved.append({"url": send_url, "title": title})
                if not args.json:
                    print("saved: {}".format(title or send_url))
            else:
                msg = STATUS_MESSAGES.get(status, "unknown error")
                failed.append({"url": send_url, "error": "{}: {}".format(status, msg)})
                if not args.json:
                    print(
                        "failed ({}): {} — {}".format(status, send_url, msg),
                        file=sys.stderr,
                    )

    if args.json:
        print(json.dumps({"saved": saved, "failed": failed}))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
def _parse_cutoff(datestr):
    """Parse a YYYY-MM-DD --before value to a UTC unix timestamp, or die."""
    try:
        return (
            datetime.strptime(datestr, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except ValueError:
        die("bad date {!r}; use YYYY-MM-DD".format(datestr))


def _saved_str(b):
    """A bookmark's saved date as YYYY-MM-DD (UTC), or '?' if absent."""
    t = b.get("time")
    if not t:
        return "?"
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def _tag_str(b):
    return " ".join(
        "#" + t.get("name", "") for t in (b.get("tags") or []) if isinstance(t, dict)
    )


def _list_rows(oc, folder, limit, before):
    """Shared fetch/filter/sort for `list` and bulk `archive`.

    Returns bookmark dicts in a folder, optionally only those saved strictly
    before a YYYY-MM-DD cutoff, sorted oldest-first.
    """
    try:
        folder_id = folders.resolve(oc, folder, create=False)
    except folders.FolderError as e:
        die(str(e))
    rows = bookmarks.list_bookmarks(oc, folder_id or "unread", limit)
    if before:
        cutoff = _parse_cutoff(before)
        rows = [b for b in rows if (b.get("time") or 0) < cutoff]
    rows.sort(key=lambda b: b.get("time") or 0)
    return rows


def cmd_list(args):
    oc = creds.load_oauth_creds()
    rows = _list_rows(oc, args.folder, args.limit, args.before)

    if args.json:
        print(json.dumps([
            {
                "id": b.get("bookmark_id"),
                "title": b.get("title", ""),
                "url": b.get("url", ""),
                "saved": _saved_str(b),
                "time": b.get("time"),
                "starred": b.get("starred") == "1",
                "tags": [t.get("name") for t in (b.get("tags") or []) if isinstance(t, dict)],
            }
            for b in rows
        ]))
        return 0

    for b in rows:
        star = "*" if b.get("starred") == "1" else " "
        line = "{}  {} {}  {}".format(
            _saved_str(b), star, b.get("bookmark_id"), (b.get("title") or "").strip()
        )
        tags = _tag_str(b)
        if tags:
            line += "  " + tags
        print(line)
    print("{} bookmark(s) in {}".format(len(rows), args.folder), file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# archive / delete (bookmark mutations)
# ---------------------------------------------------------------------------
def _check_ids(ids):
    """Reject anything that isn't a positive integer bookmark id."""
    for bid in ids:
        if not bid.isdigit():
            die("bookmark id must be numeric, got {!r}".format(bid))


def _mutate_bookmarks(oc, ids, fn, verb, as_json):
    """Apply fn(oc, id) to each id, collecting done/failed. Returns an exit code."""
    done, failed = [], []
    for bid in ids:
        try:
            fn(oc, bid)
            done.append(bid)
            if not as_json:
                print("{}: {}".format(verb, bid))
        except (transport.ApiError, transport.NetworkError) as e:
            failed.append({"id": bid, "error": str(e)})
            if not as_json:
                _print_error_with_hint("failed: {}".format(bid), e)
    if as_json:
        print(json.dumps({verb: done, "failed": failed}))
    return 1 if failed else 0


def cmd_archive(args):
    if args.before and args.ids:
        die("pass bookmark id(s) OR --before, not both.")
    if not args.before and not args.ids:
        die("archive needs bookmark id(s), or --before YYYY-MM-DD to bulk-archive by age.")

    oc = creds.load_oauth_creds()

    # Targeted mode: explicit ids, applied immediately (you named them).
    if args.ids:
        _check_ids(args.ids)
        return _mutate_bookmarks(oc, args.ids, bookmarks.archive, "archived", args.json)

    # Bulk mode: everything in --folder older than --before. Dry-run by default
    # (the set is inferred, not named) — --apply commits it.
    targets = _list_rows(oc, args.folder, args.limit, args.before)
    ids = [str(b.get("bookmark_id")) for b in targets]

    if not args.apply:
        if args.json:
            print(json.dumps({"would_archive": [
                {"id": b.get("bookmark_id"), "saved": _saved_str(b), "title": b.get("title", "")}
                for b in targets
            ]}))
        else:
            for b in targets:
                print("[dry-run] would archive: {}  {}  {}".format(
                    _saved_str(b), b.get("bookmark_id"), (b.get("title") or "")[:60]))
            print(
                "{} bookmark(s) in {} older than {}. Re-run with --apply to archive.".format(
                    len(targets), args.folder, args.before),
                file=sys.stderr,
            )
        return 0

    return _mutate_bookmarks(oc, ids, bookmarks.archive, "archived", args.json)


def cmd_unarchive(args):
    _check_ids(args.ids)
    oc = creds.load_oauth_creds()
    return _mutate_bookmarks(oc, args.ids, bookmarks.unarchive, "unarchived", args.json)


def cmd_star(args):
    _check_ids(args.ids)
    oc = creds.load_oauth_creds()
    return _mutate_bookmarks(oc, args.ids, bookmarks.star, "starred", args.json)


def cmd_unstar(args):
    _check_ids(args.ids)
    oc = creds.load_oauth_creds()
    return _mutate_bookmarks(oc, args.ids, bookmarks.unstar, "unstarred", args.json)


def cmd_move(args):
    _check_ids(args.ids)
    oc = creds.load_oauth_creds()
    try:
        folder_id = folders.resolve(oc, args.folder, create=args.create_folder)
    except folders.FolderError as e:
        die(str(e))
    if folder_id is None:
        die("--folder is required for move.")
    return _mutate_bookmarks(
        oc, args.ids,
        lambda oc_, bid: bookmarks.move(oc_, bid, folder_id),
        "moved", args.json,
    )


def cmd_delete(args):
    _check_ids(args.ids)
    if not args.yes:
        die(
            "this permanently deletes {} bookmark(s) — no undo, and their "
            "highlights go with them. Re-run with --yes to confirm.".format(len(args.ids))
        )
    oc = creds.load_oauth_creds()
    return _mutate_bookmarks(oc, args.ids, bookmarks.delete, "deleted", args.json)


# ---------------------------------------------------------------------------
# folder
# ---------------------------------------------------------------------------
def _folder_row(f):
    """Reduce a folder object to the fields we print/emit."""
    return {
        "folder_id": f.get("folder_id"),
        "title": f.get("title") or f.get("display_title") or "",
        "count": f.get("count"),
    }


def cmd_folder_list(args):
    oc = creds.load_oauth_creds()
    rows = [_folder_row(f) for f in folders.list_folders(oc)]
    if args.json:
        print(json.dumps(rows))
    elif not rows:
        print("no folders yet. Create one with: instapaper folder add <title>")
    else:
        width = max(len(str(r["folder_id"])) for r in rows)
        for r in rows:
            count = "" if r["count"] is None else "  ({})".format(r["count"])
            print("{:<{w}}  {}{}".format(r["folder_id"], r["title"], count, w=width))
    return 0


def cmd_folder_add(args):
    oc = creds.load_oauth_creds()
    folder, created = folders.get_or_create(oc, args.title)
    row = _folder_row(folder)
    if args.json:
        print(json.dumps(dict(row, created=created)))
    elif created:
        print("created folder: {} (id {})".format(row["title"], row["folder_id"]))
    else:
        print("folder already exists: {} (id {})".format(row["title"], row["folder_id"]))
    return 0


def cmd_folder_delete(args):
    oc = creds.load_oauth_creds()
    # Resolve every target first, so the --yes preview names exactly what dies.
    targets = []
    for name in args.folders:
        try:
            targets.append(folders.resolve_existing(oc, name))
        except folders.FolderError as e:
            die(str(e))

    if not args.yes:
        listing = ", ".join(
            "{} (id {})".format(_folder_row(f)["title"], f.get("folder_id"))
            for f in targets
        )
        die(
            "this deletes {} folder(s): {}. Bookmarks inside are moved out, not "
            "deleted. Re-run with --yes to confirm.".format(len(targets), listing)
        )

    done, failed = [], []
    for f in targets:
        row = _folder_row(f)
        try:
            folders.delete_folder(oc, f.get("folder_id"))
            done.append(row)
            if not args.json:
                print("deleted folder: {} (id {})".format(row["title"], row["folder_id"]))
        except (transport.ApiError, transport.NetworkError) as e:
            failed.append({"folder_id": f.get("folder_id"), "error": str(e)})
            if not args.json:
                _print_error_with_hint("failed: folder {}".format(f.get("folder_id")), e)
    if args.json:
        print(json.dumps({"deleted": done, "failed": failed}))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def cmd_auth(args):
    username = creds.get_username()
    sc = creds.simple_creds()
    status, _ = transport.simple_call(transport.SIMPLE_AUTH, sc)
    simple_ok = status == 200
    if simple_ok:
        print("simple API: ok (authenticated as {})".format(username))
    else:
        msg = STATUS_MESSAGES.get(status, "unexpected status {}".format(status))
        print("simple API: failed — {}".format(msg), file=sys.stderr)

    # Full-API layer is best-effort: report it, but don't let its absence or
    # failure change the simple-auth verdict.
    try:
        oc = creds.load_oauth_creds()
    except creds.CredsError:
        print("full API: not configured (run: instapaper login)")
        oc = None
    if oc is not None:
        try:
            resp = transport.api_call("/account/verify_credentials", {}, oc)
            print("full API: ok (username {})".format(_extract_username(resp)))
        except (transport.ApiError, transport.NetworkError) as e:
            _print_error_with_hint("full API: failed", e)

    return 0 if simple_ok else 1


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------
def cmd_login(args):
    ck = args.consumer_key or os.environ.get("INSTAPAPER_CONSUMER_KEY")
    if not ck:
        die("no consumer key. Pass --consumer-key or set INSTAPAPER_CONSUMER_KEY.")

    cs = os.environ.get("INSTAPAPER_CONSUMER_SECRET")
    if cs is None:
        cs = getpass.getpass("Instapaper OAuth consumer secret: ")

    username = creds.get_username()
    password = creds.get_password(username)

    token, secret = transport.xauth_access_token(ck, cs, username, password)
    creds.save_oauth_creds(creds.OAuthCreds(ck, cs, token, secret))
    print("logged in as {}".format(username))
    return 0


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def _config_dict(cfg):
    return {
        "freedium_enabled": cfg.freedium_enabled,
        "freedium_mirror": cfg.freedium_mirror,
        "freedium_domains": list(cfg.freedium_domains),
        "export_dir": cfg.export_dir,
    }


def cmd_config_show(args):
    cfg = config.load_config()
    if args.json:
        print(json.dumps(_config_dict(cfg)))
    else:
        print("freedium: {}".format("on" if cfg.freedium_enabled else "off"))
        print("freedium mirror: {}".format(cfg.freedium_mirror))
        print("freedium domains: {}".format(", ".join(cfg.freedium_domains) or "(none)"))
        print("export-dir: {}".format(cfg.export_dir or "(unset)"))
    return 0


def cmd_config_freedium_on(args):
    cfg = config.load_config()
    cfg.freedium_enabled = True
    config.save_config(cfg)
    print("freedium: on")
    return 0


def cmd_config_freedium_off(args):
    cfg = config.load_config()
    cfg.freedium_enabled = False
    config.save_config(cfg)
    print("freedium: off")
    return 0


def cmd_config_freedium_mirror(args):
    cfg = config.load_config()
    cfg.freedium_mirror = args.url
    config.save_config(cfg)
    print("freedium mirror: {}".format(cfg.freedium_mirror))
    return 0


def cmd_config_freedium_add(args):
    cfg = config.load_config()
    if args.domain not in cfg.freedium_domains:
        cfg.freedium_domains.append(args.domain)
        config.save_config(cfg)
    print("freedium domains: {}".format(", ".join(cfg.freedium_domains)))
    return 0


def cmd_config_freedium_remove(args):
    cfg = config.load_config()
    if args.domain in cfg.freedium_domains:
        cfg.freedium_domains.remove(args.domain)
        config.save_config(cfg)
    print("freedium domains: {}".format(", ".join(cfg.freedium_domains) or "(none)"))
    return 0


def cmd_config_export_dir(args):
    cfg = config.load_config()
    cfg.export_dir = os.path.expanduser(args.path)
    config.save_config(cfg)
    print("export-dir: {}".format(cfg.export_dir))
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def cmd_export(args):
    out = args.out or config.load_config().export_dir
    if not out:
        die("set --out or run: instapaper config export-dir <path>")

    oc = creds.load_oauth_creds()
    result = sync.sync(
        out,
        oc,
        folder=args.folder,
        limit=args.limit,
        refresh_highlights=args.refresh_highlights,
        prune=args.prune,
        dry_run=args.dry_run,
        log=lambda m: print(m, file=sys.stderr),
    )

    if args.json:
        print(
            json.dumps(
                {
                    "created": result.created,
                    "updated": result.updated,
                    "pruned": result.pruned,
                    "unchanged": result.unchanged,
                    "errors": result.errors,
                }
            )
        )
    else:
        print(
            "created {}, updated {}, pruned {}, unchanged {}".format(
                len(result.created),
                len(result.updated),
                len(result.pruned),
                result.unchanged,
            )
        )
        for err in result.errors:
            print(err, file=sys.stderr)

    return 1 if result.errors else 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="instapaper",
        description="Save URLs to Instapaper and export your library to Markdown.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- add -------------------------------------------------------------
    p_add = sub.add_parser("add", help="save one or more URLs")
    p_add.add_argument("urls", nargs="*", help="URLs to save")
    p_add.add_argument("--title", help="override the article title (single URL only)")
    p_add.add_argument("--selection", help="a note/excerpt for the Simple API (single URL only)")
    p_add.add_argument("--stdin", action="store_true", help="read URLs from stdin, one per line")
    fg = p_add.add_mutually_exclusive_group()
    fg.add_argument("--freedium", dest="freedium", action="store_true", default=None,
                    help="force-wrap URLs through the freedium mirror")
    fg.add_argument("--no-freedium", dest="freedium", action="store_false",
                    help="never wrap URLs through the freedium mirror")
    p_add.add_argument("--content", metavar="FILE",
                       help="upload page HTML from FILE, or '-' for stdin (Full API; single URL)")
    p_add.add_argument("--description", help="bookmark description (Full API; single URL)")
    p_add.add_argument("--folder", help="destination folder: name, numeric id, or unread/starred/archive")
    p_add.add_argument("--create-folder", action="store_true",
                       help="create --folder if no folder by that name exists, then save into it")
    p_add.add_argument("--tag", action="append", help="tag to attach (repeatable; Full API)")
    p_add.add_argument("--archive", action="store_true", help="add straight to the archive (Full API)")
    p_add.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_add.set_defaults(func=cmd_add)

    # --- list ------------------------------------------------------------
    p_list = sub.add_parser("list", help="list bookmarks in a folder (Full API)")
    p_list.add_argument("--folder", default="unread",
                        help="name, id, or unread/starred/archive (default: unread)")
    p_list.add_argument("--before", metavar="YYYY-MM-DD",
                        help="only bookmarks saved before this date")
    p_list.add_argument("--limit", type=int, default=500, help="max bookmarks (1-500)")
    p_list.add_argument("--json", action="store_true", help="emit a JSON array")
    p_list.set_defaults(func=cmd_list)

    # --- archive / unarchive ---------------------------------------------
    p_archive = sub.add_parser("archive",
                               help="archive bookmark id(s), or bulk-archive by age (reversible)")
    p_archive.add_argument("ids", nargs="*", help="bookmark id(s) to archive")
    p_archive.add_argument("--before", metavar="YYYY-MM-DD",
                           help="bulk-archive everything in --folder saved before this date")
    p_archive.add_argument("--folder", default="unread",
                           help="folder to bulk-archive from (default: unread)")
    p_archive.add_argument("--limit", type=int, default=500, help="max bookmarks scanned (1-500)")
    p_archive.add_argument("--apply", action="store_true",
                           help="actually archive (bulk --before is a dry run without this)")
    p_archive.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_archive.set_defaults(func=cmd_archive)

    p_unarchive = sub.add_parser("unarchive", help="move bookmark(s) out of the Archive")
    p_unarchive.add_argument("ids", nargs="+", help="bookmark id(s) to unarchive")
    p_unarchive.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_unarchive.set_defaults(func=cmd_unarchive)

    # --- star / unstar ---------------------------------------------------
    p_star = sub.add_parser("star", help="star bookmark(s)")
    p_star.add_argument("ids", nargs="+", help="bookmark id(s) to star")
    p_star.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_star.set_defaults(func=cmd_star)

    p_unstar = sub.add_parser("unstar", help="remove star from bookmark(s)")
    p_unstar.add_argument("ids", nargs="+", help="bookmark id(s) to unstar")
    p_unstar.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_unstar.set_defaults(func=cmd_unstar)

    # --- move ------------------------------------------------------------
    p_move = sub.add_parser("move", help="move bookmark(s) into a folder")
    p_move.add_argument("ids", nargs="+", help="bookmark id(s) to move")
    p_move.add_argument("--folder", required=True,
                        help="destination folder: name, id, or unread/starred/archive")
    p_move.add_argument("--create-folder", action="store_true",
                        help="create --folder if no folder by that name exists")
    p_move.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_move.set_defaults(func=cmd_move)

    # --- delete ----------------------------------------------------------
    p_delete = sub.add_parser("delete", help="permanently delete bookmark(s) — no undo")
    p_delete.add_argument("ids", nargs="+", help="bookmark id(s) to delete")
    p_delete.add_argument("--yes", action="store_true", help="confirm the deletion (required)")
    p_delete.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_delete.set_defaults(func=cmd_delete)

    # --- auth ------------------------------------------------------------
    p_auth = sub.add_parser("auth", help="verify stored credentials (both API layers)")
    p_auth.set_defaults(func=cmd_auth)

    # --- login -----------------------------------------------------------
    p_login = sub.add_parser("login", help="obtain and store Full-API OAuth tokens")
    p_login.add_argument("--consumer-key", help="OAuth consumer key (else INSTAPAPER_CONSUMER_KEY)")
    p_login.set_defaults(func=cmd_login)

    # --- folder ----------------------------------------------------------
    p_folder = sub.add_parser("folder", help="list, create, or delete Full-API folders")
    folder_sub = p_folder.add_subparsers(dest="folder_cmd", required=True)
    p_flist = folder_sub.add_parser("list", help="list folders with their ids")
    p_flist.add_argument("--json", action="store_true", help="emit JSON")
    p_flist.set_defaults(func=cmd_folder_list)
    p_fadd = folder_sub.add_parser("add", help="create a folder (idempotent by name)")
    p_fadd.add_argument("title", help="folder display name")
    p_fadd.add_argument("--json", action="store_true", help="emit JSON")
    p_fadd.set_defaults(func=cmd_folder_add)
    p_fdel = folder_sub.add_parser("delete", help="delete folder(s) by name or id")
    p_fdel.add_argument("folders", nargs="+", help="folder name(s) or id(s) to delete")
    p_fdel.add_argument("--yes", action="store_true", help="confirm the deletion (required)")
    p_fdel.add_argument("--json", action="store_true", help="emit JSON")
    p_fdel.set_defaults(func=cmd_folder_delete)

    # --- config ----------------------------------------------------------
    p_config = sub.add_parser("config", help="view or change settings")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)

    p_show = config_sub.add_parser("show", help="print current settings")
    p_show.add_argument("--json", action="store_true", help="emit JSON")
    p_show.set_defaults(func=cmd_config_show)

    p_freedium = config_sub.add_parser("freedium", help="manage the freedium mirror")
    freedium_sub = p_freedium.add_subparsers(dest="freedium_cmd", required=True)
    freedium_sub.add_parser("on", help="enable freedium wrapping").set_defaults(
        func=cmd_config_freedium_on
    )
    freedium_sub.add_parser("off", help="disable freedium wrapping").set_defaults(
        func=cmd_config_freedium_off
    )
    p_fmirror = freedium_sub.add_parser("mirror", help="set the mirror base URL")
    p_fmirror.add_argument("url")
    p_fmirror.set_defaults(func=cmd_config_freedium_mirror)
    p_fadd = freedium_sub.add_parser("add", help="add a domain to wrap")
    p_fadd.add_argument("domain")
    p_fadd.set_defaults(func=cmd_config_freedium_add)
    p_fremove = freedium_sub.add_parser("remove", help="remove a domain")
    p_fremove.add_argument("domain")
    p_fremove.set_defaults(func=cmd_config_freedium_remove)

    p_exportdir = config_sub.add_parser("export-dir", help="set the default export directory")
    p_exportdir.add_argument("path")
    p_exportdir.set_defaults(func=cmd_config_export_dir)

    # --- export ----------------------------------------------------------
    p_export = sub.add_parser("export", help="export bookmarks + highlights to Markdown")
    p_export.add_argument("--out", help="output directory (else config export-dir)")
    p_export.add_argument("--folder", default="archive",
                          help="unread | starred | archive | all | <folder id> (default: archive)")
    p_export.add_argument("--refresh-highlights", action="store_true",
                          help="re-fetch highlights for every known bookmark")
    p_export.add_argument("--prune", action="store_true",
                          help="delete note files for bookmarks removed upstream")
    p_export.add_argument("--limit", type=int, default=500, help="max bookmarks per folder")
    p_export.add_argument("--dry-run", action="store_true", help="report intended actions only")
    p_export.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (creds.CredsError, transport.ApiError, transport.NetworkError) as e:
        print("instapaper: {}".format(e), file=sys.stderr)
        hint = _hint_for(e)
        if hint:
            print("  hint: {}".format(hint), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
