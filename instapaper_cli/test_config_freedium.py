from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import config  # noqa: E402
import freedium  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="instapaper-cli-test-")
        self._orig_config_file = config.CONFIG_FILE
        config.CONFIG_FILE = os.path.join(self.tmpdir, "sub", "config.json")

    def tearDown(self):
        config.CONFIG_FILE = self._orig_config_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_defaults_when_file_absent(self):
        self.assertFalse(os.path.exists(config.CONFIG_FILE))
        cfg = config.load_config()
        self.assertEqual(cfg, config.Config())
        self.assertFalse(cfg.freedium_enabled)
        self.assertEqual(cfg.freedium_mirror, "https://freedium.cfd/")
        self.assertEqual(cfg.freedium_domains, ["medium.com"])
        self.assertIsNone(cfg.export_dir)

    def test_save_and_reload_round_trip(self):
        cfg = config.Config(
            freedium_enabled=True,
            freedium_mirror="https://example-mirror.test/",
            freedium_domains=["medium.com", "substack.com"],
            export_dir="/tmp/export",
        )
        config.save_config(cfg)
        self.assertTrue(os.path.exists(config.CONFIG_FILE))

        # config dir should be created with 0700 permissions
        config_dir = os.path.dirname(config.CONFIG_FILE)
        mode = os.stat(config_dir).st_mode & 0o777
        self.assertEqual(mode, 0o700)

        reloaded = config.load_config()
        self.assertEqual(reloaded, cfg)

    def test_atomic_write_no_stray_temp_files(self):
        cfg = config.Config(freedium_enabled=True)
        config.save_config(cfg)
        config_dir = os.path.dirname(config.CONFIG_FILE)
        entries = os.listdir(config_dir)
        self.assertEqual(entries, [os.path.basename(config.CONFIG_FILE)])

    def test_unknown_key_tolerance(self):
        config_dir = os.path.dirname(config.CONFIG_FILE)
        os.makedirs(config_dir, mode=0o700, exist_ok=True)
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "freedium_enabled": True,
                    "some_future_field": "should be ignored",
                    "another_unknown": {"nested": 1},
                },
                f,
            )

        cfg = config.load_config()
        self.assertTrue(cfg.freedium_enabled)
        self.assertEqual(cfg.freedium_mirror, "https://freedium.cfd/")
        self.assertFalse(hasattr(cfg, "some_future_field"))

    def test_corrupt_json_falls_back_to_defaults(self):
        config_dir = os.path.dirname(config.CONFIG_FILE)
        os.makedirs(config_dir, mode=0o700, exist_ok=True)
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{not valid json at all")

        cfg = config.load_config()
        self.assertEqual(cfg, config.Config())

    def test_partial_json_fills_in_defaults(self):
        config_dir = os.path.dirname(config.CONFIG_FILE)
        os.makedirs(config_dir, mode=0o700, exist_ok=True)
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"export_dir": "/only/this/field"}, f)

        cfg = config.load_config()
        self.assertEqual(cfg.export_dir, "/only/this/field")
        self.assertFalse(cfg.freedium_enabled)
        self.assertEqual(cfg.freedium_domains, ["medium.com"])

    def test_non_dict_json_falls_back_to_defaults(self):
        config_dir = os.path.dirname(config.CONFIG_FILE)
        os.makedirs(config_dir, mode=0o700, exist_ok=True)
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)

        cfg = config.load_config()
        self.assertEqual(cfg, config.Config())


class FreediumTests(unittest.TestCase):
    def make_cfg(self, **overrides):
        base = dict(
            freedium_enabled=True,
            freedium_mirror="https://freedium.cfd/",
            freedium_domains=["medium.com"],
            export_dir=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_should_wrap_true_for_exact_domain(self):
        cfg = self.make_cfg()
        self.assertTrue(freedium.should_wrap("https://medium.com/article", cfg))

    def test_should_wrap_true_for_subdomain(self):
        cfg = self.make_cfg()
        self.assertTrue(freedium.should_wrap("https://sub.medium.com/article", cfg))

    def test_should_wrap_false_for_unrelated_domain(self):
        cfg = self.make_cfg()
        self.assertFalse(freedium.should_wrap("https://example.com/article", cfg))

    def test_should_wrap_false_for_lookalike_domain(self):
        # "notmedium.com" should NOT match "medium.com" (no dot-boundary)
        cfg = self.make_cfg()
        self.assertFalse(freedium.should_wrap("https://notmedium.com/article", cfg))

    def test_should_wrap_false_when_disabled(self):
        cfg = self.make_cfg(freedium_enabled=False)
        self.assertFalse(freedium.should_wrap("https://medium.com/article", cfg))

    def test_should_wrap_false_when_host_is_mirror(self):
        cfg = self.make_cfg()
        self.assertFalse(
            freedium.should_wrap("https://freedium.cfd/https://medium.com/x", cfg)
        )

    def test_apply_override_true_wraps_unconditionally(self):
        cfg = self.make_cfg(freedium_enabled=False)
        url = "https://example.com/article"
        result = freedium.apply(url, cfg, override=True)
        self.assertEqual(result, "https://freedium.cfd/" + url)

    def test_apply_override_false_returns_unchanged(self):
        cfg = self.make_cfg(freedium_enabled=True)
        url = "https://medium.com/article"
        result = freedium.apply(url, cfg, override=False)
        self.assertEqual(result, url)

    def test_apply_override_none_wraps_when_should_wrap(self):
        cfg = self.make_cfg()
        url = "https://medium.com/article"
        result = freedium.apply(url, cfg, override=None)
        self.assertEqual(result, "https://freedium.cfd/" + url)

    def test_apply_override_none_unchanged_when_not_should_wrap(self):
        cfg = self.make_cfg()
        url = "https://example.com/article"
        result = freedium.apply(url, cfg, override=None)
        self.assertEqual(result, url)

    def test_apply_default_override_behaves_like_none(self):
        cfg = self.make_cfg()
        url = "https://medium.com/article"
        result = freedium.apply(url, cfg)
        self.assertEqual(result, "https://freedium.cfd/" + url)

    def test_apply_no_double_wrap_when_url_host_is_mirror(self):
        cfg = self.make_cfg()
        url = "https://freedium.cfd/https://medium.com/article"
        result = freedium.apply(url, cfg, override=None)
        self.assertEqual(result, url)

    def test_apply_url_kept_verbatim_not_reencoded(self):
        cfg = self.make_cfg()
        url = "https://medium.com/article?q=a b&x=y%20z"
        result = freedium.apply(url, cfg, override=True)
        self.assertEqual(result, "https://freedium.cfd/" + url)

    def test_apply_mirror_without_trailing_slash(self):
        cfg = self.make_cfg(freedium_mirror="https://freedium.cfd")
        url = "https://medium.com/article"
        result = freedium.apply(url, cfg, override=True)
        self.assertEqual(result, "https://freedium.cfd/" + url)

    def test_should_wrap_multiple_domains(self):
        cfg = self.make_cfg(freedium_domains=["medium.com", "substack.com"])
        self.assertTrue(freedium.should_wrap("https://foo.substack.com", cfg))
        self.assertTrue(freedium.should_wrap("https://medium.com", cfg))
        self.assertFalse(freedium.should_wrap("https://example.com", cfg))

    def test_config_dataclass_integration(self):
        # Real Config instance (not SimpleNamespace) also works.
        cfg = config.Config(freedium_enabled=True)
        self.assertTrue(freedium.should_wrap("https://medium.com/x", cfg))
        self.assertEqual(
            freedium.apply("https://medium.com/x", cfg),
            "https://freedium.cfd/https://medium.com/x",
        )


if __name__ == "__main__":
    unittest.main()
