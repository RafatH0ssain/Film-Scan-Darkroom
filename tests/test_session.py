"""Session: open a roll, analyse in the background, cache previews."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

import fsd.app.session as session_mod
from fsd.app.session import Session
from fsd.config import Config
from fsd.darkroom.edits import SIDECAR, load
from fsd.mock.synth import synth_scan
from fsd.processing.tiffio import write_tiff

METADATA = {"roll": "Roll014", "stock": "Kentmere 400", "developer": "ID-11"}


def _write_frame(roll_dir: Path, n: int, *, blank: bool = False, holder: bool = True,
                 seed: int | None = None, size=(300, 200)) -> Path:
    image, _ = synth_scan(
        width=size[0], height=size[1], holder=holder, blank=blank,
        seed=n if seed is None else seed,
    )
    path = roll_dir / f"Roll014_Frame{n:02d}.tif"
    write_tiff(path, image)
    return path


def _make_roll(tmp_path: Path, numbers=(1,)) -> Path:
    roll_dir = tmp_path / "Roll014"
    roll_dir.mkdir()
    (roll_dir / "roll.json").write_text(json.dumps(METADATA), encoding="utf-8")
    for n in numbers:
        _write_frame(roll_dir, n)
    return roll_dir


@pytest.fixture
def make_session(tmp_path):
    sessions = []

    def build(roll_dir):
        cfg = Config()
        cfg.library.roots = [str(tmp_path)]
        session = Session(
            cfg,
            workers=2,
            presets_path=tmp_path / "presets.yaml",
            cache_dir=tmp_path / "cache",
        )
        sessions.append(session)
        return session

    yield build
    for session in sessions:
        session.shutdown()


def test_open_analyses_all_frames(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1, 2, 3))
    session = make_session(roll_dir)

    state = session.open(roll_dir)
    assert state["analysing"]["total"] == 3

    assert session.wait_idle(20)
    state = session.state()
    assert state["analysing"] == {"done": 3, "total": 3}
    frames = {f["n"]: f for f in state["frames"]}
    assert set(frames) == {1, 2, 3}
    assert all(f["status"] == "ready" for f in frames.values())

    edits = load(roll_dir)
    assert all(edits.frames[n].auto is not None for n in (1, 2, 3))


def test_blank_frame_flagged(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1,))
    _write_frame(roll_dir, 1, seed=1, size=(600, 400))
    _write_frame(roll_dir, 2, blank=True, holder=False, seed=99, size=(600, 400))
    session = make_session(roll_dir)

    session.open(roll_dir)
    assert session.wait_idle(20)
    frames = {f["n"]: f for f in session.state()["frames"]}

    assert frames[1]["flagged"] is False
    assert frames[2]["flagged"] is True
    assert frames[2]["confidence"] < Config().detect.min_confidence


def test_refresh_picks_up_new_and_changed(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1, 2))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)
    before = load(roll_dir).frames[1].fingerprint

    changed = roll_dir / "Roll014_Frame01.tif"
    image, _ = synth_scan(width=300, height=200, holder=True, seed=101)
    write_tiff(changed, image)
    stat = changed.stat()
    os.utime(changed, (stat.st_atime, stat.st_mtime + 10))
    _write_frame(roll_dir, 3, seed=103)

    session.refresh()
    assert session.wait_idle(20)

    edits = load(roll_dir)
    assert set(edits.frames) == {1, 2, 3}
    now = changed.stat()
    assert edits.frames[1].fingerprint != before
    assert edits.frames[1].fingerprint == (now.st_mtime_ns, now.st_size)
    frames = {f["n"]: f for f in session.state()["frames"]}
    assert frames[3]["status"] == "ready"
    assert frames[3]["confidence"] is not None


def test_edit_persists_across_reopen(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1,))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)

    frame = session.edit(1, {"angle": 1.25, "film": {"contrast": 2.0}})
    assert frame["settings"]["angle"] == 1.25
    assert frame["settings"]["film"]["contrast"] == 2.0
    session.shutdown()

    reopened = make_session(roll_dir)
    reopened.open(roll_dir)
    assert reopened.wait_idle(20)
    frame = {f["n"]: f for f in reopened.state()["frames"]}[1]
    assert frame["settings"]["angle"] == 1.25
    assert frame["settings"]["film"]["contrast"] == 2.0


def test_apply_to_all(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1, 2, 3))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)

    session.edit(1, {"film": {"contrast": 2.3}, "rotate90": 1})
    session.apply(1, "all", None)

    edits = load(roll_dir)
    for n in (2, 3):
        assert edits.frames[n].user["film"]["contrast"] == 2.3
        assert edits.frames[n].user["rotate90"] == 1
        assert "crop" not in edits.frames[n].user


def test_export_job_progress_and_result(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1, 2, 3))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)

    job_id = session.start_export("all")
    assert len(job_id) == 32

    seen = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = session.job(job_id)
        seen.append(job["done"])
        if job["status"] != "running":
            break
        time.sleep(0.01)

    assert session.wait_idle(20)
    job = session.job(job_id)
    assert job["kind"] == "export"
    assert job["status"] == "done"
    assert job["total"] == 3
    assert job["done"] == 3
    assert all(0 <= d <= 3 for d in seen)
    assert [r["frame"] for r in job["results"]] == [1, 2, 3]
    for result in job["results"]:
        assert result["error"] is None
        assert len(result["files"]) == 1
        relative = result["files"][0]
        assert relative.startswith("positives/") and relative.endswith(".tif")
        assert (roll_dir / relative).is_file()

    frames = {f["n"]: f for f in session.state()["frames"]}
    assert all(f["exported_current"] is True for f in frames.values())


def test_cancel_export(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, tuple(range(1, 11)))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)

    job_id = session.start_export("all")
    session.cancel(job_id)

    assert session.wait_idle(20)
    assert session.job(job_id)["status"] == "cancelled"


def test_preview_is_jpeg_of_expected_size(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1,))
    session = make_session(roll_dir)
    session.open(roll_dir)
    assert session.wait_idle(20)

    sizes = {}
    for view in ("positive", "negative"):
        data = session.preview(1, view)
        assert data[:2] == b"\xff\xd8"
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == "JPEG"
            assert 0 < max(image.size) <= 1600
            sizes[view] = image.size

    assert sizes["negative"] == (150, 100)
    assert sizes["positive"][0] <= 150 and sizes["positive"][1] <= 100

    thumb = session.thumb(1)
    assert thumb[:2] == b"\xff\xd8"
    with Image.open(io.BytesIO(thumb)) as image:
        assert image.format == "JPEG"
        assert 0 < max(image.size) <= 320
        assert image.size == sizes["positive"]

    assert len(list((tmp_path / "cache").glob("*.jpg"))) == 1


def test_corrupt_darkroom_json_warns(tmp_path, make_session):
    roll_dir = _make_roll(tmp_path, (1,))
    (roll_dir / SIDECAR).write_text("{not json", encoding="utf-8")
    session = make_session(roll_dir)

    state = session.open(roll_dir)

    assert state["warnings"]
    assert any("darkroom.json" in warning for warning in state["warnings"])
    assert session.wait_idle(20)


def test_switching_roll_drops_stale_detections(tmp_path, make_session, monkeypatch):
    gate = threading.Event()
    real_detect = session_mod.detect

    def gated_detect(linear, *, aspect, margin):
        gate.wait(10)
        return real_detect(linear, aspect=aspect, margin=margin)

    monkeypatch.setattr(session_mod, "detect", gated_detect)

    def named_roll(name, numbers):
        roll_dir = tmp_path / name
        roll_dir.mkdir()
        (roll_dir / "roll.json").write_text(json.dumps(METADATA), encoding="utf-8")
        for n in numbers:
            image, _ = synth_scan(width=300, height=200, holder=True, seed=n)
            write_tiff(roll_dir / f"{name}_Frame{n:02d}.tif", image)
        return roll_dir

    roll_a = named_roll("RollA", (1, 2, 3))
    roll_b = named_roll("RollB", (1, 2))
    b_sources = {"RollB_Frame01.tif", "RollB_Frame02.tif"}

    session = make_session(roll_a)
    session.open(roll_a)
    session.open(roll_b)
    gate.set()

    assert session.wait_idle(20)
    state = session.state()
    assert {frame["source"] for frame in state["frames"]} == b_sources
    assert state["analysing"] == {"done": 2, "total": 2}
    assert (roll_b / SIDECAR).is_file()

    saved = json.loads((roll_b / SIDECAR).read_text(encoding="utf-8"))
    sources = {frame["source"] for frame in saved["frames"].values()}
    assert sources == b_sources
    assert all(not source.startswith("RollA") for source in sources)
