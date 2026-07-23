from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Optional, List

CONFIG_FILE = os.path.expanduser("~/.config/instapaper/config.json")


@dataclass
class Config:
    freedium_enabled: bool = False
    freedium_mirror: str = "https://freedium.cfd/"
    freedium_domains: List[str] = field(default_factory=lambda: ["medium.com"])
    export_dir: Optional[str] = None


def load_config() -> Config:
    """Load config from CONFIG_FILE. Missing file -> defaults. Unknown keys
    are ignored (forward-compat). Partial or corrupt JSON falls back to
    defaults for whatever can't be salvaged."""
    if not os.path.exists(CONFIG_FILE):
        return Config()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return Config()

    if not isinstance(raw, dict):
        return Config()

    defaults = Config()
    known_fields = {f.name for f in _fields(defaults)}

    kwargs = {}
    for key in known_fields:
        if key in raw:
            kwargs[key] = raw[key]

    try:
        return Config(**kwargs)
    except TypeError:
        return Config()


def _fields(cfg: Config):
    from dataclasses import fields as _dc_fields

    return _dc_fields(cfg)


def save_config(cfg: Config) -> None:
    """Atomically write cfg to CONFIG_FILE. Creates the config dir (0o700)
    if needed, writes to a temp file in the same dir, then os.replace()s it
    into place."""
    config_dir = os.path.dirname(CONFIG_FILE)
    os.makedirs(config_dir, mode=0o700, exist_ok=True)

    data = asdict(cfg)

    fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=config_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, CONFIG_FILE)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
