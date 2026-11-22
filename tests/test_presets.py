"""Presets stored in presets.yaml."""

from __future__ import annotations

import yaml

from fsd.darkroom.presets import load_presets, match_preset, save_preset


def test_match_case_insensitive():
    presets = {"Kentmere 400": {"film": {}}, "HP5 Plus": {}}

    assert match_preset(presets, "kentmere  400") == "Kentmere 400"
    assert match_preset(presets, "  HP5   PLUS ") == "HP5 Plus"
    assert match_preset(presets, "Tri-X 400") is None


def test_missing_file_empty(tmp_path):
    assert load_presets(tmp_path / "presets.yaml") == {}


def test_save_then_load(tmp_path):
    path = tmp_path / "presets.yaml"
    film = {"mode": "bw", "contrast": 1.7}
    sharpen = {"amount": 0.5, "radius": 1.0}

    save_preset(path, "Kentmere 400", film, sharpen)
    save_preset(path, "HP5", {"contrast": 1.5}, {"amount": 1.0})

    assert load_presets(path) == {
        "Kentmere 400": {"film": film, "sharpen": sharpen},
        "HP5": {"film": {"contrast": 1.5}, "sharpen": {"amount": 1.0}},
    }
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["HP5"]["film"] == {
        "contrast": 1.5
    }
