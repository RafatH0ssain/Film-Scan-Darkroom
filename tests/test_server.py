"""HTTP API: every route is a thin, validated wrapper over Session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsd import __version__
from fsd.app.server import create_app
from fsd.config import Config, load_config
from fsd.darkroom.export import photos_available
from fsd.mock.synth import synth_scan
from fsd.processing.tiffio import write_tiff

METADATA = {"roll": "Roll014", "stock": "Kentmere 400", "developer": "ID-11"}


def _make_roll(tmp_path: Path, numbers=(1, 2)) -> Path:
    roll_dir = tmp_path / "Roll014"
    roll_dir.mkdir()
    (roll_dir / "roll.json").write_text(json.dumps(METADATA), encoding="utf-8")
    for n in numbers:
        image, _ = synth_scan(width=300, height=200, holder=True, seed=n)
        write_tiff(roll_dir / f"Roll014_Frame{n:02d}.tif", image)
    return roll_dir


@pytest.fixture
def app_ctx(tmp_path):
    from fsd.app.session import Session

    roll_dir = _make_roll(tmp_path)
    cfg = Config()
    cfg.library.roots = [str(tmp_path)]
    session = Session(
        cfg,
        workers=2,
        presets_path=tmp_path / "presets.yaml",
        cache_dir=tmp_path / "cache",
    )
    app = create_app(cfg, session=session)
    with TestClient(app) as client:
        response = client.post("/api/roll/open", json={"path": str(roll_dir)})
        assert response.status_code == 200
        assert session.wait_idle(20)
        yield client, session, roll_dir, tmp_path
    session.shutdown()


def test_index(app_ctx):
    client, _, _, _ = app_ctx
    response = client.get("/")
    assert response.status_code == 200
    assert "Film Scan Darkroom" in response.text


def test_status(app_ctx):
    client, _, _, _ = app_ctx
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "app", "version", "platform", "photos_available", "export", "detect",
    }
    assert data["version"] == __version__
    assert data["platform"] == sys.platform
    assert data["photos_available"] is photos_available()
    cfg = Config()
    assert set(data["export"]) == {
        "format", "tiff_compression", "jpeg_quality", "folder",
    }
    assert data["export"] == {
        "format": cfg.export.format,
        "tiff_compression": cfg.export.tiff_compression,
        "jpeg_quality": cfg.export.jpeg_quality,
        "folder": cfg.export.folder,
    }
    assert data["detect"] == {"min_confidence": cfg.detect.min_confidence}


def test_library(app_ctx):
    client, _, roll_dir, _ = app_ctx
    response = client.get("/api/library")
    assert response.status_code == 200
    entries = response.json()
    assert any(entry["path"] == str(roll_dir) and entry["frames"] == 2 for entry in entries)


def test_roll_open(app_ctx):
    client, _, roll_dir, _ = app_ctx
    response = client.post("/api/roll/open", json={"path": str(roll_dir)})
    assert response.status_code == 200
    assert response.json()["name"] == "Roll014"
    assert [frame["n"] for frame in response.json()["frames"]] == [1, 2]


def test_roll_open_not_dir_404(app_ctx):
    client, _, _, tmp_path = app_ctx
    response = client.post("/api/roll/open", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 404


def test_roll_refresh(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/roll/refresh")
    assert response.status_code == 200
    assert response.json()["name"] == "Roll014"


def test_roll_get(app_ctx):
    client, _, _, _ = app_ctx
    response = client.get("/api/roll")
    assert response.status_code == 200
    assert response.json()["name"] == "Roll014"


def test_roll_defaults(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post(
        "/api/roll/defaults", json={"film": {"contrast": 2.2}, "rotate90": 1}
    )
    assert response.status_code == 200
    frame = response.json()["frames"][0]
    assert frame["settings"]["film"]["contrast"] == 2.2
    assert frame["settings"]["rotate90"] == 1


def test_thumb(app_ctx):
    client, _, _, _ = app_ctx
    response = client.get("/api/frames/1/thumb")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content[:2] == b"\xff\xd8"


def test_preview(app_ctx):
    client, _, _, _ = app_ctx
    for view in ("positive", "negative"):
        response = client.get(f"/api/frames/1/preview?view={view}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/jpeg")
        assert response.content[:2] == b"\xff\xd8"
    assert client.get("/api/frames/1/preview").status_code == 200


def test_thumb_and_preview_no_store(app_ctx):
    client, _, _, _ = app_ctx
    for url in ("/api/frames/1/thumb", "/api/frames/1/preview"):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_edit(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post(
        "/api/frames/1/edit", json={"angle": 1.5, "film": {"contrast": 2.0}}
    )
    assert response.status_code == 200
    frame = response.json()
    assert frame["n"] == 1
    assert frame["settings"]["angle"] == 1.5
    assert frame["settings"]["film"]["contrast"] == 2.0


def test_reset(app_ctx):
    client, _, _, _ = app_ctx
    assert client.post("/api/frames/1/edit", json={"angle": 2.0}).status_code == 200
    response = client.post("/api/frames/1/reset")
    assert response.status_code == 200
    assert response.json()["n"] == 1
    assert response.json()["settings"]["angle"] != 2.0


def test_reject(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/frames/1/reject", json={"rejected": True})
    assert response.status_code == 200
    assert response.json()["rejected"] is True


def test_apply(app_ctx):
    client, _, _, _ = app_ctx
    assert client.post(
        "/api/frames/1/edit", json={"film": {"contrast": 2.4}}
    ).status_code == 200
    response = client.post("/api/apply", json={"from": 1, "to": "all"})
    assert response.status_code == 200
    frames = {frame["n"]: frame for frame in response.json()["frames"]}
    assert frames[2]["settings"]["film"]["contrast"] == 2.4


def test_presets(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/presets", json={"name": "Kentmere 400", "from": 1})
    assert response.status_code == 200
    assert "Kentmere 400" in response.json()
    listed = client.get("/api/presets")
    assert listed.status_code == 200
    assert "Kentmere 400" in listed.json()


def test_export(app_ctx):
    client, session, _, _ = app_ctx
    response = client.post("/api/export", json={"frames": [1]})
    assert response.status_code == 200
    job_id = response.json()["job"]
    assert session.wait_idle(20)
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "done"
    assert job.json()["total"] == 1
    cancelled = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "done"


def test_contact_sheet(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/contact-sheet")
    assert response.status_code == 200
    path = Path(response.json()["path"])
    assert path.is_file()


def test_unknown_frame_404(app_ctx):
    client, _, _, _ = app_ctx
    for method, url, body in (
        ("get", "/api/frames/999/thumb", None),
        ("get", "/api/frames/999/preview", None),
        ("post", "/api/frames/999/edit", {}),
        ("post", "/api/frames/999/reset", None),
        ("post", "/api/frames/999/reject", {"rejected": True}),
        ("post", "/api/frames/999/open-in-photos", None),
    ):
        response = getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, url
        assert response.json() == {"detail": "No frame 999 in this roll"}


def test_no_roll_409(tmp_path):
    from fsd.app.session import Session

    cfg = Config()
    cfg.library.roots = [str(tmp_path)]
    session = Session(
        cfg,
        workers=2,
        presets_path=tmp_path / "presets.yaml",
        cache_dir=tmp_path / "cache",
    )
    app = create_app(cfg, session=session)
    with TestClient(app) as client:
        for method, url, body in (
            ("get", "/api/roll", None),
            ("post", "/api/roll/refresh", None),
            ("post", "/api/contact-sheet", None),
            ("post", "/api/export", {"frames": "all"}),
        ):
            response = getattr(client, method)(url, **({"json": body} if body else {}))
            assert response.status_code == 409, url
            assert response.json() == {"detail": "No roll is open"}
    session.shutdown()


def test_bad_edit_422(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/frames/1/edit", json={"angle": "x"})
    assert response.status_code == 422
    assert "angle" in response.text


def test_bad_edit_unknown_film_key_422(app_ctx):
    client, _, _, _ = app_ctx
    response = client.post("/api/frames/1/edit", json={"film": {"bogus": 1}})
    assert response.status_code == 422
    assert "bogus" in response.text


def test_bad_crop_422(app_ctx):
    client, _, _, _ = app_ctx
    for crop in ([0.1, 0.1, 0.9], [0.8, 0.1, 0.2, 0.9], [0.0, 0.0, 1.1, 1.0]):
        response = client.post("/api/frames/1/edit", json={"crop": crop})
        assert response.status_code == 422


def test_bad_angle_range_422(app_ctx):
    client, _, _, _ = app_ctx
    assert client.post("/api/frames/1/edit", json={"angle": 46}).status_code == 422
    assert client.post("/api/frames/1/edit", json={"rotate90": 4}).status_code == 422


def test_open_in_photos_409_off_mac(app_ctx, monkeypatch):
    client, _, _, _ = app_ctx
    monkeypatch.setattr(sys, "platform", "linux")
    response = client.post("/api/frames/1/open-in-photos")
    assert response.status_code == 409


def test_binds_loopback_by_default():
    assert load_config().server.host == "127.0.0.1"
