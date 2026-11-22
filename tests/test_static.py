"""The browser UI is three static files: everything it references must load."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from fsd.app.server import create_app
from fsd.app.session import Session
from fsd.config import Config

_LOCAL_ASSET = re.compile(r'(?:src|href)="(/[^"]*)"')


@pytest.fixture
def client(tmp_path):
    cfg = Config()
    cfg.library.roots = [str(tmp_path)]
    session = Session(
        cfg,
        workers=1,
        presets_path=tmp_path / "presets.yaml",
        cache_dir=tmp_path / "cache",
    )
    with TestClient(create_app(cfg, session=session)) as test_client:
        yield test_client
    session.shutdown()


def test_index_served_and_assets_resolve(client):
    response = client.get("/")
    assert response.status_code == 200

    assets = [
        path
        for path in _LOCAL_ASSET.findall(response.text)
        if urlparse(path).path.startswith("/static/")
    ]
    assert assets, "index.html must reference local /static/ assets"
    for path in assets:
        asset = client.get(path)
        assert asset.status_code == 200, f"{path} did not resolve"
