"""test_cli.py — argparse wiring and routing tests for cli.py.

Every network and credential seam is mocked; no real HTTP, Keychain, or config
files are touched (except a per-test temp config path). Runnable via
`python3 instapaper_cli/test_cli.py` or pytest.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instapaper_cli import cli, config, transport  # noqa: E402


def run_main(argv):
    """Invoke cli.main capturing (rc, stdout, stderr). SystemExit → rc from code."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


class TestAddSimpleRouting(unittest.TestCase):
    def test_add_routes_to_simple_call_and_prints_saved(self):
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "simple_creds", return_value={"username": "u", "password": "p"}), \
             mock.patch.object(cli.transport, "simple_call",
                               return_value=(201, {"X-Instapaper-Title": "Hello World"})) as sc, \
             mock.patch.object(cli.transport, "api_call") as api:
            rc, out, err = run_main(["add", "https://example.com/a"])

        self.assertEqual(rc, 0)
        self.assertIn("saved: Hello World", out)
        sc.assert_called_once()
        endpoint, params = sc.call_args[0]
        self.assertEqual(endpoint, transport.SIMPLE_ADD)
        self.assertEqual(params["url"], "https://example.com/a")
        api.assert_not_called()


class TestAddFullRouting(unittest.TestCase):
    def test_folder_flag_routes_to_api_call(self):
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "simple_call") as sc, \
             mock.patch.object(cli.transport, "api_call",
                               return_value=[{"type": "bookmark", "title": "T"}]) as api:
            rc, out, err = run_main(["add", "https://example.com/a", "--folder", "123"])

        self.assertEqual(rc, 0)
        self.assertIn("saved: T", out)
        sc.assert_not_called()
        api.assert_called_once()
        path, params, _creds = api.call_args[0]
        self.assertEqual(path, "/bookmarks/add")
        self.assertEqual(params["folder_id"], "123")
        self.assertEqual(params["url"], "https://example.com/a")


class TestFreedium(unittest.TestCase):
    def test_wrapping_applied_when_enabled_and_medium(self):
        cfg = config.Config(freedium_enabled=True)  # domains default ["medium.com"]
        with mock.patch.object(cli.config, "load_config", return_value=cfg), \
             mock.patch.object(cli.creds, "simple_creds", return_value={"username": "u", "password": "p"}), \
             mock.patch.object(cli.transport, "simple_call",
                               return_value=(201, {"X-Instapaper-Title": "M"})) as sc:
            rc, out, err = run_main(["add", "https://medium.com/@x/post-123"])

        self.assertEqual(rc, 0)
        _endpoint, params = sc.call_args[0]
        self.assertEqual(params["url"], "https://freedium.cfd/https://medium.com/@x/post-123")

    def test_wrapping_not_applied_when_content_supplied(self):
        cfg = config.Config(freedium_enabled=True)
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<p>hi</p>")
            content_path = f.name
        self.addCleanup(os.unlink, content_path)

        with mock.patch.object(cli.config, "load_config", return_value=cfg), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               return_value=[{"type": "bookmark", "title": "M"}]) as api:
            rc, out, err = run_main(["add", "https://medium.com/@x/post-123", "--content", content_path])

        self.assertEqual(rc, 0)
        _path, params, _creds = api.call_args[0]
        # URL must be the original, un-wrapped one.
        self.assertEqual(params["url"], "https://medium.com/@x/post-123")
        self.assertEqual(params["content"], "<p>hi</p>")


_WORK = {"type": "folder", "folder_id": 111, "title": "Work", "count": 3}
_READING = {"type": "folder", "folder_id": 222, "title": "Reading List", "count": 0}


def _fake_api(folder_list=(), add_result=None, bookmark_title="T", bmarks=None):
    """Build a transport.api_call side_effect that branches on path."""
    def _call(path, params, oc, *a, **kw):
        if path == "/folders/list":
            return list(folder_list)
        if path == "/folders/add":
            return [add_result]
        if path == "/folders/delete":
            return []
        if path == "/bookmarks/add":
            return [{"type": "bookmark", "title": bookmark_title}]
        if path == "/bookmarks/list":
            return list(bmarks or [])
        if path in ("/bookmarks/archive", "/bookmarks/unarchive", "/bookmarks/star",
                    "/bookmarks/unstar", "/bookmarks/move", "/bookmarks/delete"):
            return []
        raise AssertionError("unexpected path {!r}".format(path))
    return _call


# Fixed timestamps so --before filtering is deterministic (no clock reads).
_OLD_BM = {"type": "bookmark", "bookmark_id": 1, "title": "Old", "time": 1700000000,
           "starred": "1", "tags": [{"name": "x"}]}
_NEW_BM = {"type": "bookmark", "bookmark_id": 2, "title": "New", "time": 1790000000}


class TestList(unittest.TestCase):
    def test_human_output_lists_id_title_star_tags(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(bmarks=[_NEW_BM, _OLD_BM])):
            rc, out, err = run_main(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("Old", out)
        self.assertIn("* 1", out)          # starred marker on the starred one
        self.assertIn("#x", out)
        self.assertIn("2 bookmark(s) in unread", err)

    def test_json_includes_saved_time_and_tags(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(bmarks=[_OLD_BM])):
            rc, out, _err = run_main(["list", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data[0]["id"], 1)
        self.assertEqual(data[0]["saved"], "2023-11-14")
        self.assertEqual(data[0]["tags"], ["x"])
        self.assertTrue(data[0]["starred"])

    def test_before_filters_out_newer(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(bmarks=[_OLD_BM, _NEW_BM])):
            rc, out, _err = run_main(["list", "--before", "2025-01-01", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual([b["id"] for b in json.loads(out)], [1])

    def test_folder_name_resolves_before_listing(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(folder_list=[_READING], bmarks=[_OLD_BM])) as api:
            rc, _out, _err = run_main(["list", "--folder", "Reading List", "--json"])
        self.assertEqual(rc, 0)
        list_call = [c for c in api.call_args_list if c[0][0] == "/bookmarks/list"][0]
        self.assertEqual(list_call[0][1]["folder_id"], "222")


class TestStarUnstarUnarchiveMove(unittest.TestCase):
    def test_star_unstar_unarchive_hit_their_endpoints(self):
        for cmd, path in [("star", "/bookmarks/star"),
                          ("unstar", "/bookmarks/unstar"),
                          ("unarchive", "/bookmarks/unarchive")]:
            with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
                 mock.patch.object(cli.transport, "api_call", return_value=[]) as api:
                rc, _out, _err = run_main([cmd, "111"])
            self.assertEqual(rc, 0)
            self.assertEqual(api.call_args[0][0], path)

    def test_move_resolves_folder_and_moves(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(folder_list=[_WORK, _READING])) as api:
            rc, out, _err = run_main(["move", "111", "--folder", "Reading List"])
        self.assertEqual(rc, 0)
        move_call = [c for c in api.call_args_list if c[0][0] == "/bookmarks/move"][0]
        self.assertEqual(move_call[0][1], {"bookmark_id": "111", "folder_id": "222"})

    def test_move_create_folder_creates_then_moves(self):
        new = {"type": "folder", "folder_id": 333, "title": "Later"}
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(folder_list=[_WORK], add_result=new)) as api:
            rc, _out, _err = run_main(["move", "111", "--folder", "Later", "--create-folder"])
        self.assertEqual(rc, 0)
        move_call = [c for c in api.call_args_list if c[0][0] == "/bookmarks/move"][0]
        self.assertEqual(move_call[0][1]["folder_id"], "333")


class TestArchiveBulk(unittest.TestCase):
    def test_no_ids_no_before_dies(self):
        rc, _out, err = run_main(["archive"])
        self.assertEqual(rc, 1)
        self.assertIn("--before", err)

    def test_ids_and_before_together_die(self):
        rc, _out, err = run_main(["archive", "111", "--before", "2025-01-01"])
        self.assertEqual(rc, 1)

    def test_before_dry_run_lists_targets_without_archiving(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(bmarks=[_OLD_BM, _NEW_BM])) as api:
            rc, out, _err = run_main(["archive", "--before", "2025-01-01"])
        self.assertEqual(rc, 0)
        self.assertIn("would archive", out)
        self.assertIn("Old", out)
        self.assertFalse([c for c in api.call_args_list if c[0][0] == "/bookmarks/archive"])

    def test_before_apply_archives_only_older(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api(bmarks=[_OLD_BM, _NEW_BM])) as api:
            rc, out, _err = run_main(["archive", "--before", "2025-01-01", "--apply"])
        self.assertEqual(rc, 0)
        arch = [c for c in api.call_args_list if c[0][0] == "/bookmarks/archive"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0][0][1], {"bookmark_id": "1"})


class TestAddTags(unittest.TestCase):
    def test_tag_flag_serializes_tags_and_routes_to_full_api(self):
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               return_value=[{"type": "bookmark", "title": "T"}]) as api:
            rc, out, _err = run_main(["add", "https://example.com/a", "--tag", "longread", "--tag", "ai"])

        self.assertEqual(rc, 0)
        self.assertIn("saved: T", out)
        _path, params, _creds = api.call_args[0]
        self.assertEqual(json.loads(params["tags"]), [{"name": "longread"}, {"name": "ai"}])


class TestAddFolderByName(unittest.TestCase):
    def test_existing_folder_name_resolves_to_id(self):
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK, _READING])) as api:
            rc, out, _err = run_main(["add", "https://example.com/a", "--folder", "Reading List"])

        self.assertEqual(rc, 0)
        self.assertIn("saved: T", out)
        add_call = [c for c in api.call_args_list if c[0][0] == "/bookmarks/add"][0]
        self.assertEqual(add_call[0][1]["folder_id"], "222")

    def test_unknown_folder_name_without_create_dies(self):
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK])) as api:
            rc, _out, err = run_main(["add", "https://example.com/a", "--folder", "Nope"])

        self.assertEqual(rc, 1)
        self.assertIn("not found", err)
        # never reached the actual save
        self.assertFalse([c for c in api.call_args_list if c[0][0] == "/bookmarks/add"])

    def test_create_folder_creates_then_saves_into_new_id(self):
        new = {"type": "folder", "folder_id": 333, "title": "Nope"}
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK], add_result=new)) as api:
            rc, out, _err = run_main(
                ["add", "https://example.com/a", "--folder", "Nope", "--create-folder"])

        self.assertEqual(rc, 0)
        paths = [c[0][0] for c in api.call_args_list]
        self.assertIn("/folders/add", paths)
        add_call = [c for c in api.call_args_list if c[0][0] == "/bookmarks/add"][0]
        self.assertEqual(add_call[0][1]["folder_id"], "333")

    def test_create_folder_without_folder_dies(self):
        rc, _out, err = run_main(["add", "https://example.com/a", "--create-folder"])
        self.assertEqual(rc, 1)
        self.assertIn("--folder", err)


class TestFolderCommand(unittest.TestCase):
    def test_folder_list_prints_ids_and_titles(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call", return_value=[_WORK, _READING]):
            rc, out, _err = run_main(["folder", "list"])

        self.assertEqual(rc, 0)
        self.assertIn("111", out)
        self.assertIn("Work", out)
        self.assertIn("Reading List", out)

    def test_folder_add_creates_when_absent(self):
        new = {"type": "folder", "folder_id": 333, "title": "Fresh"}
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK], add_result=new)):
            rc, out, _err = run_main(["folder", "add", "Fresh"])

        self.assertEqual(rc, 0)
        self.assertIn("created folder: Fresh", out)
        self.assertIn("333", out)

    def test_folder_add_is_idempotent_for_existing_name(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK])) as api:
            rc, out, _err = run_main(["folder", "add", "work"])

        self.assertEqual(rc, 0)
        self.assertIn("already exists", out)
        self.assertFalse([c for c in api.call_args_list if c[0][0] == "/folders/add"])


class TestArchiveDelete(unittest.TestCase):
    def test_archive_calls_archive_endpoint_per_id(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call", return_value=[]) as api:
            rc, out, _err = run_main(["archive", "111", "222"])
        self.assertEqual(rc, 0)
        self.assertEqual([c[0][0] for c in api.call_args_list],
                         ["/bookmarks/archive", "/bookmarks/archive"])
        self.assertIn("archived: 111", out)

    def test_delete_without_yes_refuses_before_touching_creds(self):
        with mock.patch.object(cli.creds, "load_oauth_creds") as lc, \
             mock.patch.object(cli.transport, "api_call") as api:
            rc, _out, err = run_main(["delete", "111"])
        self.assertEqual(rc, 1)
        self.assertIn("--yes", err)
        api.assert_not_called()
        lc.assert_not_called()

    def test_delete_with_yes_hits_delete_endpoint(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call", return_value=[]) as api:
            rc, out, _err = run_main(["delete", "111", "--yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(api.call_args[0][0], "/bookmarks/delete")
        self.assertEqual(api.call_args[0][1], {"bookmark_id": "111"})
        self.assertIn("deleted: 111", out)

    def test_delete_non_numeric_id_dies(self):
        rc, _out, err = run_main(["delete", "abc", "--yes"])
        self.assertEqual(rc, 1)
        self.assertIn("numeric", err)


class TestFolderDelete(unittest.TestCase):
    def test_delete_without_yes_previews_and_makes_no_delete(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK, _READING])) as api:
            rc, _out, err = run_main(["folder", "delete", "Reading List"])
        self.assertEqual(rc, 1)
        self.assertIn("--yes", err)
        self.assertIn("Reading List", err)
        self.assertFalse([c for c in api.call_args_list if c[0][0] == "/folders/delete"])

    def test_delete_with_yes_resolves_name_and_deletes(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call",
                               side_effect=_fake_api([_WORK, _READING])) as api:
            rc, out, _err = run_main(["folder", "delete", "Reading List", "--yes"])
        self.assertEqual(rc, 0)
        del_call = [c for c in api.call_args_list if c[0][0] == "/folders/delete"][0]
        self.assertEqual(del_call[0][1]["folder_id"], "222")
        self.assertIn("deleted folder", out)

    def test_delete_unknown_folder_dies(self):
        with mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call", side_effect=_fake_api([_WORK])):
            rc, _out, err = run_main(["folder", "delete", "ghost", "--yes"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)


class TestConfigRoundTrip(unittest.TestCase):
    def test_freedium_add_then_show_reflects_it(self):
        tmpdir = tempfile.mkdtemp()
        cfg_path = os.path.join(tmpdir, "config.json")
        with mock.patch.object(config, "CONFIG_FILE", cfg_path):
            rc1, _out1, _err1 = run_main(["config", "freedium", "add", "economist.com"])
            self.assertEqual(rc1, 0)
            rc2, out2, _err2 = run_main(["config", "show", "--json"])

        self.assertEqual(rc2, 0)
        data = json.loads(out2)
        self.assertIn("economist.com", data["freedium_domains"])
        self.assertIn("medium.com", data["freedium_domains"])


class TestExportNoTarget(unittest.TestCase):
    def test_export_no_out_no_config_dir_dies(self):
        tmpdir = tempfile.mkdtemp()
        cfg_path = os.path.join(tmpdir, "config.json")  # does not exist → defaults
        with mock.patch.object(config, "CONFIG_FILE", cfg_path), \
             mock.patch.object(cli.sync, "sync") as sync_mock:
            rc, _out, err = run_main(["export"])

        self.assertEqual(rc, 1)
        self.assertIn("set --out", err)
        sync_mock.assert_not_called()


class TestApiErrorHint(unittest.TestCase):
    def test_add_apierror_1220_returns_1_and_prints_hint(self):
        err = transport.ApiError(400, 1220, "domain wants HTML")
        with mock.patch.object(cli.config, "load_config", return_value=config.Config()), \
             mock.patch.object(cli.creds, "load_oauth_creds", return_value=object()), \
             mock.patch.object(cli.transport, "api_call", side_effect=err):
            rc, _out, errtext = run_main(["add", "https://example.com/a", "--folder", "123"])

        self.assertEqual(rc, 1)
        self.assertIn(transport.ERROR_HINTS[1220], errtext)


if __name__ == "__main__":
    unittest.main()
