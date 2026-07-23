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
