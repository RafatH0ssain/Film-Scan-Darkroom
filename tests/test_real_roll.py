"""End-to-end check against a real scan roll.

Set ``FSD_REAL_ROLL`` to a roll folder to run this; it is skipped otherwise.
The roll is copied to ``tmp_path`` before anything is opened, so the original
scans and their sidecar are never touched -- analysis writes ``darkroom.json``
and the exported positive into the copy.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from fsd.app.session import Session
from fsd.config import Config
from fsd.processing.source import decodable

REAL_ROLL = os.environ.get("FSD_REAL_ROLL")

pytestmark = pytest.mark.skipif(
    not REAL_ROLL, reason="set FSD_REAL_ROLL to a roll folder to run this"
)


@pytest.fixture
def roll_copy(tmp_path: Path) -> Path:
    """Copy the roll's sources and roll.json; never open the original for writing."""
    source = Path(REAL_ROLL).expanduser()
    destination = tmp_path / source.name
    destination.mkdir()
    copied = 0
    for item in sorted(source.iterdir()):
        if item.is_file() and (item.name == "roll.json" or decodable(item)):
            shutil.copy2(item, destination / item.name)
            copied += 1
    assert copied > 0, f"no source files or roll.json found in {source}"
    return destination


def test_real_roll_detects(roll_copy: Path, tmp_path: Path):
    cfg = Config()
    cfg.library.roots = [str(tmp_path)]
    session = Session(
        cfg,
        presets_path=tmp_path / "presets.yaml",
        cache_dir=tmp_path / "cache",
    )
    try:
        state = session.open(roll_copy)
        assert state["frames"], "no frames found in the roll"
        assert session.wait_idle(600), "analysis did not finish within 600 s"

        frames = session.state()["frames"]
        print("\nPer-frame confidences:")
        for frame in frames:
            print(
                f"  frame {frame['n']}: confidence={frame['confidence']} "
                f"status={frame['status']} flagged={frame['flagged']}"
            )

        assert all(frame["status"] == "ready" for frame in frames)
        clean = sum(1 for frame in frames if not frame["flagged"])
        assert clean >= 0.8 * len(frames), f"only {clean}/{len(frames)} frames passed"

        job_id = session.start_export([frames[0]["n"]])
        assert session.wait_idle(600), "export did not finish within 600 s"
        job = session.job(job_id)
        assert job["status"] == "done", job
        written = job["results"][0]["files"]
        assert written, "the export wrote no files"
        assert (roll_copy / written[0]).is_file(), written
    finally:
        session.shutdown()
