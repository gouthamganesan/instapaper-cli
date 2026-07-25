"""test_folders.py — unit tests for the folders library module.

transport.api_call is mocked throughout; no real HTTP. Runnable via
`python3 instapaper_cli/test_folders.py` or unittest discovery.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instapaper_cli import folders, transport  # noqa: E402

WORK = {"type": "folder", "folder_id": 111, "title": "Work", "count": 3}
READING = {"type": "folder", "folder_id": 222, "title": "Reading List", "count": 0}


class TestListAndFind(unittest.TestCase):
    def test_list_folders_keeps_only_folder_objects(self):
        resp = [{"type": "meta"}, WORK, READING, {"nope": 1}]
        with mock.patch.object(folders.transport, "api_call", return_value=resp) as api:
            out = folders.list_folders(object())
        self.assertEqual(out, [WORK, READING])
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/folders/list")
        self.assertEqual(params, {})

    def test_find_by_name_is_case_and_space_insensitive(self):
        self.assertIs(folders.find_by_name([WORK, READING], "  reading list "), READING)
        self.assertIs(folders.find_by_name([WORK, READING], "WORK"), WORK)
        self.assertIsNone(folders.find_by_name([WORK, READING], "missing"))


class TestAddFolder(unittest.TestCase):
    def test_add_folder_returns_created_object(self):
        created = {"type": "folder", "folder_id": 999, "title": "New"}
        with mock.patch.object(folders.transport, "api_call", return_value=[created]) as api:
            out = folders.add_folder(object(), "New")
        self.assertEqual(out, created)
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/folders/add")
        self.assertEqual(params, {"title": "New"})

    def test_add_folder_raises_when_no_object_returned(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[{"type": "meta"}]):
            with self.assertRaises(transport.ApiError):
                folders.add_folder(object(), "New")


class TestGetOrCreate(unittest.TestCase):
    def test_existing_folder_is_not_recreated(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK, READING]) as api:
            folder, created = folders.get_or_create(object(), "work")
        self.assertFalse(created)
        self.assertEqual(folder, WORK)
        api.assert_called_once()  # only folders/list, never folders/add

    def test_missing_folder_is_created(self):
        new = {"type": "folder", "folder_id": 333, "title": "Fresh"}
        with mock.patch.object(folders.transport, "api_call",
                               side_effect=[[WORK, READING], [new]]) as api:
            folder, created = folders.get_or_create(object(), "Fresh")
        self.assertTrue(created)
        self.assertEqual(folder, new)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[1][0][0], "/folders/add")


class TestResolve(unittest.TestCase):
    def test_none_and_empty_resolve_to_none(self):
        self.assertIsNone(folders.resolve(object(), None))
        self.assertIsNone(folders.resolve(object(), "   "))

    def test_numeric_and_special_pass_through_without_network(self):
        with mock.patch.object(folders.transport, "api_call") as api:
            self.assertEqual(folders.resolve(object(), "123"), "123")
            self.assertEqual(folders.resolve(object(), "archive"), "archive")
        api.assert_not_called()

    def test_name_hit_returns_id_as_string(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK, READING]):
            self.assertEqual(folders.resolve(object(), "Reading List"), "222")

    def test_name_miss_without_create_raises_folder_error(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK, READING]):
            with self.assertRaises(folders.FolderError) as ctx:
                folders.resolve(object(), "Nope")
        # error should name existing folders and point at the fix
        self.assertIn("Work", str(ctx.exception))
        self.assertIn("--create-folder", str(ctx.exception))

    def test_name_miss_with_create_creates_and_returns_new_id(self):
        new = {"type": "folder", "folder_id": 444, "title": "Nope"}
        with mock.patch.object(folders.transport, "api_call",
                               side_effect=[[WORK, READING], [new]]) as api:
            self.assertEqual(folders.resolve(object(), "Nope", create=True), "444")
        self.assertEqual(api.call_args_list[1][0][0], "/folders/add")


class TestDeleteFolder(unittest.TestCase):
    def test_delete_folder_posts_folder_id_as_string(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[]) as api:
            folders.delete_folder(object(), 5391419)
        path, params, _oc = api.call_args[0]
        self.assertEqual(path, "/folders/delete")
        self.assertEqual(params, {"folder_id": "5391419"})


class TestResolveExisting(unittest.TestCase):
    def test_resolves_name_to_folder_object(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK, READING]):
            self.assertIs(folders.resolve_existing(object(), "reading list"), READING)

    def test_resolves_numeric_id_to_folder_object(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK, READING]):
            self.assertIs(folders.resolve_existing(object(), "111"), WORK)

    def test_special_literal_is_rejected(self):
        with mock.patch.object(folders.transport, "api_call") as api:
            with self.assertRaises(folders.FolderError):
                folders.resolve_existing(object(), "archive")
        api.assert_not_called()

    def test_unknown_name_raises_with_listing(self):
        with mock.patch.object(folders.transport, "api_call", return_value=[WORK]):
            with self.assertRaises(folders.FolderError) as ctx:
                folders.resolve_existing(object(), "ghost")
        self.assertIn("Work", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
