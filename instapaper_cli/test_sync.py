import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# Make the package importable when run as `python3 instapaper_cli/test_sync.py`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from instapaper_cli import sync, transport  # noqa: E402


# --- Fixtures ---------------------------------------------------------------

def _bookmark(bid=123456, title="A Great Article", h="hashA", url="https://ex.com/a"):
    return {
        "type": "bookmark",
        "bookmark_id": bid,
        "url": url,
        "title": title,
        "time": 1720000000,
        "starred": "0",
        "hash": h,
        "progress": 0.3,
        "progress_timestamp": 1721000000,
        "tags": [],
    }


def _highlight(hid, bid=123456, text="an insight", position=0, note=None):
    return {
        "type": "highlight",
        "highlight_id": hid,
        "bookmark_id": bid,
        "text": text,
        "position": position,
        "time": 1720500000,
        "note": note,
    }


ARTICLE_HTML = b"<html><body><p>Hello <strong>world</strong>.</p></body></html>"


class FakeApi:
    """Routes transport.api_call by path. No network.

    Configure:
      - list_response: dict returned by /bookmarks/list
      - highlights_by_id: {bookmark_id_str: [highlight dicts]}
      - text_error: if set, /bookmarks/get_text raises this exception
    """

    def __init__(self, list_response, highlights_by_id=None, text_error=None,
                 text_html=ARTICLE_HTML):
        self.list_response = list_response
        self.highlights_by_id = highlights_by_id or {}
        self.text_error = text_error
        self.text_html = text_html
        self.calls = []

    def __call__(self, path, params, creds, *, base=transport.API_BASE, raw=False):
        self.calls.append((path, params, base, raw))
        if path == "/bookmarks/list":
            return self.list_response
        if path == "/bookmarks/get_text":
            if self.text_error is not None:
                raise self.text_error
            return self.text_html
        if path.endswith("/highlights"):
            # path looks like /bookmarks/<id>/highlights
            bid = path.split("/")[2]
            return list(self.highlights_by_id.get(bid, []))
        raise AssertionError("unexpected path: " + path)


_CREDS = object()  # opaque; the fake never inspects it


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.export_dir = self._tmp

    def _run(self, fake, **kw):
        with mock.patch.object(transport, "api_call", fake):
            return sync.sync(self.export_dir, _CREDS, **kw)

    def _md_files(self):
        return [f for f in os.listdir(self.export_dir) if f.endswith(".md")]

    def _read(self, fname):
        with open(os.path.join(self.export_dir, fname)) as f:
            return f.read()

    def _state(self):
        with open(os.path.join(self.export_dir, sync.STATE_FILENAME)) as f:
            return json.load(f)

    # (1) First run against empty state creates a note + state entry.
    def test_first_run_creates(self):
        fake = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911, text="an insight")]},
        )
        res = self._run(fake, folder="archive")

        self.assertEqual(len(res.created), 1)
        self.assertEqual(res.updated, [])
        self.assertEqual(res.unchanged, 0)
        self.assertEqual(res.errors, [])

        files = self._md_files()
        self.assertEqual(len(files), 1)
        content = self._read(files[0])
        self.assertIn("an insight", content)
        self.assertIn("Hello", content)  # article body rendered

        state = self._state()
        entry = state["bookmarks"]["123456"]
        self.assertEqual(entry["hash"], "hashA")
        self.assertEqual(entry["highlight_ids"], [911])
        self.assertEqual(entry["file"], files[0])

    # (2) Second run with nothing dirty → unchanged, no rewrite.
    def test_second_run_unchanged(self):
        fake1 = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911)]},
        )
        self._run(fake1, folder="archive")
        fname = self._md_files()[0]
        mtime_before = os.path.getmtime(os.path.join(self.export_dir, fname))

        # have/highlights suppress the already-known bookmark on the server side.
        fake2 = FakeApi(
            list_response={"bookmarks": [], "highlights": [], "delete_ids": []}
        )
        res = self._run(fake2, folder="archive")

        self.assertEqual(res.created, [])
        self.assertEqual(res.updated, [])
        self.assertEqual(res.unchanged, 1)
        mtime_after = os.path.getmtime(os.path.join(self.export_dir, fname))
        self.assertEqual(mtime_before, mtime_after)  # not rewritten

    # (3) New highlight on a hash-unchanged bookmark → re-render from state meta.
    def test_new_highlight_on_unchanged_bookmark(self):
        fake1 = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911, text="first")]},
        )
        self._run(fake1, folder="archive")

        # bookmarks[] is EMPTY (hash unchanged) but a new highlight references it.
        fake2 = FakeApi(
            list_response={
                "bookmarks": [],
                "highlights": [_highlight(912, text="second")],
                "delete_ids": [],
            },
            highlights_by_id={
                "123456": [
                    _highlight(911, text="first", position=0),
                    _highlight(912, text="second insight", position=1),
                ]
            },
        )
        res = self._run(fake2, folder="archive")

        self.assertEqual(res.created, [])
        self.assertEqual(len(res.updated), 1)

        fname = self._md_files()[0]
        content = self._read(fname)
        self.assertIn("second insight", content)
        # Metadata came from state (title preserved).
        self.assertIn("A Great Article", content)

        state = self._state()
        self.assertEqual(state["bookmarks"]["123456"]["highlight_ids"], [911, 912])

    # (4) delete_ids: without prune keeps file; with prune removes it.
    def test_delete_without_and_with_prune(self):
        fake1 = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911)]},
        )
        self._run(fake1, folder="archive")
        fname = self._md_files()[0]

        # delete without prune
        fake_del = FakeApi(
            list_response={"bookmarks": [], "highlights": [], "delete_ids": ["123456"]}
        )
        res = self._run(fake_del, folder="archive", prune=False)
        self.assertEqual(res.pruned, ["123456"])
        self.assertNotIn("123456", self._state()["bookmarks"])  # state pruned
        self.assertTrue(os.path.exists(os.path.join(self.export_dir, fname)))  # file kept

        # Re-seed and delete WITH prune.
        self._run(fake1, folder="archive")
        self.assertTrue(os.path.exists(os.path.join(self.export_dir, fname)))
        res2 = self._run(fake_del, folder="archive", prune=True)
        self.assertEqual(res2.pruned, ["123456"])
        self.assertFalse(os.path.exists(os.path.join(self.export_dir, fname)))  # file removed

    # (5) get_text 400 → note still written, highlights present, empty article.
    def test_get_text_error_still_renders(self):
        err = transport.ApiError(400, 1220, "domain requires full page HTML")
        fake = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911, text="kept highlight")]},
            text_error=err,
        )
        res = self._run(fake, folder="archive")

        self.assertEqual(len(res.created), 1)
        self.assertEqual(res.errors, [])  # per-bookmark get_text error is absorbed
        files = self._md_files()
        self.assertEqual(len(files), 1)
        content = self._read(files[0])
        self.assertIn("kept highlight", content)
        self.assertIn("## Article", content)  # section present, body empty

    # (6) dry_run → no files, no state file.
    def test_dry_run_writes_nothing(self):
        fake = FakeApi(
            list_response={
                "bookmarks": [_bookmark()],
                "highlights": [_highlight(911)],
                "delete_ids": [],
            },
            highlights_by_id={"123456": [_highlight(911)]},
        )
        res = self._run(fake, folder="archive", dry_run=True)

        self.assertEqual(len(res.created), 1)  # intent still reported
        self.assertEqual(self._md_files(), [])
        self.assertFalse(
            os.path.exists(os.path.join(self.export_dir, sync.STATE_FILENAME))
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
