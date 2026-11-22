"""Edits sidecar: load, save, effective settings and apply-to."""

from __future__ import annotations

import json
import os
import threading

import pytest

from fsd.config import Config
from fsd.darkroom.edits import (
    SIDECAR,
    Edits,
    FrameEdit,
    apply,
    effective,
    load,
    needs_detection,
    save,
    settings_hash,
)
from fsd.processing.detect import Detection
from fsd.processing.render import FrameSettings
from fsd.processing.sharpen import SharpenParams


def _detection():
    return Detection(
        crop=(0.1, 0.2, 0.8, 0.9),
        angle=0.4,
        base_region=(0.0, 0.0, 0.1, 0.1),
        confidence=0.93,
        reasons=["ok"],
    )


def test_round_trip(tmp_path):
    exported = {"tif": "positives/x.tif", "at": "2026-11-21T00:00:00", "settings_hash": "abc"}
    edits = Edits(
        roll_defaults={
            "film": {"contrast": 1.6},
            "sharpen": {"amount": 0.5},
            "mirror": True,
            "rotate90": 1,
        },
        preset="Kentmere 400",
        frames={
            7: FrameEdit(
                source="Roll014_Frame07.CR3",
                fingerprint=(123, 456),
                auto=_detection(),
                user={
                    "crop": (0.2, 0.3, 0.7, 0.8),
                    "angle": None,
                    "base_region": None,
                    "film": {"exposure": 1.2},
                    "sharpen": None,
                },
                rejected=True,
                exported=exported,
            )
        },
    )

    save(tmp_path, edits)
    loaded = load(tmp_path)

    assert loaded.roll_defaults == edits.roll_defaults
    assert loaded.preset == edits.preset
    assert list(loaded.frames) == [7]
    frame = loaded.frames[7]
    assert frame.source == "Roll014_Frame07.CR3"
    assert frame.fingerprint == (123, 456)
    assert frame.auto == _detection()
    assert frame.user["crop"] == (0.2, 0.3, 0.7, 0.8)
    assert frame.user["film"] == {"exposure": 1.2}
    assert frame.rejected is True
    assert frame.exported == exported

    raw = json.loads((tmp_path / SIDECAR).read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert list(raw["frames"]) == ["7"]
    assert raw["frames"]["7"]["auto"]["crop"] == [0.1, 0.2, 0.8, 0.9]
    assert raw["frames"]["7"]["user"]["crop"] == [0.2, 0.3, 0.7, 0.8]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_save_uses_umask_mode(tmp_path, monkeypatch):
    # The umask is read once at import (it is process-wide); pin it here.
    import fsd.darkroom.edits as edits_module

    monkeypatch.setattr(edits_module, "_UMASK", 0o027)
    save(tmp_path, Edits())

    mode = (tmp_path / SIDECAR).stat().st_mode & 0o777
    assert mode == 0o666 & ~0o027


def test_save_is_atomic(tmp_path, monkeypatch):
    original = "do not lose me"
    (tmp_path / SIDECAR).write_text(original, encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        save(tmp_path, Edits())

    assert (tmp_path / SIDECAR).read_text(encoding="utf-8") == original


def test_corrupt_sidecar_renamed_and_warned(tmp_path):
    (tmp_path / SIDECAR).write_text("{not json", encoding="utf-8")

    edits = load(tmp_path)

    assert edits.frames == {}
    assert edits.warnings
    bads = list(tmp_path.glob("darkroom.json.bad-*"))
    assert len(bads) == 1
    assert bads[0].read_text(encoding="utf-8") == "{not json"
    assert not (tmp_path / SIDECAR).exists()


def test_needs_detection_on_changed_fingerprint(tmp_path):
    source = tmp_path / "img.CR3"
    source.write_bytes(b"abcd")
    stat = source.stat()
    fp = (stat.st_mtime_ns, stat.st_size)
    edit = FrameEdit(source=source.name, fingerprint=fp, auto=_detection())

    assert needs_detection(edit, source) is False
    assert needs_detection(None, source) is True
    assert needs_detection(FrameEdit(source=source.name, auto=_detection()), source) is True

    source.write_bytes(b"abcdef")
    os.utime(source, (stat.st_atime, stat.st_mtime + 10))
    assert needs_detection(edit, source) is True


def test_effective_precedence(tmp_path):
    cfg = Config()
    cfg.film.mode = "bw"
    edits = Edits(
        roll_defaults={"film": {"contrast": 1.6}},
        frames={
            7: FrameEdit(source="img.CR3", auto=_detection(), user={"film": {"contrast": 1.7}})
        },
    )
    preset = {"film": {"contrast": 1.5}}

    s = effective(edits, 7, preset, cfg)
    assert s.film.contrast == 1.7
    assert s.crop == (0.1, 0.2, 0.8, 0.9)
    assert s.angle == 0.4
    assert s.base_region == (0.0, 0.0, 0.1, 0.1)

    edits.frames[7].user.pop("film")
    assert effective(edits, 7, preset, cfg).film.contrast == 1.6

    edits.roll_defaults["film"].pop("contrast")
    s = effective(edits, 7, preset, cfg)
    assert s.film.contrast == 1.5
    assert s.film.mode == "bw"


def test_effective_merges_partial_film_and_sharpen():
    cfg = Config()
    edits = Edits(
        roll_defaults={
            "film": {"contrast": 1.6, "exposure": 1.1},
            "sharpen": {"amount": 0.5, "radius": 2.0},
            "mirror": True,
            "rotate90": 1,
        },
        frames={1: FrameEdit(source="img.CR3", user={
            "film": {"contrast": 1.7},
            "sharpen": {"amount": 2.0},
            "mirror": False,
        })},
    )

    s = effective(edits, 1, None, cfg)

    assert s.film.contrast == 1.7
    assert s.film.exposure == 1.1
    assert s.film.base is None
    assert s.sharpen.amount == 2.0
    assert s.sharpen.radius == 2.0
    assert s.mirror is False
    assert s.rotate90 == 1


def test_effective_ignores_base_and_unknown_preset_fields():
    edits = Edits()
    preset = {"film": {"base": (0.1, 0.1, 0.1), "contrasts": 2.0, "contrast": 1.5}}

    s = effective(edits, 1, preset, Config())

    assert s.film.base is None
    assert s.film.contrast == 1.5
    assert any("contrasts" in w for w in edits.warnings)


def test_load_drops_unknown_fields_with_warning(tmp_path):
    raw = {
        "schema": 1,
        "roll_defaults": {"film": {"contrast": 1.6, "bogus": 9}, "sharpen": {}},
        "frames": {
            "3": {
                "source": "img.CR3",
                "auto": None,
                "user": {"film": {"exposure": 1.2, "base": (1, 1, 1), "wrong": 0}},
            }
        },
    }
    (tmp_path / SIDECAR).write_text(json.dumps(raw), encoding="utf-8")

    edits = load(tmp_path)

    assert edits.roll_defaults["film"] == {"contrast": 1.6}
    assert edits.frames[3].user["film"] == {"exposure": 1.2}
    assert any("bogus" in w for w in edits.warnings)
    assert any("wrong" in w for w in edits.warnings)


def test_apply_copies_film_not_crop():
    edits = Edits(
        roll_defaults={"film": {"contrast": 1.2}, "mirror": False},
        frames={
            1: FrameEdit(source="a.CR3", user={
                "film": {"contrast": 1.7, "exposure": 1.1},
                "crop": (0.1, 0.2, 0.8, 0.9),
                "rotate90": 2,
            }),
            2: FrameEdit(source="b.CR3", user={"crop": (0.0, 0.0, 1.0, 1.0)}),
            3: FrameEdit(source="c.CR3"),
        },
    )

    apply(edits, 1, [1, 2, 3])

    assert edits.frames[1].user["film"] == {"contrast": 1.7, "exposure": 1.1}
    assert edits.frames[2].user["film"] == {"contrast": 1.7, "exposure": 1.1}
    assert edits.frames[3].user["film"] == {"contrast": 1.7, "exposure": 1.1}
    assert edits.frames[2].user["rotate90"] == 2
    assert edits.frames[2].user["crop"] == (0.0, 0.0, 1.0, 1.0)
    assert "crop" not in edits.frames[3].user


def test_settings_hash_stable_and_sensitive():
    s = FrameSettings()
    h = settings_hash(s)

    assert h == settings_hash(FrameSettings())
    assert len(h) == 40
    assert all(c in "0123456789abcdef" for c in h)
    assert settings_hash(FrameSettings(angle=0.1)) != h
    assert settings_hash(FrameSettings(sharpen=SharpenParams(amount=1.0))) != h


def test_concurrent_saves_leave_valid_json(tmp_path):
    edits = [Edits(frames={n: FrameEdit(source=f"img{n}.CR3", auto=_detection())})
             for n in range(20)]
    barrier = threading.Barrier(len(edits))
    errors = []

    def writer(state):
        try:
            barrier.wait()
            save(tmp_path, state)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(state,)) for state in edits]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert load(tmp_path).warnings == []
    assert not list(tmp_path.glob("*.tmp"))


def test_save_retries_while_target_is_locked(tmp_path, monkeypatch):
    # Windows raises PermissionError while another process holds the file.
    import fsd.darkroom.edits as edits_module

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(edits_module.os, "replace", flaky)
    monkeypatch.setattr(edits_module.time, "sleep", lambda s: None)
    save(tmp_path, Edits())
    assert calls["n"] == 3
    assert load(tmp_path).warnings == []
    assert not list(tmp_path.glob("*.tmp"))
