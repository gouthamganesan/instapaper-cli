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

from instapaper_cli import creds, config, freedium, transport, sync

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


def _resolve_folder(folder):
    """Resolve --folder to a folder_id param value.

    Numeric → used as an id. The literals unread/starred/archive pass through.
    A non-numeric folder NAME is not resolvable in v1 (we don't call
    folders/list) → die with a clear message.
    """
    if folder is None:
        return None
    if folder.isdigit():
        return folder
    if folder in ("unread", "starred", "archive"):
        return folder
    die(
        "folder '{}': folder-name resolution isn't supported yet. "
        "Use a numeric folder id, or one of: unread, starred, archive.".format(folder)
    )


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
        folder_id = _resolve_folder(args.folder)
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
    p_add.add_argument("--folder", help="destination folder: numeric id, or unread/starred/archive")
    p_add.add_argument("--tag", action="append", help="tag to attach (repeatable; Full API)")
    p_add.add_argument("--archive", action="store_true", help="add straight to the archive (Full API)")
    p_add.add_argument("--json", action="store_true", help="emit a JSON summary object")
    p_add.set_defaults(func=cmd_add)

    # --- auth ------------------------------------------------------------
    p_auth = sub.add_parser("auth", help="verify stored credentials (both API layers)")
    p_auth.set_defaults(func=cmd_auth)

    # --- login -----------------------------------------------------------
    p_login = sub.add_parser("login", help="obtain and store Full-API OAuth tokens")
    p_login.add_argument("--consumer-key", help="OAuth consumer key (else INSTAPAPER_CONSUMER_KEY)")
    p_login.set_defaults(func=cmd_login)

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
