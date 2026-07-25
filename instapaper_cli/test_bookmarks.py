"""test_bookmarks.py — unit tests for the bookmarks mutation module.

transport.api_call is mocked; no real HTTP. Runnable via
`python3 instapaper_cli/test_bookmarks.py` or unittest discovery.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instapaper_cli import bookmarks  # noqa: E402


class TestBookmarkVerbs(unittest.TestCase):
    def test_archive_posts_bookmark_id_as_string(self):
        with mock.patch.object(bookmarks.transport, "api_call", return_value=[]) as api:
            bookmarks.archive(object(), 12345)
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/bookmarks/archive")
        self.assertEqual(params, {"bookmark_id": "12345"})

    def test_unarchive_hits_unarchive_endpoint(self):
        with mock.patch.object(bookmarks.transport, "api_call", return_value=[]) as api:
            bookmarks.unarchive(object(), "678")
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/bookmarks/unarchive")
        self.assertEqual(params, {"bookmark_id": "678"})

    def test_delete_hits_delete_endpoint(self):
        with mock.patch.object(bookmarks.transport, "api_call", return_value=[]) as api:
            bookmarks.delete(object(), 999)
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/bookmarks/delete")
        self.assertEqual(params, {"bookmark_id": "999"})

    def test_star_and_unstar_hit_their_endpoints(self):
        with mock.patch.object(bookmarks.transport, "api_call", return_value=[]) as api:
            bookmarks.star(object(), 1)
            bookmarks.unstar(object(), 2)
        self.assertEqual(api.call_args_list[0][0][0], "/bookmarks/star")
        self.assertEqual(api.call_args_list[1][0][0], "/bookmarks/unstar")

    def test_move_posts_bookmark_and_folder_ids(self):
        with mock.patch.object(bookmarks.transport, "api_call", return_value=[]) as api:
            bookmarks.move(object(), 111, 222)
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/bookmarks/move")
        self.assertEqual(params, {"bookmark_id": "111", "folder_id": "222"})


class TestListBookmarks(unittest.TestCase):
    BM = {"type": "bookmark", "bookmark_id": 1, "title": "A"}

    def test_handles_array_response_shape(self):
        resp = [{"type": "meta"}, {"type": "user"}, self.BM]
        with mock.patch.object(bookmarks.transport, "api_call", return_value=resp) as api:
            out = bookmarks.list_bookmarks(object(), "unread", 50)
        self.assertEqual(out, [self.BM])
        _p, params, _oc = api.call_args[0]
        self.assertEqual(params, {"folder_id": "unread", "limit": "50"})

    def test_handles_object_response_shape(self):
        resp = {"type": "meta", "user": {}, "bookmarks": [self.BM]}
        with mock.patch.object(bookmarks.transport, "api_call", return_value=resp):
            out = bookmarks.list_bookmarks(object(), "123")
        self.assertEqual(out, [self.BM])


if __name__ == "__main__":
    unittest.main()
