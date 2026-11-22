"""The frozen build's load-bearing bits: its datas and its entry point.

Adapted from the Canon EDSDK app's ``test_packaging.py``. That app guarded
against shipping Canon's SDK; nothing here is licensed that way, so what is
worth testing is that the browser UI and the config seed are actually in the
bundle and that the bundle starts the same server the source does.
"""

from __future__ import annotations

import importlib.util

from fsd.paths import REPO_ROOT


def _spec() -> str:
    return (REPO_ROOT / "packaging" / "fsd.spec").read_text(encoding="utf-8")


def test_spec_bundles_static_and_example_config():
    spec = _spec()
    assert "fsd/app/static" in spec, "the browser UI must be bundled"
    assert "config.example.yaml" in spec, "the seeded config must be bundled"


def test_spec_has_no_sdk_or_camera_leftovers():
    spec = _spec().lower()
    for leftover in ("edsdk", "camera", "cefs"):
        assert leftover not in spec, f"{leftover} survived the port"


def test_entry_runs_server_main(monkeypatch):
    import fsd.app.server as server

    called = []
    monkeypatch.setattr(server, "main", lambda: called.append(True) or 0)

    module_spec = importlib.util.spec_from_file_location(
        "fsd_packaging_entry", REPO_ROOT / "packaging" / "entry.py"
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    assert module.run() == 0
    assert called == [True]
