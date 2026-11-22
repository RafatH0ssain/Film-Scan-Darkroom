"""Non-destructive edits for one roll, stored in ``darkroom.json`` beside it.

The sidecar holds what the app figured out automatically (crop, angle, base
region) separately from what the user changed, so a rescanned source can be
re-detected without throwing away any manual work. Settings are merged in one
place, :func:`effective`, which is the only thing previews and exports use.

Writes are atomic: a complete temp file is renamed over the sidecar. A file
that cannot be read is kept aside as ``darkroom.json.bad-<timestamp>`` rather
than silently replaced.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from fsd.config import Config
from fsd.processing.detect import Detection
from fsd.processing.film import FilmParams
from fsd.processing.render import FrameSettings
from fsd.processing.sharpen import SharpenParams

logger = logging.getLogger(__name__)

SIDECAR = "darkroom.json"
SCHEMA = 1

#: What "apply to all" copies: film, sharpen and orientation, never the frame's
#: own crop or angle.
APPLY_FIELDS = ("film", "sharpen", "mirror", "rotate90")

_USER_FIELDS = frozenset({"crop", "angle", "rotate90", "mirror", "base_region", "film", "sharpen"})
_TUPLE_FIELDS = {FilmParams: frozenset({"base", "channel_gain"}), SharpenParams: frozenset()}


@dataclass
class FrameEdit:
    """One frame's automatic detection and the user's sparse overrides."""

    source: str
    fingerprint: tuple[int, int] | None = None
    auto: Detection | None = None
    user: dict = field(default_factory=dict)
    rejected: bool = False
    exported: dict = field(default_factory=dict)


@dataclass
class Edits:
    """All of a roll's darkroom state."""

    roll_defaults: dict = field(default_factory=lambda: {"film": {}, "sharpen": {}})
    preset: str | None = None
    frames: dict[int, FrameEdit] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _jsonable(value: Any) -> Any:
    """Recursively turn dataclasses and tuples into JSON-ready values."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _clean_params(mapping: Any, cls: type, label: str, warnings: list[str]) -> dict:
    """Keep only the known fields of ``cls`` in a partial settings dict.

    ``base`` is a FilmParams field but is never stored: the render measures it
    from the source. Unknown names are dropped with a warning, never fatal.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        warnings.append(f"Ignored {label}: expected a mapping of settings.")
        return {}
    allowed = {f.name for f in fields(cls)}
    tuple_fields = _TUPLE_FIELDS.get(cls, frozenset())
    clean: dict = {}
    for key, value in mapping.items():
        if key == "base":
            warnings.append(f"Ignored 'base' in {label}; the render measures the film base.")
            continue
        if key not in allowed:
            warnings.append(f"Ignored unknown setting '{key}' in {label}.")
            continue
        if value is None:
            continue
        if key in tuple_fields:
            try:
                value = tuple(value)
            except TypeError:
                warnings.append(f"Ignored '{key}' in {label}: expected a sequence.")
                continue
        clean[key] = value
    return clean


def _apply_params(params, mapping, cls, label, warnings):
    """Overlay a partial settings dict on ``params``."""
    changes = _clean_params(mapping, cls, label, warnings)
    if not changes:
        return params
    try:
        return replace(params, **changes)
    except (TypeError, ValueError) as exc:
        warnings.append(f"Ignored invalid settings in {label} ({exc}).")
        return params


def _quad(value: Any, label: str, key: str, warnings: list[str]) -> tuple[float, ...] | None:
    """A crop or region as four floats, or None if it cannot be read."""
    if isinstance(value, (str, bytes)):
        warnings.append(f"Ignored '{key}' in {label}: expected four numbers.")
        return None
    try:
        quad = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        warnings.append(f"Ignored '{key}' in {label}: expected four numbers.")
        return None
    if len(quad) != 4:
        warnings.append(f"Ignored '{key}' in {label}: expected four numbers.")
        return None
    return quad


def _number(value: Any, cast, label: str, key: str, warnings: list[str]):
    try:
        return cast(value)
    except (TypeError, ValueError):
        warnings.append(f"Ignored '{key}' in {label}: expected a number.")
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clean_user(raw: Any, label: str, warnings: list[str]) -> dict:
    """Keep the known sparse frame overrides, with tuples as tuples."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        warnings.append(f"Ignored {label}: expected a mapping of overrides.")
        return {}
    clean: dict = {}
    for key, value in raw.items():
        if key in ("crop", "base_region"):
            clean[key] = None if value is None else _quad(value, label, key, warnings)
        elif key in ("film", "sharpen"):
            cls = FilmParams if key == "film" else SharpenParams
            clean[key] = _clean_params(value, cls, f"{label}[{key}]", warnings)
        elif key in ("angle", "rotate90", "mirror"):
            clean[key] = value
        else:
            warnings.append(f"Ignored unknown frame override '{key}' in {label}.")
    return clean


def _fingerprint(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return None
    try:
        if len(raw) != 2:
            raise ValueError("wrong length")
        return (int(raw[0]), int(raw[1]))
    except (TypeError, ValueError):
        return None


def _detection(raw: Any, label: str, warnings: list[str]) -> Detection | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append(f"Ignored {label}: expected a detection mapping.")
        return None
    crop = _quad(raw.get("crop"), label, "crop", warnings) or (0.0, 0.0, 1.0, 1.0)
    region = raw.get("base_region")
    base_region = None if region is None else _quad(region, label, "base_region", warnings)
    angle = _number(raw.get("angle", 0.0), float, label, "angle", warnings)
    confidence = _number(raw.get("confidence", 0.0), float, label, "confidence", warnings)
    reasons = raw.get("reasons") or []
    return Detection(
        crop=crop,
        angle=0.0 if angle is None else angle,
        base_region=base_region,
        confidence=0.0 if confidence is None else confidence,
        reasons=list(reasons) if isinstance(reasons, list) else [],
    )


def _quarantine(path: Path, reason: str) -> Edits:
    """Keep an unreadable sidecar under a timestamped name and start fresh."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bad-{stamp}")
    attempt = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bad-{stamp}-{attempt}")
        attempt += 1
    try:
        path.rename(target)
        message = f"Could not read {path.name} ({reason}); saved as {target.name} and started fresh."
    except OSError as exc:
        message = f"Could not read {path.name} ({reason}); could not set it aside ({exc})."
    logger.warning("%s", message)
    return Edits(warnings=[message])


def load(roll_dir: Path) -> Edits:
    """Read the roll's sidecar, returning fresh state if it is absent or bad."""
    path = Path(roll_dir) / SIDECAR
    if not path.is_file():
        return Edits()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _quarantine(path, str(exc))
    if not isinstance(raw, dict):
        return _quarantine(path, "not a JSON object")

    warnings: list[str] = []
    if raw.get("schema", SCHEMA) != SCHEMA:
        warnings.append(f"Unknown darkroom.json schema {raw.get('schema')!r}; reading what we can.")
    edits = Edits(warnings=warnings)
    preset = raw.get("preset")
    edits.preset = preset if isinstance(preset, str) else None

    roll = raw.get("roll_defaults") or {}
    if not isinstance(roll, dict):
        warnings.append("Ignored darkroom.json [roll_defaults]: expected a mapping.")
        roll = {}
    edits.roll_defaults = {
        "film": _clean_params(roll.get("film"), FilmParams, "darkroom.json [roll_defaults][film]", warnings),
        "sharpen": _clean_params(
            roll.get("sharpen"), SharpenParams, "darkroom.json [roll_defaults][sharpen]", warnings
        ),
    }
    for key in ("mirror", "rotate90"):
        if key in roll:
            edits.roll_defaults[key] = roll[key]

    frames = raw.get("frames") or {}
    if not isinstance(frames, dict):
        warnings.append("Ignored darkroom.json [frames]: expected a mapping.")
        frames = {}
    for key, value in frames.items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            warnings.append(f"Ignored frame {key!r} in darkroom.json: expected a frame number.")
            continue
        label = f"darkroom.json [frames][{n}]"
        if not isinstance(value, dict):
            warnings.append(f"Ignored {label}: expected a mapping.")
            continue
        source = value.get("source")
        exported = value.get("exported") or {}
        if not isinstance(exported, dict):
            warnings.append(f"Ignored {label}[exported]: expected a mapping.")
            exported = {}
        edits.frames[n] = FrameEdit(
            source=source if isinstance(source, str) else "",
            fingerprint=_fingerprint(value.get("fingerprint")),
            auto=_detection(value.get("auto"), f"{label}[auto]", warnings),
            user=_clean_user(value.get("user"), f"{label}[user]", warnings),
            rejected=bool(value.get("rejected", False)),
            exported=exported,
        )
    return edits


def save(roll_dir: Path, edits: Edits) -> None:
    """Write the sidecar atomically: full temp file, then ``os.replace``."""
    path = Path(roll_dir) / SIDECAR
    tmp = path.with_name("darkroom.json.tmp")
    payload = {
        "schema": SCHEMA,
        "roll_defaults": _jsonable(edits.roll_defaults),
        "preset": edits.preset,
        "frames": {str(n): _jsonable(asdict(frame)) for n, frame in edits.frames.items()},
    }
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def needs_detection(edit: FrameEdit | None, source: Path) -> bool:
    """Whether ``source`` has no usable detection or has changed since it."""
    if edit is None or edit.auto is None or edit.fingerprint is None:
        return True
    try:
        stat = source.stat()
    except OSError:
        return True
    return edit.fingerprint != (stat.st_mtime_ns, stat.st_size)


def _apply_layer(film, sharpen, rotate90, mirror, mapping, label, warnings):
    """Overlay preset or roll defaults: film, sharpen and orientation only."""
    if not isinstance(mapping, dict):
        if mapping is not None:
            warnings.append(f"Ignored {label}: expected a mapping of settings.")
        return film, sharpen, rotate90, mirror
    film = _apply_params(film, mapping.get("film"), FilmParams, f"{label}[film]", warnings)
    sharpen = _apply_params(
        sharpen, mapping.get("sharpen"), SharpenParams, f"{label}[sharpen]", warnings
    )
    if mapping.get("rotate90") is not None:
        value = _number(mapping["rotate90"], int, label, "rotate90", warnings)
        if value is not None:
            rotate90 = value
    if mapping.get("mirror") is not None:
        mirror = _bool(mapping["mirror"])
    return film, sharpen, rotate90, mirror


def effective(edits: Edits, n: int, preset: dict | None, cfg: Config) -> FrameSettings:
    """Merge defaults ← preset ← roll defaults ← auto detection ← user edits."""
    warnings: list[str] = []
    film = FilmParams(mode=cfg.film.mode)
    sharpen = SharpenParams()
    crop: tuple[float, ...] = (0.0, 0.0, 1.0, 1.0)
    angle = 0.0
    rotate90 = 0
    mirror = False
    base_region: tuple[float, ...] | None = None

    film, sharpen, rotate90, mirror = _apply_layer(
        film, sharpen, rotate90, mirror, preset, "preset", warnings
    )
    film, sharpen, rotate90, mirror = _apply_layer(
        film, sharpen, rotate90, mirror, edits.roll_defaults, "roll defaults", warnings
    )

    frame = edits.frames.get(n)
    auto = frame.auto if frame is not None else None
    if auto is not None:
        auto_crop = _quad(auto.crop, "auto detection", "crop", warnings)
        if auto_crop is not None:
            crop = auto_crop
        auto_region = (
            None
            if auto.base_region is None
            else _quad(auto.base_region, "auto detection", "base_region", warnings)
        )
        if auto.base_region is not None:
            base_region = auto_region
        angle = _number(auto.angle, float, "auto detection", "angle", warnings) or 0.0

    if frame is not None:
        label = f"frame {n}[user]"
        user = frame.user
        for key in ("crop", "base_region"):
            value = user.get(key)
            if value is not None:
                parsed = _quad(value, label, key, warnings)
                if parsed is not None:
                    if key == "crop":
                        crop = parsed
                    else:
                        base_region = parsed
        if user.get("angle") is not None:
            value = _number(user["angle"], float, label, "angle", warnings)
            if value is not None:
                angle = value
        if user.get("rotate90") is not None:
            value = _number(user["rotate90"], int, label, "rotate90", warnings)
            if value is not None:
                rotate90 = value
        if user.get("mirror") is not None:
            mirror = _bool(user["mirror"])
        film = _apply_params(film, user.get("film"), FilmParams, f"{label}[film]", warnings)
        sharpen = _apply_params(
            sharpen, user.get("sharpen"), SharpenParams, f"{label}[sharpen]", warnings
        )

    edits.warnings.extend(warnings)
    return FrameSettings(
        crop=crop,
        angle=angle,
        rotate90=rotate90,
        mirror=mirror,
        base_region=base_region,
        film=film,
        sharpen=sharpen,
    )


def settings_hash(s: FrameSettings) -> str:
    """Stable SHA-1 of the canonical JSON form of a frame's settings."""
    payload = json.dumps(_jsonable(asdict(s)), sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def apply(
    edits: Edits, from_n: int, to: list[int], fields: tuple[str, ...] = APPLY_FIELDS
) -> None:
    """Copy the source frame's visible values for ``fields`` onto other frames.

    For film and sharpen that means the user's partial overrides merged over
    the roll defaults. Crop and angle are deliberately absent: every frame has
    its own. The source frame and frame numbers not in the roll are untouched.
    """
    source = edits.frames.get(from_n)
    if source is None:
        return
    roll = edits.roll_defaults
    roll_film = roll.get("film") or {}
    roll_sharpen = roll.get("sharpen") or {}
    shared: dict = {}
    for name in fields:
        if name == "film":
            value = {**roll_film, **(source.user.get("film") or {})}
        elif name == "sharpen":
            value = {**roll_sharpen, **(source.user.get("sharpen") or {})}
        elif name in ("mirror", "rotate90"):
            value = source.user.get(name, roll.get(name))
        else:
            continue
        if value is not None and value != {}:
            shared[name] = copy.deepcopy(value)
    if not shared:
        return
    for n in to:
        if n == from_n:
            continue
        target = edits.frames.get(n)
        if target is not None:
            target.user.update(copy.deepcopy(shared))
