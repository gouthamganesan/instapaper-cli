# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Copilot, etc.) using or
modifying this repo. Humans want [README.md](README.md).

## Using the tool

The invocation an agent almost always wants:

```sh
instapaper add "<url>"
```

Trigger on user phrasings like "save this to Instapaper", "read this later",
"add these to my inbox". Batch multiple URLs into one call rather than looping:

```sh
instapaper add "https://a.com" "https://b.com" "https://c.com"
```

**Do not** attempt to list, read, search, or delete bookmarks. The Simple API
is add-only; there is no endpoint for those and no amount of flag-guessing will
find one.

**Do not** ask the user to paste their password, and do not write it into a
file or a command. Credential setup is a one-time manual step the user performs
themselves via `security add-generic-password -s instapaper -a <user> -w`. If
`instapaper auth` fails with a credential error, surface the setup instructions
rather than trying to work around them.

**Check the exit code**, don't parse stdout. `0` = all saved, `1` = something
failed. Per-URL errors go to stderr and do not stop the remaining URLs, so a
partial batch can fail — exit code `1` means "at least one failed", not "nothing
saved".

Re-sending a URL the user already saved is harmless — Instapaper bumps rather
than duplicates — so there's no need to track what you've sent. But see the
caveat in the README: dedup is URL-string exact, so tracking-parameter variants
of the same article can still produce two entries.

## Modifying the code

The whole tool is one file, [`instapaper`](instapaper), around 180 lines. Keep
it that way. Specifically:

- **Stdlib only.** No `requests`, no `click`. The zero-dependency property is
  the point — it makes the tool droppable into any environment without a
  virtualenv. A PR adding a dependency needs to justify itself against that.
- **Never accept a password as a CLI argument.** This is the specific design
  flaw the tool exists to avoid. Command-line arguments are visible in `ps`
  output and shell history.
- **An empty password is valid, not missing.** Many Instapaper accounts have
  none. Note the `is not None` check in `get_password` — a truthiness check
  there would be a bug.
- **Password resolution order** is env var, then Keychain. The env var comes
  first so non-macOS environments and CI work without a Keychain.

## Structure

| Function | Role |
|---|---|
| `get_username` / `get_password` | Credential resolution. The only place secrets are touched. |
| `call` | The single HTTP POST. Returns `(status, headers)`; never raises past `die`. |
| `cmd_add` / `cmd_auth` | Subcommand handlers. Return an exit code. |
| `STATUS_MESSAGES` | Maps Instapaper's `400` / `403` / `500` to human text. |

The API contract this depends on is documented at
<https://www.instapaper.com/developers/v1/simple-api>. Read it before changing
request construction.

## Testing

There is no automated test suite, and this is a known gap rather than a
decision. Testing means real API calls against a real account, so verification
has been manual:

```sh
instapaper --help          # arg parsing
instapaper auth            # credentials + network path
instapaper add "https://example.com"   # the real thing
```

If you add tests, mock at the `call` boundary — it is the only function that
touches the network, and it deliberately returns a plain `(status, headers)`
tuple to make that substitution easy.
