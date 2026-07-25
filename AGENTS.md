# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Copilot, etc.) using or
modifying this repo. Humans want [README.md](README.md).

## Using the tool

### `add` — the common case

The invocation an agent almost always wants:

```sh
instapaper add "<url>"
```

Trigger on user phrasings like "save this to Instapaper", "read this later",
"add these to my inbox". Batch multiple URLs into one call rather than
looping:

```sh
instapaper add "https://a.com" "https://b.com" "https://c.com"
```

This uses the zero-setup **Simple API** and needs no credential beyond the
username/password already configured (see README's Configure section — don't
try to set that up yourself, it's a one-time manual step for the human).

### The routing rule

`add` silently switches to the **Full API** (OAuth) the moment the call
includes any of `--content`, `--folder`, `--tag`, `--archive`, or
`--description`. There is no separate "full add" subcommand — the flags you
pass decide the API, and that in turn decides what's required:

- No routing flags → Simple API. Works immediately, no `login` needed.
- Any routing flag present → Full API. Requires `instapaper login` to have
  been run already. If it hasn't, the call fails with a `CredsError` whose
  message says `run: instapaper login` — surface that to the user rather than
  trying to work around it (see "Do not" below).

Only reach for the Full-API flags when the user's request actually needs them
— a folder, a tag, archiving on save, or a paywalled/login-gated page that
needs `--content`. Don't add `--folder unread` etc. out of habit; it forces
Full-API routing (and its `login` prerequisite) for no benefit over the
Simple-API default.

```sh
# Paywalled or Medium-style page: two independent escape hatches
instapaper add "https://medium.com/@x/post" --freedium
instapaper add "https://example.com/paywalled" --content page.html
```

`--content` is single-URL only and, when present, the URL is sent verbatim —
never Freedium-wrapped.

### `export` — pulling the library back out

```sh
instapaper export                          # default folder=archive, --out or config export-dir
instapaper export --folder unread
instapaper export --dry-run                # preview only, no writes
```

Trigger this when the user asks to "back up my Instapaper", "sync my
highlights", "get my saved articles into my vault/notes", or similar — not on
every `add`. `export` always requires `login` to have been run (it's Full-API
only). It's safe to re-run: incremental via server-side hashing, and
`--dry-run` exists precisely so you can preview a first run's scope (file
count, roughly) before actually writing into the user's filesystem.

Each Full-API request retries on a timeout/network/5xx failure (`--retries`,
default 2) with backoff, and a bookmark that still fails is **skipped and
recorded, not fatal** — the run finishes and reports the skips in the errors
list, so a single flaky `get_text` no longer aborts the whole export. Bump
`--timeout` (seconds, default 30) or `--retries` for a slow/unreliable link
rather than wrapping the command in a shell `timeout` (macOS has none).

**Treat exported files as generated output, not something to hand-edit.** A
re-export overwrites the file for any bookmark whose hash or highlights
changed. If the user wants notes alongside an exported article, that's a
separate file, not an edit to the exported `.md`.

### `folder` — see, create, delete folders

```sh
instapaper folder list                        # read-only: id + title per folder
instapaper folder add "Reading List"           # create (idempotent by name)
instapaper folder delete "Reading List" --yes  # delete by name or id (needs --yes)
```

Full-API only (`login` required). Reach for `folder list` before saving into a
named folder you're unsure exists — it's read-only and cheap. `folder add`
won't duplicate: naming an existing folder just returns its id. To save
*into* a folder, prefer `add --folder "<name>"` (and `--create-folder` if it
may not exist yet) over creating the folder separately — one call does both.
`folder delete` **refuses without `--yes`** and previews what it would remove;
deleting a folder moves its bookmarks out rather than destroying them.

### `archive` / `delete` — mutate existing bookmarks

```sh
instapaper archive <id> [<id> ...]            # move to Archive (reversible)
instapaper unarchive <id> [<id> ...]          # move back out
instapaper star <id> / unstar <id>            # (un)star
instapaper move <id> --folder "<name>"        # move into a folder (resolves name, --create-folder)
instapaper delete <id> [<id> ...] --yes       # PERMANENT — refuses without --yes
```

All take **bookmark ids** (not URLs) and are Full-API only. Get ids from
`instapaper list` (below) — don't guess one, and if the user gives you a URL,
resolve it via `list --json` or `export --json` first. `archive`/`star`/`move`
are reversible. `delete` is irreversible and takes the bookmark's highlights
with it, so it refuses without `--yes` — surface that refusal and let the user
decide, rather than adding `--yes` reflexively.

### `list` — read a folder (find ids, triage the queue)

```sh
instapaper list                               # unread, oldest-first: date, id, title, tags
instapaper list --folder starred --json
instapaper list --before YYYY-MM-DD           # only saves older than a date
instapaper list --order newest                # newest-first (the app's order); default is oldest
```

Read-only, always safe. This is the id-lookup primitive for the mutation verbs,
and the cheap answer to "what's in my inbox / how old is it" — reach for it
rather than `export` (which downloads every article's full text) when the user
just wants to *see* the queue. `list` is **oldest-first** by default while the
app shows a folder **newest-first** — pass `--order newest` for the app's order
instead of reversing the output yourself. In `--json`, the id field is
`bookmark_id` (see the schema note under "Check the exit code" below).

### bulk archive by date

```sh
instapaper archive --before YYYY-MM-DD           # DRY RUN (lists what it would archive)
instapaper archive --before YYYY-MM-DD --apply   # actually archives; --folder defaults to unread
```

`archive` with `--before` (instead of ids) bulk-archives everything in `--folder`
older than the date. **Never run it with `--apply` without showing the dry run
first**, and never on a queue the user hasn't seen — bulk archiving by date is
easy to get wrong, and the dry-run is the ceremony that keeps it honest. (This
replaced the old `tools/inbox.py`, which was folded into the CLI and removed.)

## What NOT to do

**Do not** run `instapaper delete` reflexively or in bulk. It is permanent —
it destroys the bookmark and its highlights — and it takes bookmark ids, so a
wrong id deletes the wrong thing irreversibly. The `--yes` gate exists for a
reason: when the user asks to delete, confirm you have the *right* ids (resolve
from a URL via `instapaper list --json` first if needed) before adding `--yes`.
Prefer `archive` (reversible) whenever the user's real intent is "get this out
of my unread queue" rather than "destroy it forever."

**Do not** treat `folder delete` as harmless because the bookmarks survive —
it still needs `--yes` and still previews. Don't pass `--yes` on the user's
behalf without their go-ahead.

**Do not** fabricate a folder id or guess one. You don't need to: `--folder`
now takes the folder's **display name** and resolves it via `folders/list`
itself (a numeric id and the literals `unread` / `starred` / `archive` still
work too). If the user names a folder that doesn't exist yet, the call errors
and lists what does — pass `--create-folder` to create-and-save, or run
`instapaper folder add "<name>"` first. Use `instapaper folder list` to see the
real folders rather than inventing an id. (`export --folder` is the one place
that still wants an id, not a name.)

**Do not** ask the user to paste their account password, and do not write it
into a file or a command. Credential setup (username file + Keychain entry) is
a one-time manual step the user performs themselves. If `instapaper auth`
fails with a credential error, surface the setup instructions from the error
message rather than trying to work around them.

**Do not** run `instapaper login` on the user's behalf without them present —
it prompts for the OAuth consumer secret via a hidden `getpass` prompt (or
reads `INSTAPAPER_CONSUMER_SECRET`), and uses the account password once. If
the user wants Full-API features, tell them what `login` needs (a Consumer
Key + Secret from Instapaper's developer console — see README) and let them
run it, or run it interactively with them watching.

**Check the exit code**, don't parse stdout for success/failure — use `--json`
if you need structured output. `0` = everything succeeded (all URLs saved, or
export completed with no per-bookmark errors), `1` = something failed.
Per-item errors go to stderr (or the JSON `failed`/`errors` array) and do not
stop the rest of the batch, so a partial success still exits `1` — that means
"at least one failed," not "nothing worked." For a multi-id mutation a summary
line (`deleted 3`, or `deleted 2, failed 1`) also lands on stderr so a
half-failed batch is visible without parsing.

**One canonical id field: `bookmark_id`.** Every `--json` surface that names a
bookmark's id uses the key `bookmark_id` — `list`, `add` (`saved[].bookmark_id`,
`null` on the Simple-API path since it returns no id), the mutation verbs'
`failed[].bookmark_id`, and `export` frontmatter. So the id you read out of
`add --json` (Full-API path) or `list --json` feeds straight into `move` /
`delete` / `archive` with no field renaming and no follow-up lookup. Don't
write a script against a bare `id` key — that inconsistency was removed.

Re-sending a URL the user already saved via plain `add` is harmless —
Instapaper bumps rather than duplicates — so there's no need to track what
you've sent. Three things still worth knowing, from the README: dedup is
URL-string exact (tracking-parameter variants can double); a Freedium-wrapped
URL dedupes against its wrapped form, not the canonical article URL (toggling
freedium on/off for the same article can produce two bookmarks); and the
"bumps to the top" behaviour is the **Simple-API path only** — a Full-API
re-add (with `--content`/`--folder`/…) updates the bookmark **in place**,
keeping its `bookmark_id` and its existing position. So re-adding with new
`--content` updates the stored HTML but will **not** reorder a folder, and
there is no ordering/bump primitive to force it — if the user needs a reading
order, save the items in that order (oldest first) and read back with
`list --order`.

**`--content` uploads keep external image URLs as-is** — Instapaper proxies
them, it does not re-host. Its proxy is stricter than a plain fetch: images
whose URL uses standard base64 (`+`/`/`) or a multi-parameter query string can
render as broken boxes even though `curl` gets a clean `200`. When you generate
`--content` HTML with external images (diagram services, etc.), prefer url-safe
base64 (`-`/`_`) and single-parameter URLs, and verify the images actually
render in the reader — a successful `curl` is not proof.

## Modifying the code

- **Stdlib only.** No `requests`, no `click`, no YAML library. The
  zero-dependency property is the point — it makes the tool droppable into
  any environment without a virtualenv, and `git pull` is the entire upgrade
  story. A PR adding a dependency needs to justify itself against that.
- **Never accept a password or secret as a CLI argument.** This is the
  specific design flaw the tool exists to avoid. Command-line arguments are
  visible in `ps` output and shell history. That's why `login` reads the
  consumer secret via `getpass` (or an env var) instead of a flag, and why
  OAuth tokens live in a file, not an argument.
- **An empty password is valid, not missing.** Many Instapaper accounts have
  none. `creds.get_password` uses `is not None` on `INSTAPAPER_PASSWORD` — a
  truthiness check there would be a bug (it would treat `""` as unset and fall
  through to the Keychain).
- **Library modules never call `sys.exit` or print.** Only `cli.py` does that
  — every other module raises (`CredsError`, `ApiError`, `NetworkError`) and
  lets `cli.py` translate exceptions into exit codes and stderr output. Keep
  new code inside that boundary; it's what makes the modules testable without
  spawning a subprocess.
- **Secrets touch disk in exactly one place beyond the Keychain**:
  `creds.save_oauth_creds` writes `~/.config/instapaper/credentials.json` via
  `os.open(..., 0o600)` — mode set at creation, not chmod'd after. Don't
  introduce a second path that writes credentials.

## Structure

| Module | Role |
|---|---|
| `instapaper` | Entrypoint shim. Puts the package dir's parent on `sys.path` (via the resolved real path, so a symlink onto `PATH` keeps working) and hands off to `instapaper_cli.cli.main`. Not itself a target for edits — the orchestrating logic lives in the package. |
| `instapaper_cli/cli.py` | argparse command tree + thin handlers. The ONLY module that maps exceptions to exit codes and prints to stdout/stderr. |
| `instapaper_cli/transport.py` | The single HTTP/OAuth seam. `simple_call` (unsigned form POST, Simple API) and `api_call`/`xauth_access_token` (OAuth-signed, Full API). Every network-touching call in the repo funnels through here. |
| `instapaper_cli/oauth.py` | Pure OAuth 1.0a HMAC-SHA1 request signer. Zero I/O, zero network — the hardest module to get right (percent-encoding, base-string construction); has a signature-vector test. |
| `instapaper_cli/creds.py` | The only place secrets are touched. Username/password/OAuth-token resolution and persistence. |
| `instapaper_cli/config.py` | Non-secret settings (freedium config, default export dir) at `~/.config/instapaper/config.json`. Atomic writes (temp file + `os.replace`). |
| `instapaper_cli/freedium.py` | Decides whether/how to rewrite a URL through a paywall-mirror before sending it to Instapaper. |
| `instapaper_cli/folders.py` | Folder API + name→id resolution — `list_folders`, `add_folder`, `delete_folder`, `resolve` (name/id/literal, create-on-miss), `resolve_existing` (for delete). Raises, never prints. |
| `instapaper_cli/bookmarks.py` | Bookmark read/mutation seam — `list_bookmarks`, `archive`, `unarchive`, `star`, `unstar`, `move`, `delete`. One function per `/bookmarks/*` verb; raises, never prints. |
| `instapaper_cli/htmlmd.py` | Deliberately-lossy HTML → Markdown converter (stdlib `html.parser`) used to render exported article text. |
| `instapaper_cli/render.py` | Turns a bookmark dict + highlight list + article Markdown into one note file's exact text (frontmatter, `[!quote]` callouts, body). |
| `instapaper_cli/sync.py` | The incremental `export` engine — diffs local state against the server via `have=id:hash` and a highlight-id delta, fetches only what's dirty. |

The Simple API contract this depends on is documented at
<https://www.instapaper.com/developers/v1/simple-api>; the Full API at
<https://www.instapaper.com/developers/v1/full-api>. Read the relevant one
before changing request construction.

## Testing

148 unit tests across the library modules, stdlib `unittest`, no pytest
dependency:

```sh
python3 -m unittest discover -s instapaper_cli -p 'test_*.py'
# or, per-module:
python3 instapaper_cli/test_oauth.py
python3 instapaper_cli/test_transport.py
```

Mock at the network boundary — `urllib.request.urlopen` for `transport.py`,
or `transport.simple_call`/`transport.api_call` for anything built on top of
it (`sync.py`, `cli.py`). Never require real credentials or hit the real
Keychain in a test; `creds.py`'s tests use a temp `HOME` and monkeypatched
env vars instead. `oauth.py`'s tests assert exact signature strings against
precomputed vectors (fixed timestamp + nonce) — that module is pure and has
zero excuse for a flaky test.

For anything not practically unit-testable — the real `login` xAuth exchange,
a real `export` against your own account — verify manually:

```sh
instapaper --help
instapaper auth                 # both API layers, credentials + network path
instapaper add "https://example.com"
instapaper export --dry-run     # safe: no writes, no state mutation
```

If you touch `cli.py`'s command wiring, run the full suite plus at least one
manual `add` and one manual `auth` — the tests exercise the handlers directly
and won't catch an argparse wiring mistake (wrong dest, missing
`set_defaults`, etc.) the way an actual invocation will.
