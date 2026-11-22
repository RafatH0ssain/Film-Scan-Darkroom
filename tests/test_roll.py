"""Roll discovery, frame grouping and the roll library."""

from __future__ import annotations

import os

from fsd.darkroom.roll import find_rolls, open_roll


def _touch(path):
    path.write_bytes(b"")
    return path


def test_raw_wins_over_jpeg_pair(tmp_path):
    _touch(tmp_path / "Roll001_Frame07.CR3")
    _touch(tmp_path / "Roll001_Frame07.JPG")

    roll = open_roll(tmp_path)

    assert len(roll.frames) == 1
    frame = roll.frames[0]
    assert frame.number == 7
    assert frame.source.suffix.lower() == ".cr3"
    assert [p.suffix.lower() for p in frame.companions] == [".jpg"]


def test_positive_files_ignored(tmp_path):
    _touch(tmp_path / "Roll001_Frame07.CR3")
    _touch(tmp_path / "Roll001_Frame07-positive.CR3")
    _touch(tmp_path / "Roll001_Frame07-positive.tif")
    positives = tmp_path / "positives"
    positives.mkdir()
    _touch(positives / "Roll001_Frame07.tif")

    roll = open_roll(tmp_path)

    assert len(roll.frames) == 1
    assert roll.frames[0].source.name == "Roll001_Frame07.CR3"


def test_unmatched_names_follow(tmp_path):
    _touch(tmp_path / "IMG_0002.CR3")
    _touch(tmp_path / "Roll001_Frame07.CR3")
    _touch(tmp_path / "IMG_0001.JPG")

    roll = open_roll(tmp_path)

    assert [f.number for f in roll.frames] == [7, 8, 9]
    assert [f.source.name for f in roll.frames] == [
        "Roll001_Frame07.CR3",
        "IMG_0001.JPG",
        "IMG_0002.CR3",
    ]


def test_duplicate_suffix_gets_own_frame_and_warning(tmp_path):
    _touch(tmp_path / "Roll001_Frame01.CR3")
    _touch(tmp_path / "Roll001_Frame01-1.CR3")

    roll = open_roll(tmp_path)

    assert [f.number for f in roll.frames] == [1, 2]
    assert any("Roll001_Frame01-1.CR3" in w for w in roll.warnings)


def test_corrupt_roll_json_opens_with_warning(tmp_path):
    (tmp_path / "roll.json").write_text("{not json", encoding="utf-8")
    _touch(tmp_path / "Roll001_Frame01.CR3")

    roll = open_roll(tmp_path)

    assert roll.metadata == {}
    assert roll.warnings
    assert len(roll.frames) == 1


def test_find_rolls_orders_newest_first(tmp_path):
    root = tmp_path / "captures"
    root.mkdir()
    old = root / "Roll001"
    new = root / "Roll002"
    old.mkdir()
    new.mkdir()
    old_file = _touch(old / "Roll001_Frame01.CR3")
    new_file = _touch(new / "Roll002_Frame01.CR3")
    os.utime(old_file, (1000, 1000))
    os.utime(new_file, (2000, 2000))
    skipped_positive = root / "positives"
    skipped_positive.mkdir()
    _touch(skipped_positive / "x.JPG")
    skipped_dot = root / ".hidden"
    skipped_dot.mkdir()
    _touch(skipped_dot / "x.JPG")

    rolls = find_rolls([root])

    assert rolls == [new, old]


def test_missing_root_is_skipped(tmp_path):
    assert find_rolls([tmp_path / "does-not-exist"]) == []
