"""Write finished positives, a contact sheet, and hand a positive to Photos.

Every export goes through the same :func:`fsd.processing.render.render` the
preview uses, so what was reviewed is what lands on disk. Nothing in a roll
folder is touched except ``darkroom.json`` and files under ``positives/``.
"""

from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

from fsd.config import Config
from fsd.darkroom.edits import Edits, FrameEdit, effective, save as save_edits, settings_hash
from fsd.darkroom.roll import FrameFile, Roll
from fsd.naming import unique
from fsd.processing.metadata import SOFTWARE, describe
from fsd.processing.render import render
from fsd.processing.source import decode
from fsd.processing.tiffio import write_tiff

CELL = 400
LABEL_HEIGHT = 30
COLUMNS = 6

_FORMAT_EXTENSIONS = {
    "tiff": (("tif", ".tif"),),
    "jpeg": (("jpg", ".jpg"),),
    "both": (("tif", ".tif"), ("jpg", ".jpg")),
}


@dataclass
class ExportResult:
    """One frame's export: the files written, or the reason none were."""

    frame: int
    files: list[Path] = field(default_factory=list)
    error: str | None = None


def _frame_file(roll: Roll, n: int) -> FrameFile | None:
    return next((f for f in roll.frames if f.number == n), None)


def _relative(roll: Roll, path: Path) -> str:
    """Path as recorded in darkroom.json, relative to the roll folder."""
    return path.relative_to(roll.path).as_posix()


def _formats(cfg: Config):
    return _FORMAT_EXTENSIONS.get(cfg.export.format, _FORMAT_EXTENSIONS["tiff"])


def _encode_description(text: str) -> bytes:
    """EXIF and TIFF ImageDescription are nominally 7-bit ASCII fields.

    The ``·`` separator in :func:`fsd.processing.metadata.describe` is Latin-1,
    which is what Pillow decodes these tags with. Encode it to bytes so the
    description round-trips byte-for-byte instead of becoming ``?``.
    """
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        return text.encode("utf-8")


def _target(roll: Roll, source: Path, extension: str, key: str, recorded: str | None, cfg: Config) -> Path:
    """The path to write: reuse our own recorded name, never a foreign file."""
    folder = roll.path / cfg.export.folder
    target = folder / f"{source.stem}{extension}"
    if target.exists() and recorded != _relative(roll, target):
        target = unique(target)
    return target


def export_frame(
    roll: Roll, edits: Edits, n: int, cfg: Config, preset: dict | None
) -> ExportResult:
    """Export one frame in the configured format(s) and record where it went.

    A file already at the default name is only overwritten if this very frame
    recorded it as its own; otherwise :func:`fsd.naming.unique` adds ``-1``,
    ``-2`` and so on. ``edits`` is updated in place; saving it is the caller's
    job (see :func:`export_frames`).
    """
    frame = _frame_file(roll, n)
    if frame is None:
        return ExportResult(n, error=f"Frame {n} not found in roll {roll.name}.")

    source = frame.source
    edit = edits.frames.get(n)
    if edit is None:
        edit = FrameEdit(source=source.name)
        edits.frames[n] = edit

    files: list[Path] = []
    try:
        settings = effective(edits, n, preset, cfg)
        digest = settings_hash(settings)
        image = render(decode(source), settings)
        bw = settings.film.mode == "bw"
        description = describe(roll, n)

        (roll.path / cfg.export.folder).mkdir(parents=True, exist_ok=True)
        exported: dict[str, str] = {}
        for key, extension in _formats(cfg):
            target = _target(
                roll, source, extension, key, edit.exported.get(key), cfg
            )
            if key == "tif":
                write_tiff(
                    target,
                    image[..., 0] if bw else image,
                    compression=cfg.export.tiff_compression,
                    description=_encode_description(description),
                    software=SOFTWARE,
                )
            else:
                exif = Image.Exif()
                exif[0x010E] = _encode_description(description)
                exif[0x0131] = SOFTWARE
                eight_bit = (image >> 8).astype(np.uint8)
                if bw:
                    eight_bit = eight_bit[..., 0]
                Image.fromarray(eight_bit, "L" if bw else "RGB").save(
                    target, quality=cfg.export.jpeg_quality, exif=exif
                )
            exported[key] = _relative(roll, target)
            files.append(target)

        exported["at"] = datetime.now().isoformat(timespec="seconds")
        exported["settings_hash"] = digest
        edit.exported = exported
    except Exception as exc:  # one bad frame must not stop the batch
        return ExportResult(n, files=files, error=str(exc))
    return ExportResult(n, files=files)


def export_frames(
    roll: Roll,
    edits: Edits,
    frames: list[int],
    cfg: Config,
    preset: dict | None,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[ExportResult]:
    """Export ``frames`` in order, skipping rejected ones and saving as we go.

    Each frame is independent: a failure is recorded on its result and the loop
    continues. The sidecar is saved only after a success, so a failed export
    never records a path to a file that does not exist.
    """
    results: list[ExportResult] = []
    total = len(frames)
    for done, n in enumerate(frames, 1):
        if cancelled():
            break
        edit = edits.frames.get(n)
        if edit is None or not edit.rejected:
            result = export_frame(roll, edits, n, cfg, preset)
            if result.error is None:
                save_edits(roll.path, edits)
            results.append(result)
        if progress is not None:
            progress(done, total)
    return results


def _thumbnail(
    roll: Roll,
    edits: Edits,
    frame: FrameFile,
    thumbs: dict[int, Path],
    cfg: Config,
) -> Image.Image:
    """A small RGB image fitted inside a cell, from a supplied JPEG or a render."""
    path = thumbs.get(frame.number)
    if path is not None:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
            image.thumbnail((CELL, CELL - LABEL_HEIGHT))
            return image.copy()

    settings = effective(edits, frame.number, None, cfg)
    rendered = render(decode(frame.source, half=True), settings, scale=0.5)
    image = Image.fromarray((rendered >> 8).astype(np.uint8), "RGB")
    image.thumbnail((CELL, CELL - LABEL_HEIGHT))
    return image


def contact_sheet(
    roll: Roll, edits: Edits, thumbs: dict[int, Path], cfg: Config
) -> Path:
    """Build ``positives/<roll>_contact.jpg``: 400 px cells, six to a row.

    Rejected frames are left out. A frame with no supplied thumbnail is
    rendered from its half-size decode so the sheet always shows the whole roll.
    """
    active = [
        f for f in roll.frames
        if (edits.frames.get(f.number) is None or not edits.frames[f.number].rejected)
    ]
    rows = max(1, math.ceil(len(active) / COLUMNS))
    sheet = Image.new("RGB", (COLUMNS * CELL, rows * CELL), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(active):
        thumb = _thumbnail(roll, edits, frame, thumbs, cfg)
        x = (index % COLUMNS) * CELL + (CELL - thumb.width) // 2
        y = (index // COLUMNS) * CELL + (CELL - LABEL_HEIGHT - thumb.height) // 2
        sheet.paste(thumb, (x, y))
        label = str(frame.number)
        box = draw.textbbox((0, 0), label)
        width = box[2] - box[0]
        draw.text(
            ((index % COLUMNS) * CELL + (CELL - width) // 2,
             (index // COLUMNS) * CELL + CELL - LABEL_HEIGHT + 6),
            label,
            fill=(255, 255, 255),
        )

    folder = roll.path / cfg.export.folder
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{roll.name}_contact.jpg"
    sheet.save(path, quality=cfg.export.jpeg_quality)
    return path


def photos_available() -> bool:
    """Open in Photos uses the macOS ``open`` command; it is hidden elsewhere."""
    return sys.platform == "darwin"


def open_in_photos(path: Path) -> None:
    """Hand a positive to the Photos app. Photos imports a copy of it."""
    if not photos_available():
        raise RuntimeError("Open in Photos is only available on macOS.")
    subprocess.run(["open", "-a", "Photos", str(path)], check=True)
