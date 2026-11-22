"""Roll discovery, frame grouping and the library view.

A roll is a folder of scans with a ``roll.json`` beside them. The scanner may
still be appending to that sidecar, so it is only ever read here: Darkroom's
own state goes in ``darkroom.json``. Frames are grouped by the ``_FrameNN``
token in the file name, RAW beating HEIF beating JPEG/TIFF as the source for a
frame, with every other file kept as a companion.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from fsd.processing.source import HEIF_EXTENSIONS, RAW_EXTENSIONS, decodable

logger = logging.getLogger(__name__)

_FRAME_RE = re.compile(r"_Frame(\d+)(-\d+)?")
_POSITIVE_RE = re.compile(r"-positive$")


@dataclass
class FrameFile:
    """One shutter release: a source image plus the alternates beside it."""

    number: int
    source: Path
    companions: list[Path] = field(default_factory=list)


@dataclass
class Roll:
    """An opened roll folder and everything found in it."""

    path: Path
    name: str
    metadata: dict
    frames: list[FrameFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read(path: Path) -> dict:
    """Copied from Canon-EDSDK-Film-Scanner src/cefs/sidecar.py at abf739d. Port fixes by hand.

    Load a sidecar, or return an empty one. Never raises.
    """
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s); it will not be overwritten.", path, exc)
        # Not an empty dict: that reads as a fresh roll and the file gets
        # replaced, discarding a log a human might have salvaged by hand.
        return {"_unreadable": True}
    return loaded if isinstance(loaded, dict) else {"_unreadable": True}


def _source_rank(path: Path) -> int:
    """Prefer RAW, then HEIF, then the 8/16-bit direct formats."""
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        return 0
    if suffix in HEIF_EXTENSIONS:
        return 1
    return 2


def _candidate(path: Path) -> bool:
    """Whether a file is a source image belonging to the roll."""
    if not path.is_file() or path.name.startswith("."):
        return False
    if _POSITIVE_RE.search(path.stem):
        return False
    return decodable(path)


def _frame(number: int, group: list[Path]) -> FrameFile:
    ordered = sorted(group, key=lambda p: (_source_rank(p), p.name))
    return FrameFile(number=number, source=ordered[0], companions=ordered[1:])


def open_roll(path: Path) -> Roll:
    """Read a roll folder's sidecar and group its files into frames."""
    path = Path(path)
    warnings: list[str] = []

    sidecar = read(path / "roll.json")
    if sidecar.get("_unreadable"):
        metadata: dict = {}
        warnings.append(f"Could not read roll.json in {path}; using empty metadata.")
    else:
        metadata = sidecar

    files = sorted((p for p in path.iterdir() if _candidate(p)), key=lambda p: p.name)

    base_groups: dict[int, list[Path]] = {}
    suffix_groups: dict[tuple[int, str], list[Path]] = {}
    unmatched: list[Path] = []
    parsed: list[int] = []
    for item in files:
        match = _FRAME_RE.search(item.stem)
        if match is None:
            unmatched.append(item)
            continue
        number = int(match.group(1))
        parsed.append(number)
        if match.group(2):
            suffix_groups.setdefault((number, match.group(2)), []).append(item)
        else:
            base_groups.setdefault(number, []).append(item)

    next_number = (max(parsed) if parsed else 0) + 1
    frames = [_frame(number, base_groups[number]) for number in sorted(base_groups)]

    for (base, suffix), group in suffix_groups.items():
        number = next_number
        next_number += 1
        frames.append(_frame(number, group))
        for item in group:
            warnings.append(
                f"{item.name} has a uniqueness suffix (-{suffix.lstrip('-')}); "
                f"numbered as frame {number}."
            )

    for item in unmatched:
        frames.append(_frame(next_number, [item]))
        next_number += 1

    frames.sort(key=lambda f: f.number)
    return Roll(
        path=path,
        name=path.name,
        metadata=metadata,
        frames=frames,
        warnings=warnings,
    )


def _allowed_dir(path: Path) -> bool:
    return not path.name.startswith(".") and path.name != "positives"


def _is_roll_dir(path: Path) -> bool:
    if (path / "roll.json").is_file():
        return True
    try:
        return any(_candidate(item) for item in path.iterdir())
    except OSError:  # pragma: no cover - unreadable directories are environment-specific
        return False


def _newest_mtime(path: Path) -> float:
    newest = 0.0
    try:
        for item in path.iterdir():
            if item.is_file() and not item.name.startswith("."):
                newest = max(newest, item.stat().st_mtime)
    except OSError:  # pragma: no cover - unreadable directories are environment-specific
        pass
    return newest


def find_rolls(roots: list[Path]) -> list[Path]:
    """Roll folders at most two levels below any root, newest first."""
    found: dict[Path, Path] = {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir() or not _allowed_dir(root):
            continue
        level = [root]
        for _ in range(3):
            next_level: list[Path] = []
            for directory in level:
                if _is_roll_dir(directory):
                    found.setdefault(directory.resolve(), directory)
                try:
                    children = [
                        child for child in directory.iterdir()
                        if child.is_dir() and _allowed_dir(child)
                    ]
                except OSError:  # pragma: no cover - unreadable dirs are environment-specific
                    children = []
                next_level.extend(children)
            level = next_level
    return sorted(found.values(), key=_newest_mtime, reverse=True)
