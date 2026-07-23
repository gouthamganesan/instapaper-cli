from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import htmlmd  # noqa: E402


class HtmlMdCases(unittest.TestCase):
    """Table-driven: each case asserts a set of substrings are present.

    'ordered' is a list of substrings that must appear in that relative order.
    """

    CASES = [
        (
            "heading",
            "<h2>Hello World</h2>",
            ["## Hello World"],
            [],
        ),
        (
            "para_link_bold_italic",
            '<p>See <a href="http://example.com">the text</a> which is '
            "<strong>bold</strong> and <em>italic</em>.</p>",
            ["[the text](http://example.com)", "**bold**", "*italic*"],
            ["[the text](http://example.com)", "**bold**", "*italic*"],
        ),
        (
            "nested_ul",
            "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>",
            ["- one", "- two", "  - nested"],
            ["- one", "- two", "  - nested"],
        ),
        (
            "ordered_list",
            "<ol><li>first</li><li>second</li></ol>",
            ["1. first", "2. second"],
            ["1. first", "2. second"],
        ),
        (
            "blockquote",
            "<blockquote><p>a wise quote</p></blockquote>",
            ["> a wise quote"],
            [],
        ),
        (
            "inline_code",
            "<p>Use <code>print()</code> now.</p>",
            ["`print()`"],
            [],
        ),
        (
            "fenced_pre",
            "<pre><code>def f():\n    return 1</code></pre>",
            ["```", "def f():", "return 1"],
            ["```", "def f():"],
        ),
        (
            "image",
            '<p><img src="http://img/x.png" alt="a cat"></p>',
            ["![a cat](http://img/x.png)"],
            [],
        ),
        (
            "hr",
            "<p>above</p><hr><p>below</p>",
            ["above", "---", "below"],
            ["above", "---", "below"],
        ),
        (
            "entities",
            "<p>Tom &amp; Jerry &lt;3</p>",
            ["Tom & Jerry <3"],
            [],
        ),
    ]

    def test_cases(self):
        for name, html, present, ordered in self.CASES:
            with self.subTest(case=name):
                out = htmlmd.html_to_markdown(html)
                for token in present:
                    self.assertIn(token, out, f"{name}: missing {token!r} in\n{out}")
                # verify relative order
                last = -1
                for token in ordered:
                    idx = out.find(token)
                    self.assertGreater(
                        idx, last, f"{name}: {token!r} out of order in\n{out}"
                    )
                    last = idx

    def test_table_survives_as_raw_html(self):
        html = "<table><tr><td>cell1</td><td>cell2</td></tr></table>"
        out = htmlmd.html_to_markdown(html)
        # Non-crashing + content preserved. Raw HTML passthrough is acceptable
        # (Obsidian renders it), so assert the table markup or its cell text.
        self.assertIn("cell1", out)
        self.assertIn("cell2", out)
        self.assertIn("<table>", out)

    def test_scripts_dropped(self):
        html = "<p>keep</p><script>var x = 1;</script><style>.a{}</style>"
        out = htmlmd.html_to_markdown(html)
        self.assertIn("keep", out)
        self.assertNotIn("var x", out)
        self.assertNotIn(".a{}", out)

    def test_empty(self):
        self.assertEqual(htmlmd.html_to_markdown(""), "")
        self.assertEqual(htmlmd.html_to_markdown("   "), "")

    def test_blocks_separated_by_blank_line(self):
        out = htmlmd.html_to_markdown("<h1>Title</h1><p>Body text here.</p>")
        self.assertIn("# Title\n\n", out)
        self.assertNotIn("\n\n\n", out)


if __name__ == "__main__":
    unittest.main()
