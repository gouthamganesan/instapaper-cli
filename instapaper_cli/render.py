from __future__ import annotations

import re
from datetime import datetime, timezone

# Characters that are hostile to Obsidian filenames / paths.
_BAD_CHARS_RE = re.compile(r'[\/\\:#\^\[\]|?"]')
# Non-whitespace control characters (0x00-0x1F minus whitespace, and 0x7F).
# Whitespace control chars (tab, newline, CR, etc.) are handled by whitespace
# collapsing below rather than being stripped outright.
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0e-\x1f\x7f]')
_WHITESPACE_RE = re.compile(r'\s+')

MAX_TITLE_LEN = 80


def _slugify_title(title: str) -> str:
    if not title:
        return ""
    title = _BAD_CHARS_RE.sub("", title)
    title = _CONTROL_CHARS_RE.sub("", title)
    title = _WHITESPACE_RE.sub(" ", title)
    title = title.strip()
    if len(title) > MAX_TITLE_LEN:
        title = title[:MAX_TITLE_LEN].strip()
    return title


def note_filename(bookmark: dict) -> str:
    title = _slugify_title(bookmark.get("title") or "")
    if not title:
        title = "untitled"
    return f"{title} ({bookmark['bookmark_id']}).md"


def _escape_yaml_string(value: str) -> str:
    return value.replace('"', '\\"')


def _iso_date_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _iso8601_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote_block(text: str) -> str:
    lines = text.splitlines() if text else [""]
    return "\n".join(f"> {line}" for line in lines)


def render_note(bookmark: dict, highlights: list, article_md: str, now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)

    title = bookmark.get("title") or ""
    escaped_title = _escape_yaml_string(title)
    saved = _iso_date_from_ts(bookmark["time"])
    starred = bool(int(bookmark.get("starred", "0")))
    tags = [t["name"] for t in (bookmark.get("tags") or [])]
    synced = _iso8601_utc(now)

    frontmatter_lines = [
        "---",
        f"url: {bookmark.get('url', '')}",
        f'title: "{escaped_title}"',
        f"bookmark_id: {bookmark['bookmark_id']}",
        f"saved: {saved}",
        f"progress: {bookmark.get('progress', 0)}",
        f"starred: {'true' if starred else 'false'}",
    ]
    if tags:
        frontmatter_lines.append(f"tags: [{', '.join(tags)}]")
    frontmatter_lines.append(f"synced: {synced}")
    frontmatter_lines.append("---")

    parts = ["\n".join(frontmatter_lines), ""]

    if highlights:
        ordered = sorted(highlights, key=lambda h: h["position"])
        parts.append("## Highlights")
        parts.append("")
        blocks = []
        for h in ordered:
            block_lines = ["> [!quote]"]
            block_lines.append(_quote_block(h.get("text", "")))
            note = h.get("note")
            if note:
                block_lines.append(">")
                block_lines.append(f"> **Note:** {note}")
            blocks.append("\n".join(block_lines))
        parts.append("\n\n".join(blocks))
        parts.append("")

    parts.append("## Article")
    parts.append("")
    parts.append(article_md)

    return "\n".join(parts)
