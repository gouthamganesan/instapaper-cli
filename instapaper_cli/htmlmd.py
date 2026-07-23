from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# Deliberately-lossy HTML -> Markdown converter. Stdlib only.
# Not a pandoc: unhandled block structures (tables, etc.) pass through as raw
# HTML, which Obsidian renders. Public entrypoint: html_to_markdown().

_VOID = {"br", "hr", "img"}
_DROP = {"script", "style", "noscript"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCKS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
    "ul", "ol", "li", "hr", "figure", "figcaption", "div", "picture",
}
# Inline tags we translate; everything else inline just passes text through.
_INLINE = {"a", "em", "i", "strong", "b", "code", "span", "br", "img"}
_WS = re.compile(r"[ \t\r\n]+")


class _MdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []      # finished top-level blocks
        self.buf: list[str] = []         # current inline run for the open block
        self.list_stack: list[dict] = [] # {"ordered":bool, "n":int}
        self.bq_depth = 0                # blockquote nesting
        self.pre_depth = 0               # inside <pre>
        self.code_depth = 0             # inside inline <code> (not pre)
        self.drop_depth = 0             # inside script/style/noscript
        self.a_href: list[str] = []      # stack of open <a> hrefs
        self.a_start: list[int] = []     # buffer length at <a> open
        self._raw_passthrough = 0        # depth of unhandled block emitted raw

    # ---- helpers -------------------------------------------------------
    def _flush_block(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        if self.pre_depth:
            return
        text = text.strip("\n")
        if text.strip() == "":
            return
        if self.bq_depth:
            prefix = "> " * self.bq_depth
            text = "\n".join(prefix + ln for ln in text.split("\n"))
        self.blocks.append(text)

    def _emit(self, s: str) -> None:
        self.buf.append(s)

    def _list_prefix(self) -> str:
        # indentation for the current list item marker
        depth = len(self.list_stack) - 1
        indent = "  " * depth
        lst = self.list_stack[-1]
        if lst["ordered"]:
            marker = f"{lst['n']}. "
        else:
            marker = "- "
        return indent + marker

    # ---- tag handling --------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if self.drop_depth or self._raw_passthrough:
            if tag in _DROP:
                self.drop_depth += 1
            if self._raw_passthrough and tag not in _VOID:
                self._raw_passthrough += 1
            self._emit(self.get_starttag_text() or "")
            return
        if tag in _DROP:
            self.drop_depth += 1
            return

        ad = dict(attrs)
        if tag in _HEADINGS:
            self._flush_block()
            self._emit("#" * _HEADINGS[tag] + " ")
        elif tag == "p":
            self._flush_block()
        elif tag == "div":
            self._flush_block()
        elif tag == "br":
            if self.pre_depth:
                self._emit("\n")
            else:
                self._emit("  \n")
        elif tag == "hr":
            self._flush_block()
            self.blocks.append("---")
        elif tag == "a":
            self.a_href.append(ad.get("href", ""))
            self.a_start.append(len(self.buf))
            self._emit("")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag == "code":
            if not self.pre_depth:
                self.code_depth += 1
                self._emit("`")
        elif tag == "pre":
            self._flush_block()
            self.pre_depth += 1
            self.buf = []
        elif tag == "blockquote":
            self._flush_block()
            self.bq_depth += 1
        elif tag in ("ul", "ol"):
            if not self.list_stack:
                self._flush_block()
            self.list_stack.append({"ordered": tag == "ol", "n": 1})
        elif tag == "li":
            # start a fresh inline run for this item
            if self.buf and "".join(self.buf).strip():
                self._flush_list_item()
            self.buf = []
            if self.list_stack:
                self._emit(self._list_prefix())
        elif tag == "img":
            alt = ad.get("alt", "")
            src = ad.get("src", "")
            self._emit(f"![{alt}]({src})")
        elif tag in ("figure", "figcaption", "picture"):
            # transparent wrappers; let inner content (e.g. <img>) render
            self._flush_block()
        elif tag == "source":
            pass  # <source> carries srcset we don't parse; drop it, keep <img>
        elif tag == "span":
            pass  # transparent
        else:
            # Unhandled tag. If block-level, emit raw HTML passthrough so
            # structures like <table> survive. Inline unknowns: drop the tag,
            # keep text.
            if tag not in _INLINE:
                self._flush_block()
                self._raw_passthrough = 1
                self._emit(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        # self-closing, e.g. <img/>, <br/>
        if self.drop_depth:
            return
        if self._raw_passthrough:
            self._emit(self.get_starttag_text() or "")
            return
        if tag == "source":
            return  # drop self-closing <source/>, keep the <img> fallback
        if tag in ("img", "br", "hr"):
            self.handle_starttag(tag, attrs)
        elif tag not in _INLINE and tag not in _BLOCKS:
            self._emit(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if self.drop_depth:
            if tag in _DROP:
                self.drop_depth -= 1
            return
        if self._raw_passthrough:
            self._emit(f"</{tag}>")
            self._raw_passthrough -= 1
            if self._raw_passthrough == 0:
                # finished the raw block
                self.blocks.append("".join(self.buf).strip())
                self.buf = []
            return

        if tag in _HEADINGS or tag == "p" or tag == "div":
            self._flush_block()
        elif tag == "a":
            if self.a_href:
                href = self.a_href.pop()
                start = self.a_start.pop()
                text = "".join(self.buf[start:])
                del self.buf[start:]
                text = text.strip()
                if href:
                    self._emit(f"[{text}]({href})")
                else:
                    self._emit(text)
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag == "code":
            if not self.pre_depth and self.code_depth:
                self.code_depth -= 1
                self._emit("`")
        elif tag == "pre":
            code = "".join(self.buf)
            self.buf = []
            self.pre_depth -= 1
            code = code.strip("\n")
            self.blocks.append("```\n" + code + "\n```")
        elif tag == "blockquote":
            self._flush_block()
            if self.bq_depth:
                self.bq_depth -= 1
        elif tag in ("ul", "ol"):
            self._flush_list_item()
            if self.list_stack:
                self.list_stack.pop()
        elif tag == "li":
            self._flush_list_item()
        elif tag in ("figure", "figcaption"):
            self._flush_block()

    def _flush_list_item(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        if text.strip() == "":
            return
        self.blocks.append(text.rstrip())
        if self.list_stack and self.list_stack[-1]["ordered"]:
            self.list_stack[-1]["n"] += 1

    def handle_data(self, data):
        if self.drop_depth:
            return
        if self._raw_passthrough:
            self._emit(data)
            return
        if self.pre_depth:
            self._emit(data)
            return
        # collapse whitespace outside pre
        text = _WS.sub(" ", data)
        if text == "":
            return
        self._emit(text)

    # ---- finalize ------------------------------------------------------
    def result(self) -> str:
        self._flush_list_item()
        self._flush_block()
        # apply blockquote prefix retroactively is handled at emit-time via
        # depth; here we just join blocks with a single blank line.
        cleaned = []
        for b in self.blocks:
            b = b.strip("\n")
            if b == "":
                continue
            cleaned.append(b)
        return "\n\n".join(cleaned)


def html_to_markdown(html: str) -> str:
    """Convert an HTML fragment/document to lossy Markdown (stdlib only)."""
    if not html:
        return ""
    p = _MdParser()
    p.feed(html)
    p.close()
    out = p.result()
    # Unescape entities in the final text (also covers code/pre — Instapaper's
    # get_text HTML uses entities and we want the raw characters there).
    out = unescape(out)
    # Normalize excessive blank lines to exactly one between blocks.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n" if out.strip() else ""
