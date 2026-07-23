import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import render


class TestNoteFilename(unittest.TestCase):
    def test_basic_slugify(self):
        bookmark = {"title": "Hello World", "bookmark_id": 123}
        self.assertEqual(render.note_filename(bookmark), "Hello World (123).md")

    def test_strips_bad_chars(self):
        bookmark = {
            "title": 'A/B\\C:D#E^F[G]H|I?J"K',
            "bookmark_id": 42,
        }
        self.assertEqual(render.note_filename(bookmark), "ABCDEFGHIJK (42).md")

    def test_strips_control_chars_and_collapses_whitespace(self):
        bookmark = {"title": "Hello\tWorld\n\n  Foo", "bookmark_id": 7}
        self.assertEqual(render.note_filename(bookmark), "Hello World Foo (7).md")

    def test_truncates_to_80_chars(self):
        long_title = "x" * 200
        bookmark = {"title": long_title, "bookmark_id": 99}
        result = render.note_filename(bookmark)
        # strip the " (99).md" suffix to check the title portion length
        title_part = result[: -len(" (99).md")]
        self.assertLessEqual(len(title_part), 80)
        self.assertTrue(result.endswith(" (99).md"))

    def test_empty_title_is_untitled(self):
        bookmark = {"title": "", "bookmark_id": 5}
        self.assertEqual(render.note_filename(bookmark), "untitled (5).md")

    def test_missing_title_is_untitled(self):
        bookmark = {"bookmark_id": 6}
        self.assertEqual(render.note_filename(bookmark), "untitled (6).md")

    def test_whitespace_only_title_is_untitled(self):
        bookmark = {"title": "   \t  ", "bookmark_id": 8}
        self.assertEqual(render.note_filename(bookmark), "untitled (8).md")


class TestRenderNote(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 23, 10, 12, 0, tzinfo=timezone.utc)
        self.bookmark = {
            "type": "bookmark",
            "bookmark_id": 123456,
            "url": "https://example.com/article",
            "title": 'A "Great" Article',
            "description": "",
            "time": 1720000000,
            "starred": "1",
            "private_source": "",
            "hash": "AbCdEf",
            "progress": 0.53,
            "progress_timestamp": 1721000000,
            "tags": [{"id": 1, "name": "ai"}, {"id": 2, "name": "reading"}],
        }
        self.highlights = [
            {
                "type": "highlight",
                "highlight_id": 912,
                "bookmark_id": 123456,
                "text": "second highlight, no note",
                "position": 2,
                "time": 1720000100,
                "note": None,
            },
            {
                "type": "highlight",
                "highlight_id": 911,
                "bookmark_id": 123456,
                "text": "first highlight text",
                "position": 1,
                "time": 1720000050,
                "note": "this is important",
            },
        ]
        self.article_md = "# Heading\n\nSome article body text."

    def test_frontmatter_keys_present(self):
        note = render.render_note(self.bookmark, [], self.article_md, now=self.now)
        self.assertIn("url: https://example.com/article", note)
        self.assertIn('title: "A \\"Great\\" Article"', note)
        self.assertIn("bookmark_id: 123456", note)
        self.assertIn("saved: 2024-07-03", note)
        self.assertIn("progress: 0.53", note)
        self.assertIn("starred: true", note)
        self.assertIn("tags: [ai, reading]", note)
        self.assertIn("synced: 2026-07-23T10:12:00Z", note)

    def test_starred_false_conversion(self):
        bookmark = dict(self.bookmark)
        bookmark["starred"] = "0"
        note = render.render_note(bookmark, [], self.article_md, now=self.now)
        self.assertIn("starred: false", note)

    def test_tags_omitted_when_empty(self):
        bookmark = dict(self.bookmark)
        bookmark["tags"] = []
        note = render.render_note(bookmark, [], self.article_md, now=self.now)
        for line in note.splitlines():
            self.assertFalse(line.startswith("tags:"))

    def test_highlights_omitted_when_empty(self):
        note = render.render_note(self.bookmark, [], self.article_md, now=self.now)
        self.assertNotIn("## Highlights", note)

    def test_highlights_section_present_and_ordered(self):
        note = render.render_note(self.bookmark, self.highlights, self.article_md, now=self.now)
        self.assertIn("## Highlights", note)
        # position 1 (first highlight) should appear before position 2 (second highlight)
        pos1 = note.index("first highlight text")
        pos2 = note.index("second highlight, no note")
        self.assertLess(pos1, pos2)

    def test_quote_callout_blocks(self):
        note = render.render_note(self.bookmark, self.highlights, self.article_md, now=self.now)
        self.assertIn("> [!quote]", note)
        self.assertIn("> first highlight text", note)
        self.assertIn("> second highlight, no note", note)

    def test_note_only_on_highlight_with_note(self):
        note = render.render_note(self.bookmark, self.highlights, self.article_md, now=self.now)
        self.assertIn("**Note:** this is important", note)
        # count occurrences of "**Note:**" -- should be exactly 1
        self.assertEqual(note.count("**Note:**"), 1)

    def test_article_section_present(self):
        note = render.render_note(self.bookmark, [], self.article_md, now=self.now)
        self.assertIn("## Article", note)
        self.assertIn(self.article_md, note)
        # article should come after the Article heading
        heading_pos = note.index("## Article")
        article_pos = note.index(self.article_md)
        self.assertLess(heading_pos, article_pos)

    def test_default_now_used_when_not_provided(self):
        note = render.render_note(self.bookmark, [], self.article_md)
        self.assertIn("synced:", note)
        # should not contain the fixed test timestamp since now wasn't passed
        self.assertNotIn("synced: 2026-07-23T10:12:00Z", note)


if __name__ == "__main__":
    unittest.main()
