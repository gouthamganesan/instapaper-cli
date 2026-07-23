"""Unit tests for creds.py. Stdlib unittest only.

Run: python3 instapaper_cli/test_creds.py
(or: python3 -m pytest instapaper_cli/test_creds.py)

These tests never touch the real macOS Keychain or the real ~/.config —
module-level path constants are monkeypatched to a temp directory per test.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import creds  # noqa: E402


class CredsTestBase(unittest.TestCase):
    ENV_KEYS = (
        "INSTAPAPER_USERNAME",
        "INSTAPAPER_PASSWORD",
        "INSTAPAPER_CONSUMER_KEY",
        "INSTAPAPER_CONSUMER_SECRET",
        "INSTAPAPER_OAUTH_TOKEN",
        "INSTAPAPER_OAUTH_SECRET",
    )

    def setUp(self):
        # Snapshot and clear relevant env vars.
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)

        # Snapshot module path constants, redirect to a temp dir.
        self._saved_config_dir = creds.CONFIG_DIR
        self._saved_username_file = creds.USERNAME_FILE
        self._saved_creds_file = creds.CREDS_FILE
        self._saved_platform = sys.platform

        self.tmpdir = tempfile.mkdtemp(prefix="instapaper-creds-test-")
        creds.CONFIG_DIR = self.tmpdir
        creds.USERNAME_FILE = os.path.join(self.tmpdir, "username")
        creds.CREDS_FILE = os.path.join(self.tmpdir, "credentials.json")

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        creds.CONFIG_DIR = self._saved_config_dir
        creds.USERNAME_FILE = self._saved_username_file
        creds.CREDS_FILE = self._saved_creds_file
        sys.platform = self._saved_platform

        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestUsername(CredsTestBase):
    def test_username_from_env(self):
        os.environ["INSTAPAPER_USERNAME"] = "env-user@example.com"
        self.assertEqual(creds.get_username(), "env-user@example.com")

    def test_username_from_file(self):
        with open(creds.USERNAME_FILE, "w") as f:
            f.write("file-user@example.com\n")
        self.assertEqual(creds.get_username(), "file-user@example.com")

    def test_username_missing_raises(self):
        with self.assertRaises(creds.CredsError):
            creds.get_username()


class TestPassword(CredsTestBase):
    def test_password_empty_string_is_valid(self):
        os.environ["INSTAPAPER_PASSWORD"] = ""
        self.assertEqual(creds.get_password("someone"), "")

    def test_password_nonempty_from_env(self):
        os.environ["INSTAPAPER_PASSWORD"] = "hunter2"
        self.assertEqual(creds.get_password("someone"), "hunter2")

    def test_password_non_darwin_without_env_raises(self):
        sys.platform = "linux"
        with self.assertRaises(creds.CredsError):
            creds.get_password("someone")


class TestOAuthLoad(CredsTestBase):
    def _write_creds_file(self, **overrides):
        payload = {
            "consumer_key": "ck-file",
            "consumer_secret": "cs-file",
            "oauth_token": "tok-file",
            "oauth_token_secret": "toksec-file",
        }
        payload.update(overrides)
        with open(creds.CREDS_FILE, "w") as f:
            json.dump(payload, f)
        return payload

    def test_load_from_file(self):
        self._write_creds_file()
        result = creds.load_oauth_creds()
        self.assertEqual(
            result,
            creds.OAuthCreds(
                consumer_key="ck-file",
                consumer_secret="cs-file",
                oauth_token="tok-file",
                oauth_token_secret="toksec-file",
            ),
        )

    def test_env_overrides_single_field(self):
        self._write_creds_file()
        os.environ["INSTAPAPER_CONSUMER_KEY"] = "ck-env"
        result = creds.load_oauth_creds()
        self.assertEqual(result.consumer_key, "ck-env")
        # remaining fields still come from the file
        self.assertEqual(result.consumer_secret, "cs-file")
        self.assertEqual(result.oauth_token, "tok-file")
        self.assertEqual(result.oauth_token_secret, "toksec-file")

    def test_missing_field_raises(self):
        payload = {
            "consumer_key": "ck-file",
            "consumer_secret": "cs-file",
            # oauth_token deliberately omitted
            "oauth_token_secret": "toksec-file",
        }
        with open(creds.CREDS_FILE, "w") as f:
            json.dump(payload, f)
        with self.assertRaises(creds.CredsError) as ctx:
            creds.load_oauth_creds()
        self.assertIn("instapaper login", str(ctx.exception))

    def test_missing_file_and_no_env_raises(self):
        with self.assertRaises(creds.CredsError):
            creds.load_oauth_creds()


class TestOAuthSave(CredsTestBase):
    def test_save_writes_0600_file(self):
        oc = creds.OAuthCreds(
            consumer_key="ck",
            consumer_secret="cs",
            oauth_token="tok",
            oauth_token_secret="toksec",
        )
        creds.save_oauth_creds(oc)

        self.assertTrue(os.path.exists(creds.CREDS_FILE))
        mode = oct(os.stat(creds.CREDS_FILE).st_mode & 0o777)
        self.assertEqual(mode, "0o600")

        with open(creds.CREDS_FILE) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["consumer_key"], "ck")
        self.assertEqual(on_disk["oauth_token_secret"], "toksec")

        # round-trips through load_oauth_creds too
        loaded = creds.load_oauth_creds()
        self.assertEqual(loaded, oc)

    def test_save_creates_config_dir(self):
        nested = os.path.join(self.tmpdir, "nested", "config")
        creds.CONFIG_DIR = nested
        creds.CREDS_FILE = os.path.join(nested, "credentials.json")

        oc = creds.OAuthCreds("ck", "cs", "tok", "toksec")
        creds.save_oauth_creds(oc)

        self.assertTrue(os.path.isdir(nested))
        dir_mode = oct(os.stat(nested).st_mode & 0o777)
        self.assertEqual(dir_mode, "0o700")


if __name__ == "__main__":
    unittest.main()
