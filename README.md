# instapaper-cli

Save URLs to your Instapaper inbox from the command line. One file, Python 3,
no dependencies.

```sh
instapaper add "https://example.com/article"
saved: The Article's Real Title
```

Built to be driven by a coding agent (Claude Code, Cursor, etc.) as much as by a
human — so it takes no interactive input, never puts a password on the command
line, and reports failure through exit codes. See [AGENTS.md](AGENTS.md) if
you're wiring it into an agent.

## Why this exists

Instapaper's [Simple API](https://www.instapaper.com/developers/v1/simple-api)
is a single endpoint. Existing wrappers are heavier than the thing they wrap —
the best-known one, [dakrone/ricepaper](https://github.com/dakrone/ricepaper),
is an unmaintained 2010-era Ruby gem that takes your password as a `-p` flag.
This is roughly the same feature set in a single stdlib Python file, with
credentials pulled from the OS keychain instead.

## Install

Requires Python 3.8+. macOS for keychain-backed credentials; anything else works
via an environment variable.

```sh
git clone https://github.com/gouthamganesan/instapaper-cli.git
cd instapaper-cli
chmod +x instapaper
ln -s "$PWD/instapaper" /usr/local/bin/instapaper   # or anywhere on your PATH
```

## Configure

**Username** — your Instapaper login. Instapaper usernames are usually, but not
always, an email address.

```sh
mkdir -p ~/.config/instapaper
echo -n 'you@example.com' > ~/.config/instapaper/username
```

Or set `INSTAPAPER_USERNAME`, which takes precedence.

**Password** — resolved in this order:

1. `INSTAPAPER_PASSWORD` environment variable, if set (including to an empty
   string). Use this on Linux, in CI, or in containers.
2. macOS Keychain, service `instapaper`, account matching your username.

```sh
security add-generic-password -s instapaper -a "you@example.com" -w
```

The `-w` flag prompts interactively, so the password never enters your shell
history. **Many Instapaper accounts have no password at all** — if yours is one,
store an empty string. The tool treats that as valid, not as missing.

Verify:

```sh
instapaper auth
ok: authenticated as you@example.com
```

## Usage

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

# Check credentials without saving anything
instapaper auth
```

`--title` and `--selection` apply to a single URL. Passing them alongside
multiple URLs is rejected rather than silently applied to all of them.

**Exit codes:** `0` if every URL saved, `1` if any failed or if configuration is
missing. Per-URL failures print to stderr and do not abort the remaining URLs.

On success the tool prints the title Instapaper resolved server-side, taken from
the `X-Instapaper-Title` response header — which is a useful signal that the
article actually parsed, not just that the URL was accepted.

## What this cannot do

The Simple API is **add-only**. There is no listing, reading, deleting,
archiving, or folder support, and everything lands in the inbox. That is a limit
of the API, not an unimplemented feature.

Doing more requires Instapaper's
[Full API](https://www.instapaper.com/developers/v1/full-api), which uses
OAuth/xAuth and needs an API key you request from Instapaper by email. This tool
does not implement it.

## Behaviour worth knowing

**Re-adding a URL does not duplicate it.** Instapaper's documentation states
that adding an existing URL marks it unread and moves it to the top of the list,
still returning `201`. This makes repeat calls safe and is why the tool keeps no
local cache of what it has already sent.

Two caveats on that, stated plainly because they are easy to over-trust:

- The dedup happens server-side on the URL string. `https://x.com/post` and
  `https://x.com/post?utm_source=rss` are different strings and will plausibly
  produce two entries. If you feed it links harvested from mixed sources,
  normalise them first or accept occasional near-duplicates.
- The behaviour is documented by Instapaper but not verified by a test in this
  repo. Treat it as their contract, not as something this code enforces.

**Pages behind a login or a hard paywall** will save, but Instapaper's
server-side parser may extract little or nothing. The `201` reflects acceptance
of the URL, not a successful article extraction.

## Security notes

- The password is read at call time and held only in memory. It is never
  written to disk by this tool, never passed as a command-line argument (where
  it would be visible in `ps` and shell history), and never logged.
- Credentials are transmitted to Instapaper over HTTPS as form parameters. This
  is what the Simple API requires; it has no token-based alternative. If that
  tradeoff bothers you, the Full API's OAuth flow is the alternative.
- `~/.config/instapaper/username` holds only the username, never the password.

## Licence

MIT — see [LICENSE](LICENSE).
