"""Export, metadata, contact sheet and Open in Photos."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

import fsd.darkroom.export as export_mod
from fsd.config import Config
from fsd.darkroom.edits import Edits, FrameEdit, effective, settings_hash
from fsd.darkroom.export import (
    SOFTWARE,
    contact_sheet,
    export_frame,
    export_frames,
    open_in_photos,
    photos_available,
)
from fsd.darkroom.roll import open_roll
from fsd.mock.synth import synth_scan
from fsd.processing.metadata import describe
from fsd.processing.tiffio import write_tiff

METADATA = {
    "roll": "Roll014",
    "stock": "Kentmere 400",
    "developer": "ID-11",
    "notes": "push one stop",
    "date": "2026-11-02",
}


def _make_roll(tmp_path: Path, numbers=(1,), *, metadata=None) -> tuple[Path, object]:
    """Build a synth roll in tmp_path and return (roll_dir, opened Roll)."""
    roll_dir = tmp_path / "Roll014"
    roll_dir.mkdir()
    for n in numbers:
        image, _ = synth_scan(width=300, height=200, holder=True, seed=n)
        write_tiff(roll_dir / f"Roll014_Frame{n:02d}.tif", image)
    (roll_dir / "roll.json").write_text(
        json.dumps(METADATA if metadata is None else metadata), encoding="utf-8"
    )
    return roll_dir, open_roll(roll_dir)


def _hash_tree(roll_dir: Path) -> dict[str, str]:
    """Content hashes of every file except positives/ and the darkroom sidecar."""
    result = {}
    for path in sorted(roll_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(roll_dir)
        if rel.parts[0] == "positives" or path.name.startswith("darkroom.json"):
            continue
        result[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_export_writes_positive_and_records(tmp_path):
    _, roll = _make_roll(tmp_path)
    edits = Edits()
    cfg = Config()
    cfg.film.mode = "color"

    result = export_frame(roll, edits, 1, cfg, None)

    assert result.error is None
    assert len(result.files) == 1
    out = result.files[0]
    assert out.is_file()
    assert out.parent.name == "positives"
    assert out.name == "Roll014_Frame01.tif"
    assert read_rgb(out).shape[-1] == 3
    exported = edits.frames[1].exported
    assert exported["tif"] == "positives/Roll014_Frame01.tif"
    assert "at" in exported
    assert exported["settings_hash"] == settings_hash(effective(edits, 1, None, cfg))


def test_bw_export_tiff_is_single_channel(tmp_path):
    _, roll = _make_roll(tmp_path)
    cfg = Config()
    cfg.film.mode = "bw"

    result = export_frame(roll, Edits(), 1, cfg, None)

    assert result.error is None
    image = read_rgb(result.files[0])
    assert image.ndim == 2
    assert image.dtype == np.uint16


def test_color_export_tiff_is_rgb(tmp_path):
    _, roll = _make_roll(tmp_path)
    cfg = Config()
    cfg.film.mode = "color"

    result = export_frame(roll, Edits(), 1, cfg, None)

    assert result.error is None
    image = read_rgb(result.files[0])
    assert image.shape == (200, 300, 3)


def test_export_never_touches_sources(tmp_path):
    roll_dir, roll = _make_roll(tmp_path, numbers=(1, 2))
    before = _hash_tree(roll_dir)

    results = export_frames(roll, Edits(), [1, 2], Config(), None)

    assert all(r.error is None for r in results)
    assert _hash_tree(roll_dir) == before


def test_existing_foreign_file_not_overwritten(tmp_path):
    roll_dir, roll = _make_roll(tmp_path)
    positives = roll_dir / "positives"
    positives.mkdir()
    foreign = positives / "Roll014_Frame01.tif"
    foreign.write_bytes(b"foreign content, not ours")

    edits = Edits()
    result = export_frame(roll, edits, 1, Config(), None)

    assert result.error is None
    assert foreign.read_bytes() == b"foreign content, not ours"
    assert result.files[0].name == "Roll014_Frame01-1.tif"
    assert edits.frames[1].exported["tif"] == "positives/Roll014_Frame01-1.tif"


def test_reexport_overwrites_own_file(tmp_path):
    roll_dir, roll = _make_roll(tmp_path)
    edits = Edits()
    cfg = Config()

    first = export_frame(roll, edits, 1, cfg, None)
    own = first.files[0]
    own.write_bytes(b"sentinel")
    second = export_frame(roll, edits, 1, cfg, None)

    assert second.error is None
    assert second.files[0] == own
    assert own.read_bytes() != b"sentinel"
    assert not (roll_dir / "positives" / "Roll014_Frame01-1.tif").exists()


def test_rejected_skipped(tmp_path):
    roll_dir, roll = _make_roll(tmp_path, numbers=(1, 2))
    edits = Edits(
        frames={1: FrameEdit(source="Roll014_Frame01.tif", rejected=True)}
    )

    results = export_frames(roll, edits, [1, 2], Config(), None)

    assert [r.frame for r in results] == [2]
    assert not (roll_dir / "positives" / "Roll014_Frame01.tif").exists()
    assert (roll_dir / "positives" / "Roll014_Frame02.tif").is_file()


def test_failure_continues(tmp_path, monkeypatch):
    roll_dir, roll = _make_roll(tmp_path, numbers=(1, 2, 3))
    (roll_dir / "Roll014_Frame02.tif").unlink()

    real_write = export_mod.write_tiff

    def fail_on_three(path, image, **kwargs):
        if "Frame03" in str(path):
            raise OSError("simulated read-only folder")
        return real_write(path, image, **kwargs)

    monkeypatch.setattr(export_mod, "write_tiff", fail_on_three)

    seen = []
    results = export_frames(
        roll,
        Edits(),
        [1, 2, 3],
        Config(),
        None,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert [r.frame for r in results] == [1, 2, 3]
    assert results[0].error is None and results[0].files
    assert results[1].error and not results[1].files
    assert results[2].error and not results[2].files
    assert seen == [(1, 3), (2, 3), (3, 3)]
    # The sidecar is only saved after a success, so frame 2/3 were not recorded.
    saved = json.loads((roll_dir / "darkroom.json").read_text(encoding="utf-8"))
    assert "1" in saved["frames"]
    assert "2" not in saved["frames"]
    assert "3" not in saved["frames"]


def test_tiff_metadata(tmp_path):
    _, roll = _make_roll(tmp_path)
    expected = describe(roll, 1)

    result = export_frame(roll, Edits(), 1, Config(), None)

    with tifffile.TiffFile(result.files[0]) as tif:
        assert tif.pages[0].description == expected
        assert tif.pages[0].software == SOFTWARE


def test_jpeg_exif_description(tmp_path):
    _, roll = _make_roll(tmp_path)
    cfg = Config()
    cfg.export.format = "jpeg"
    edits = Edits()
    expected = describe(roll, 1)

    result = export_frame(roll, edits, 1, cfg, None)

    assert result.error is None
    assert result.files[0].suffix == ".jpg"
    assert edits.frames[1].exported["jpg"] == "positives/Roll014_Frame01.jpg"
    with Image.open(result.files[0]) as img:
        exif = img.getexif()
        assert exif[0x010E] == expected
        assert exif[0x0131] == SOFTWARE


def test_contact_sheet_dims(tmp_path):
    roll_dir, roll = _make_roll(tmp_path, numbers=range(1, 8))
    edits = Edits()
    cfg = Config()

    path = contact_sheet(roll, edits, {}, cfg)

    assert path == roll_dir / "positives" / "Roll014_contact.jpg"
    with Image.open(path) as img:
        assert img.size == (6 * 400, 2 * 400)

    edits.frames[1] = FrameEdit(source="Roll014_Frame01.tif", rejected=True)
    path = contact_sheet(roll, edits, {}, cfg)
    with Image.open(path) as img:
        assert img.size == (6 * 400, 1 * 400)


def test_open_in_photos_off_mac_raises(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert photos_available() is False
    with pytest.raises(RuntimeError):
        open_in_photos(Path("positive.tif"))


def test_open_in_photos_calls_open(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = Path("/tmp/positive.tif")

    assert photos_available() is True
    open_in_photos(path)

    assert calls == [(["open", "-a", "Photos", str(path)], {"check": True})]


def read_rgb(path: Path) -> np.ndarray:
    return tifffile.imread(str(path))


def test_export_to_folder_outside_roll(tmp_path):
    _, roll = _make_roll(tmp_path)
    edits = Edits()
    cfg = Config()
    library = tmp_path / "Pictures" / "Film"
    cfg.export.folder = str(library)

    first = export_frame(roll, edits, 1, cfg, None)
    assert first.error is None
    assert first.files == [library / "Roll014_Frame01.tif"]
    assert first.files[0].is_file()
    recorded = edits.frames[1].exported["tif"]
    assert roll.path / recorded == first.files[0].resolve()

    # Re-export recognises its own file and overwrites it, no -1 copy.
    second = export_frame(roll, edits, 1, cfg, None)
    assert second.error is None
    assert sorted(p.name for p in library.iterdir()) == ["Roll014_Frame01.tif"]
