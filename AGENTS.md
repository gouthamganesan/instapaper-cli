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

**Treat exported files as generated output, not something to hand-edit.** A
re-export overwrites the file for any bookmark whose hash or highlights
changed. If the user wants notes alongside an exported article, that's a
separate file, not an edit to the exported `.md`.

### `tools/inbox.py` — triage the unread queue

The two things `instapaper` deliberately doesn't do: list bookmarks with their
saved timestamps, and archive an existing one.

```sh
python3 tools/inbox.py list --json                           # read-only, always safe
python3 tools/inbox.py archive --before YYYY-MM-DD           # DRY RUN by default
python3 tools/inbox.py archive --before YYYY-MM-DD --apply   # actually archives
```

Trigger `list` on "what's in my inbox", "how old are these", "what's clogging
my queue". It's the cheap answer — reach for it rather than `export` when the
user wants to *see* the queue, since `export` downloads every article's full
text to answer a question about dates.

Full API only, so `login` must have run. It imports `instapaper_cli.creds` and
`instapaper_cli.transport` directly; don't reimplement the signer, and don't
patch `sys.path` by hand — it resolves the package from its own `__file__`.

**Never run `archive --apply` without showing the dry run first**, and never
run it on a queue the user hasn't seen. Archiving moves a bookmark to the
Archive folder and is reversible, but it's still the only operation in this
repo that changes server state, so it gets the ceremony.

## What NOT to do

**Do not** attempt to delete a bookmark. Nothing here can, and no amount of
flag-guessing will find a way. `add`, `export`, and `tools/inbox.py`'s archive
are the whole mutating surface, and archive only *moves*.

**Do not** try to archive from `instapaper` itself. It isn't there by design —
`tools/inbox.py` is where that lives, precisely so the CLI's "only ever adds
and reads" contract stays literally true.

**Do not** resolve a folder *name* to an id yourself by guessing or calling
some other endpoint. `--folder` only accepts a numeric id or the literals
`unread` / `starred` / `archive`. If the user names a folder like "Reading
Later", ask them for its numeric id or use one of the literals — don't
fabricate an id.

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
"at least one failed," not "nothing worked."

Re-sending a URL the user already saved via plain `add` is harmless —
Instapaper bumps rather than duplicates — so there's no need to track what
you've sent. Two things still worth knowing, from the README: dedup is
URL-string exact (tracking-parameter variants can double), and a
Freedium-wrapped URL dedupes against its wrapped form, not the canonical
article URL — toggling freedium on/off for the same article can produce two
bookmarks.

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
| `instapaper_cli/htmlmd.py` | Deliberately-lossy HTML → Markdown converter (stdlib `html.parser`) used to render exported article text. |
| `instapaper_cli/render.py` | Turns a bookmark dict + highlight list + article Markdown into one note file's exact text (frontmatter, `[!quote]` callouts, body). |
| `instapaper_cli/sync.py` | The incremental `export` engine — diffs local state against the server via `have=id:hash` and a highlight-id delta, fetches only what's dirty. |
| `tools/inbox.py` | Standalone triage script, **not** part of the CLI's command tree. Lists bookmarks with saved timestamps and archives existing ones. Imports `creds` + `transport`; the only thing in the repo that mutates server state, and its one mutation moves rather than deletes. Lives outside the package on purpose. |

The Simple API contract this depends on is documented at
<https://www.instapaper.com/developers/v1/simple-api>; the Full API at
<https://www.instapaper.com/developers/v1/full-api>. Read the relevant one
before changing request construction. Deeper internals-level notes (the OAuth
signing math, the sync algorithm's dirty-set logic, the freedium/routing
decisions) live in [`docs/`](docs/) if you want the "why," not just the "what."

## Testing

84 unit tests across the library modules, stdlib `unittest`, no pytest
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
