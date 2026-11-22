"""Configuration loading: defaults, file, then environment."""

from __future__ import annotations

import os

import pytest
import yaml

from fsd import config as config_module
from fsd.config import Config, load_config
from fsd.paths import REPO_ROOT


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    """No user config file and no inherited FSD_* overrides."""
    for name in list(os.environ):
        if name.startswith("FSD_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "user_data_dir", lambda: tmp_path)
    return tmp_path


def test_defaults(clean_config):
    cfg = load_config()
    assert cfg.library.roots == [
        "~/Documents/Canon EDSDK Film Scanner/captures",
        "~/Documents/Canon Smart Film Scanner/captures",
        "rolls",
    ]
    assert cfg.film.mode == "bw"
    assert cfg.film.aspect == 1.5
    assert cfg.detect.margin == 0.01
    assert cfg.detect.min_confidence == 0.75
    assert cfg.export.format == "tiff"
    assert cfg.export.tiff_compression == "deflate"
    assert cfg.export.jpeg_quality == 95
    assert cfg.export.folder == "positives"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8001


def test_unknown_key_is_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  prot: 9000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prot"):
        load_config(path)


def test_env_override_port(clean_config, monkeypatch):
    monkeypatch.setenv("FSD_SERVER_PORT", "9000")
    assert load_config().server.port == 9000


def test_env_roots_split_on_pathsep(clean_config, monkeypatch):
    monkeypatch.setenv("FSD_LIBRARY_ROOTS", os.pathsep.join(["/one", "/two"]))
    assert load_config().library.roots == ["/one", "/two"]


def test_resolved_roots_expands_home_and_resolves_relatives(clean_config, monkeypatch):
    home = clean_config / "home"
    # expanduser reads HOME on POSIX and USERPROFILE on Windows.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cfg = load_config()
    assert cfg.library.resolved_roots() == [
        home / "Documents" / "Canon EDSDK Film Scanner" / "captures",
        home / "Documents" / "Canon Smart Film Scanner" / "captures",
        clean_config / "rolls",
    ]


def test_example_config_roots_match_defaults():
    example = yaml.safe_load(
        (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    )
    assert example["library"]["roots"] == Config().library.roots


def test_invalid_mode_is_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("film:\n  mode: sepia\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert "film.mode" in str(exc.value)
    assert "bw" in str(exc.value) and "color" in str(exc.value)


def test_invalid_format_is_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("export:\n  format: png\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert "export.format" in str(exc.value)
    assert "tiff" in str(exc.value)


def test_port_out_of_range_is_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 70000\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert "server.port" in str(exc.value)
    assert "65535" in str(exc.value)
