# instapaper-cli

Save URLs to your Instapaper inbox from the command line — and, if you want
more, export your library (bookmarks, highlights, notes, full article text) to
a folder of Markdown files. Python 3, stdlib only, no dependencies.

```sh
instapaper add "https://example.com/article"
saved: The Article's Real Title
```

Built to be driven by a coding agent (Claude Code, Cursor, etc.) as much as by
a human — so it takes no interactive input outside `login`, never puts a
password on the command line, and reports failure through exit codes. See
[AGENTS.md](AGENTS.md) if you're wiring it into an agent.

## Why this exists

Instapaper's [Simple API](https://www.instapaper.com/developers/v1/simple-api)
is a single endpoint. Existing wrappers are heavier than the thing they wrap —
the best-known one, [dakrone/ricepaper](https://github.com/dakrone/ricepaper),
is an unmaintained 2010-era Ruby gem that takes your password as a `-p` flag.
This started as roughly the same feature set in a single stdlib Python file,
with credentials pulled from the OS keychain instead.

It has since grown into a small package that also speaks Instapaper's
[Full API](https://www.instapaper.com/developers/v1/full-api) — OAuth 1.0a,
signed requests, the works — for the things the Simple API structurally cannot
do: folders, tags, archiving, and pulling your library (with highlights and
notes) back out as Markdown. The zero-setup path is untouched: if you never
touch the Full-API flags, this behaves exactly like v1.

## Install

Requires Python 3.8+. macOS for keychain-backed credentials; anything else
works via environment variables.

```sh
git clone https://github.com/gouthamganesan/instapaper-cli.git
cd instapaper-cli
chmod +x instapaper
ln -s "$PWD/instapaper" /usr/local/bin/instapaper   # or anywhere on your PATH
```

The `instapaper` file is a thin shim; the real code lives in the
`instapaper_cli/` package beside it. `git pull` is the whole upgrade story —
no install step, no virtualenv, no lockfile.

## Configure

### Username

Your Instapaper login. Instapaper usernames are usually, but not always, an
email address.

```sh
mkdir -p ~/.config/instapaper
echo -n 'you@example.com' > ~/.config/instapaper/username
```

Or set `INSTAPAPER_USERNAME`, which takes precedence.

### Password (Simple API + `login`)

Resolved in this order:

1. `INSTAPAPER_PASSWORD` environment variable, if set (including to an empty
   string). Use this on Linux, in CI, or in containers.
2. macOS Keychain, service `instapaper`, account matching your username.

```sh
security add-generic-password -s instapaper -a "you@example.com" -w
```

The `-w` flag prompts interactively, so the password never enters your shell
history. **Many Instapaper accounts have no password at all** — if yours is
one, store an empty string. The tool treats that as valid, not as missing.

Verify:

```sh
instapaper auth
simple API: ok (authenticated as you@example.com)
full API: not configured (run: instapaper login)
```

### Advanced setup: the Full API (OAuth)

This is optional. Skip it if `add` and `auth` are all you need. It unlocks
`--content`, `--folder`, `--tag`, `--archive`, `--description` on `add`, plus
the `export` command.

1. Register an app at
   [instapaper.com/developers/applications/create](https://www.instapaper.com/developers/applications/create).
   It starts in **Owner Only** mode, which works immediately against your own
   account — no emailed approval wait. You get a Consumer Key and a Consumer
   Secret.
2. Run:

   ```sh
   instapaper login --consumer-key <YOUR_CONSUMER_KEY>
   Instapaper OAuth consumer secret:      # hidden prompt (or set INSTAPAPER_CONSUMER_SECRET)
   logged in as you@example.com
   ```

   `login` does a one-time xAuth exchange: your account password (resolved
   the same way as above) is used once to obtain an OAuth token, then never
   touched again. The token is written to
   `~/.config/instapaper/credentials.json` at file mode `0600`.

That's it — `instapaper auth` now reports both layers, and the Full-API flags
on `add` and the `export` command work.

Per-field environment overrides exist if you'd rather not use the credentials
file (CI, containers): `INSTAPAPER_CONSUMER_KEY`, `INSTAPAPER_CONSUMER_SECRET`,
`INSTAPAPER_OAUTH_TOKEN`, `INSTAPAPER_OAUTH_SECRET`.

## Usage

### `add` — save URLs

```sh
# One URL
instapaper add "https://example.com/article"

# Several at once
instapaper add "https://a.com" "https://b.com" "https://c.com"

# With a custom title and a note (single URL only)
instapaper add "https://example.com" --title "Custom title" --selection "why I saved this"

# From a pipe or a file
cat urls.txt | instapaper add --stdin
pbpaste | instapaper add --stdin

# JSON output instead of human-readable lines
instapaper add "https://example.com" --json
```

By default `add` uses the zero-setup **Simple API** — identical to v1. It
automatically routes through the **Full API** instead, the moment you pass any
of `--content`, `--folder`, `--tag`, `--archive`, or `--description` (this
requires `login` first; see above). You don't choose the API explicitly, the
flags you use choose it for you:

```sh
# Simple API (default) — no login required
instapaper add "https://example.com"

# Full API (auto-routed) — needs `instapaper login` first
instapaper add "https://example.com" --folder starred --tag longread --archive
instapaper add "https://example.com" --description "for the reading list"
```

`--title` and `--selection`/`--description` apply to a single URL. Passing
them alongside multiple URLs is rejected rather than silently applied to all
of them. Same rule for `--content` (below).

**Paywalled, login-gated, or Medium-style pages** — Instapaper's server-side
fetcher can't see past a login wall, and Medium in particular blocks it
outright. Two independent escape hatches, usable together or separately:

```sh
# Upload the page's own HTML instead of letting Instapaper re-fetch it
# (e.g. pull it from a browser extension, curl with your own cookies, etc.)
instapaper add "https://example.com/paywalled" --content page.html
cat page.html | instapaper add "https://example.com/paywalled" --content -

# Force-wrap through a Freedium-style mirror that de-paywalls Medium
instapaper add "https://medium.com/@x/some-post" --freedium
```

`--content` is Full-API only (it implies routing there) and single-URL only.
When `--content` is supplied, the URL is sent verbatim — it is *never*
Freedium-wrapped, so the bookmark's identity stays the real article URL, not a
mirror URL.

**Freedium auto-wrapping** happens even without `--content` or `--folder`:
any URL whose host matches an entry in the configured domain list (seeded with
`medium.com`, and its subdomains) gets silently rewritten to go through the
mirror — *if* freedium is turned on (`instapaper config freedium on`; off by
default). `--freedium` forces wrapping for this call regardless of config;
`--no-freedium` disables it for this call regardless of config. See
[Behaviour worth knowing](#behaviour-worth-knowing) for what wrapping actually
does to the saved bookmark.

**Exit codes:** `0` if every URL saved, `1` if any failed or if configuration
is missing. Per-URL failures print to stderr and do not abort the remaining
URLs. `--json` emits `{"saved": [...], "failed": [...]}` on stdout instead of
per-line text (still exit `1` on any failure).

On success the tool prints the title Instapaper resolved server-side — from
`X-Instapaper-Title` on the Simple path, from the response body on the
Full-API path — which is a useful signal that the article actually parsed,
not just that the URL was accepted.

### `auth` — verify credentials

```sh
instapaper auth
```

Checks **both** layers independently: the Simple API's `authenticate`
endpoint, and — if OAuth credentials exist — the Full API's
`verify_credentials`. A missing Full-API login is reported, not treated as an
error; the command's exit code reflects the Simple-API result only, since
that's the layer everything else depends on.

### `login` — one-time OAuth setup

```sh
instapaper login --consumer-key <KEY>
```

See [Advanced setup](#advanced-setup-the-full-api-oauth) above. Idempotent —
re-running it re-does the token exchange and overwrites the stored credentials.

### `config` — settings

```sh
instapaper config show                       # human-readable
instapaper config show --json

instapaper config freedium on                 # enable auto-wrapping
instapaper config freedium off                # disable it (default)
instapaper config freedium mirror "https://freedium.cfd/"
instapaper config freedium add "some-blog.com"
instapaper config freedium remove "medium.com"

instapaper config export-dir "~/Documents/Instapaper Exports"
```

Settings live in `~/.config/instapaper/config.json` (non-secret — freedium
config and the default export directory only; credentials are never in here).

### `export` — pull your library to Markdown

```sh
instapaper export                              # uses config export-dir, folder=archive
instapaper export --out ~/vault/Clippings/instapaper
instapaper export --folder unread
instapaper export --folder all                  # unread + starred + archive, unioned
instapaper export --folder 12345678              # a numeric folder id
instapaper export --refresh-highlights           # re-fetch highlights for every known bookmark
instapaper export --prune                        # delete local files for server-deleted bookmarks
instapaper export --dry-run                      # report what would happen, write nothing
instapaper export --limit 200 --json
```

For each bookmark, `export` pulls its metadata, its highlights (with your
notes), and the full article text (Instapaper's `get_text`), converts the
article HTML to Markdown, and writes one `<title> (<bookmark_id>).md` file per
article — YAML frontmatter, a `## Highlights` section with `[!quote]` callouts
per highlight, then `## Article`.

It's **incremental**: a `.instapaper-sync.json` file inside the export
directory tracks each bookmark's server-side content hash and known highlight
IDs, and every run sends that state back to Instapaper (`have=id:hash,...`,
`highlights=id-id-...`) so the server tells it exactly what changed. A repeat
`export` with nothing new touches nothing. **Read-only against the server** —
it never pushes reading progress or anything else upstream; the traffic is
one-way, Instapaper → your disk.

- `--folder all` loops `unread`, `starred`, `archive` and unions the results
  (a bookmark can appear in more than one virtual folder).
- `--refresh-highlights` forces every already-known bookmark to be re-checked
  for highlights this run, useful if you suspect a highlight was added without
  the bookmark's own hash changing.
- `--prune` actually deletes the local `.md` file for a bookmark that no
  longer exists upstream. Without it, a deleted bookmark is just dropped from
  the sync state (the file is left alone) — the safer default.
- `--dry-run` runs the whole diff and logs intended actions but writes no
  files and doesn't touch the sync state.

## What this can and can't do

The **Simple API** (used by default `add`, and by `auth`) is add-only: no
listing, reading, deleting, or folder support, and everything lands in the
inbox. That's an API limit, not an unimplemented feature.

The **Full API** (OAuth, opt-in via `login`) adds:

- `add` with `--folder`, `--tag`, `--archive`, `--description`, `--content`
- `export` — read-only pull of bookmarks, highlights, notes, and article text

Still out of scope, deliberately:

- **No delete.** This tool only ever adds and reads. Deleting or archiving
  from the CLI isn't implemented.
- **No folder-name resolution.** `--folder` on `add` and `export` accepts a
  numeric folder id or the literals `unread` / `starred` / `archive` — it does
  not call `folders/list` to resolve a name like `"Reading Later"` to an id.
  Look the id up yourself (or pass the literal) for now.
- **No two-way sync.** `export` never writes back to Instapaper — no read
  progress, no highlight creation, nothing. It's a one-way mirror.
- **Export files are tool-owned.** A re-export overwrites whatever's in the
  file, including hand-edits. Point `--out` / `config export-dir` at a folder
  you treat as generated output (e.g. `Clippings/instapaper/` in an Obsidian
  vault), not somewhere you keep hand-maintained notes.

## Behaviour worth knowing

**Re-adding a URL does not duplicate it.** Instapaper's documentation states
that adding an existing URL marks it unread and moves it to the top of the
list, still returning `201`. This makes repeat calls safe and is why the tool
keeps no local cache of what it has already sent on the `add` path.

Two caveats on that, stated plainly because they are easy to over-trust:

- The dedup happens server-side on the URL string. `https://x.com/post` and
  `https://x.com/post?utm_source=rss` are different strings and will plausibly
  produce two entries. If you feed it links harvested from mixed sources,
  normalise them first or accept occasional near-duplicates.
- **Freedium-wrapped saves store the mirror URL as the bookmark's identity.**
  If `add` wraps `https://medium.com/@x/post` into
  `https://freedium.cfd/https://medium.com/@x/post`, that wrapped URL — not
  the canonical article URL — is what gets deduped against and what shows up
  in an `export`'s frontmatter. Keep that in mind if you toggle freedium on
  and off over time; the same article saved both ways will look like two
  bookmarks.

**Pages behind a login or a hard paywall** will save via the Simple API or
plain Full-API `add`, but Instapaper's server-side parser may extract little
or nothing — the `201`/success reflects acceptance of the URL, not a
successful article extraction. Use `--content` to hand it the HTML directly
when that matters.

**`get_text` works without an approved Instaparser key for personal use** —
when the app you registered and the account you're authenticated as are the
same person, `export`'s article-text fetch works out of the box under "Owner
Only" mode. That's what makes the advanced setup a two-step, no-wait process.

## Security notes

- Passwords are read at call time and held only in memory — for the Simple
  API, for `login`'s one-time xAuth exchange. Never written to disk, never
  passed as a command-line argument (where it would be visible in `ps` and
  shell history), never logged.
- The Full-API OAuth token (from `login`) is written to
  `~/.config/instapaper/credentials.json` at mode `0600`, replacing the need
  to hand your password to anything after the first login.
- Simple-API credentials are transmitted to Instapaper over HTTPS as form
  parameters — that's what the API requires; it has no token-based
  alternative. The Full API's OAuth 1.0a signing is the alternative, if that
  tradeoff bothers you.
- `~/.config/instapaper/username` holds only the username, never a password
  or a token.

## How it's built

Still stdlib-only, still zero dependencies — `git pull` is the whole upgrade
story. What changed going from v1 to v2 is the shape: one 180-line script
became a small package behind a thin entrypoint shim.

| Module | Role |
|---|---|
| `instapaper` | Entrypoint shim — puts the package on `sys.path`, hands off to `cli.main`. |
| `instapaper_cli/cli.py` | argparse command tree, thin handlers, the only place that maps exceptions to exit codes and prints. |
| `instapaper_cli/transport.py` | The single HTTP seam — `simple_call` (Simple API), `api_call`/`xauth_access_token` (OAuth-signed Full API). |
| `instapaper_cli/oauth.py` | Pure OAuth 1.0a HMAC-SHA1 request signing. Zero I/O. |
| `instapaper_cli/creds.py` | The only place secrets are touched — username/password/OAuth-token resolution. |
| `instapaper_cli/config.py` | Non-secret settings (freedium, export dir), atomic JSON writes. |
| `instapaper_cli/freedium.py` | Paywall-mirror URL wrapping logic. |
| `instapaper_cli/htmlmd.py` | Deliberately-lossy HTML → Markdown conversion for exported article text. |
| `instapaper_cli/render.py` | Turns a bookmark + highlights + article Markdown into one note file. |
| `instapaper_cli/sync.py` | The incremental export engine — diffing against Instapaper via `have`/hash. |

84 unit tests cover the library modules (`python3 instapaper_cli/test_*.py`,
or `python3 -m unittest discover -s instapaper_cli -p 'test_*.py'`); see
[Testing](AGENTS.md#testing) in AGENTS.md for the doctrine. Deeper
design/teaching notes on how the pieces fit together live in
[`docs/`](docs/) — an interlinked set covering the OAuth signing math, the
incremental sync algorithm, and the freedium/routing decisions — start there
if you want the internals, not just the interface.

## Licence

MIT — see [LICENSE](LICENSE).
