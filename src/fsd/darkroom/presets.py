"""Named film and sharpen presets, kept in ``presets.yaml`` in the user folder.

A preset is matched to the roll's stock name when the roll is opened, so a
favourite film remembers how it likes to be inverted. The path is passed in by
the caller; from a checkout and in a bundle it lives under ``user_data_dir()``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_presets(path: Path) -> dict[str, dict]:
    """Load the preset mapping, or an empty one if the file is missing or bad."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): value for name, value in raw.items() if isinstance(value, dict)}


def save_preset(path: Path, name: str, film: dict, sharpen: dict) -> None:
    """Store (or replace) one named preset, leaving the others alone."""
    path = Path(path)
    presets = load_presets(path)
    presets[name] = {"film": dict(film), "sharpen": dict(sharpen)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(presets, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    os.replace(tmp, path)


def _normalise(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def match_preset(presets: dict[str, dict], stock: str) -> str | None:
    """Find the preset whose name equals ``stock`` ignoring case and spacing."""
    wanted = _normalise(stock)
    for name in presets:
        if _normalise(name) == wanted:
            return name
    return None
