"""Configuration: built-in defaults, then ``config.yaml``, then
``FSD_<SECTION>_<KEY>`` environment variables, each overriding the last.

The defaults open no rolls until a library root is given, but the two scanner
capture folders and the user's own ``rolls`` folder are listed so a fresh
install finds real rolls without setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from functools import cache
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import yaml

from fsd.paths import user_data_dir
from fsd.processing.tiffio import COMPRESSIONS

_ENV_PREFIX = "FSD"
_FILM_MODES = frozenset({"bw", "color"})
_EXPORT_FORMATS = frozenset({"tiff", "jpeg", "both"})


@dataclass
class LibraryConfig:
    """Folders searched for rolls, plus any opened by hand."""

    # Roots stay strings in the config file. resolved_roots() turns them into
    # paths, expanding ~ and treating relatives as under the user data folder.
    roots: list[str] = field(default_factory=lambda: [
        "~/Documents/Canon EDSDK Film Scanner/captures",
        "~/Documents/Canon Smart Film Scanner/captures",
        "rolls",
    ])

    def resolved_roots(self) -> list[Path]:
        """Roots as paths: ``~`` expanded, relatives under the user folder."""
        base = user_data_dir()
        paths = []
        for root in self.roots:
            path = Path(root).expanduser()
            if not path.is_absolute():
                path = base / path
            paths.append(path)
        return paths


@dataclass
class FilmConfig:
    """Starting values for the inversion pipeline."""

    mode: str = "bw"
    aspect: float = 1.5  # 1.0, 1.25, 7/6 and 4/3 are also valid; any value > 0


@dataclass
class DetectConfig:
    """How the frame is found inside the scan."""

    margin: float = 0.01
    min_confidence: float = 0.75


@dataclass
class ExportConfig:
    """Where and how finished positives are written."""

    format: str = "tiff"  # "tiff" | "jpeg" | "both"
    tiff_compression: str = "deflate"
    jpeg_quality: int = 95
    folder: str = "positives"


@dataclass
class ServerConfig:
    """The local web UI. Loopback by default: it can change your files."""

    host: str = "127.0.0.1"
    port: int = 8001


@dataclass
class Config:
    library: LibraryConfig = field(default_factory=LibraryConfig)
    film: FilmConfig = field(default_factory=FilmConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    def validate(self) -> None:
        """Reject values a hand-edited config.yaml could plausibly get wrong."""
        if self.film.mode not in _FILM_MODES:
            raise ValueError(
                f"film.mode must be one of: {', '.join(sorted(_FILM_MODES))} "
                f"(got {self.film.mode!r})."
            )
        if not self.film.aspect > 0:
            raise ValueError(f"film.aspect must be greater than 0 (got {self.film.aspect!r}).")
        if not 0 <= self.detect.margin <= 0.1:
            raise ValueError(
                f"detect.margin must be between 0 and 0.1 (got {self.detect.margin!r})."
            )
        if not 0 <= self.detect.min_confidence <= 1:
            raise ValueError(
                f"detect.min_confidence must be between 0 and 1 "
                f"(got {self.detect.min_confidence!r})."
            )
        if self.export.format not in _EXPORT_FORMATS:
            raise ValueError(
                f"export.format must be one of: {', '.join(sorted(_EXPORT_FORMATS))} "
                f"(got {self.export.format!r})."
            )
        if self.export.tiff_compression not in COMPRESSIONS:
            raise ValueError(
                f"export.tiff_compression must be one of: {', '.join(COMPRESSIONS)} "
                f"(got {self.export.tiff_compression!r})."
            )
        if not 1 <= self.export.jpeg_quality <= 100:
            raise ValueError(
                f"export.jpeg_quality must be between 1 and 100 "
                f"(got {self.export.jpeg_quality!r})."
            )
        if not 1 <= self.server.port <= 65535:
            raise ValueError(f"server.port must be between 1 and 65535 (got {self.server.port!r}).")


@cache
def _field_types(cls: type) -> dict[str, Any]:
    """Real types, not the strings ``fields()`` reports under postponed
    annotations -- which never compare equal to ``bool`` or ``list[str]``.
    """
    return get_type_hints(cls)


def _coerce(value: Any, target_type: Any) -> Any:
    """Coerce a YAML/env value to the declared type. Env vars arrive as
    strings; list fields split on ``os.pathsep`` so a single variable can hold
    several library roots.
    """
    if target_type is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if get_origin(target_type) is list:
        item_type = (get_args(target_type) or (str,))[0]
        if isinstance(value, str):
            value = [part for part in value.split(os.pathsep) if part != ""]
        return [_coerce(part, item_type) for part in value]
    if target_type is float:
        return float(value)
    if target_type is int:
        return int(value)
    if target_type is str:
        return str(value)
    return value


def _apply_mapping(section: Any, values: dict[str, Any], where: str) -> None:
    """Overlay a dict of values onto a config dataclass instance, in place."""
    known = _field_types(type(section))
    for key, value in values.items():
        if key not in known:
            raise ValueError(
                f"Unknown setting '{key}' in {where}. "
                f"Valid settings here: {', '.join(sorted(known))}."
            )
        setattr(section, key, _coerce(value, known[key]))


def _apply_env(config: Config) -> None:
    """Overlay ``FSD_<SECTION>_<KEY>`` environment variables onto the config."""
    for section_field in fields(config):
        section = getattr(config, section_field.name)
        if not is_dataclass(section):
            continue
        types = _field_types(type(section))
        for f in fields(section):
            env_name = f"{_ENV_PREFIX}_{section_field.name}_{f.name}".upper()
            if env_name in os.environ:
                setattr(section, f.name, _coerce(os.environ[env_name], types[f.name]))


def load_config(path: Path | None = None) -> Config:
    """Load defaults, then ``config.yaml``, then the environment.

    The default file being absent is fine; an explicit ``path`` that is missing
    is not. Unknown sections and settings raise, on purpose: a silently ignored
    typo in a library root is worse to debug than an error at startup.
    """
    config = Config()

    config_path = Path(path) if path is not None else user_data_dir() / "config.yaml"
    if path is not None and not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping at the top level.")
        known_sections = {f.name for f in fields(config)}
        for section_name, values in raw.items():
            if section_name not in known_sections:
                raise ValueError(
                    f"Unknown section '{section_name}' in {config_path}. "
                    f"Valid sections: {', '.join(sorted(known_sections))}."
                )
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError(f"Section '{section_name}' in {config_path} must be a mapping.")
            _apply_mapping(getattr(config, section_name), values, f"{config_path} [{section_name}]")

    _apply_env(config)
    config.validate()
    return config
