"""Copied from Canon-EDSDK-Film-Scanner src/cefs/app/server.py at abf739d. Port fixes by hand.

Only the startup helpers (``prepare_user_folder``, ``open_browser_when_ready``
and the shape of ``main``) are copied from that file. The routes, request
models and Session wiring below belong to Film Scan Darkroom.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from fsd import __version__
from fsd.app.session import Session
from fsd.config import Config, load_config
from fsd.darkroom.export import photos_available
from fsd.darkroom.presets import load_presets
from fsd.darkroom.roll import find_rolls
from fsd.mock.synth import main as write_example_roll
from fsd.paths import example_config_path, is_frozen, static_dir, user_data_dir
from fsd.processing.film import FilmParams
from fsd.processing.sharpen import SharpenParams

logger = logging.getLogger(__name__)

APP_NAME = "Film Scan Darkroom"
STATIC_DIR = static_dir()
_FILM_FIELDS = frozenset(field.name for field in dataclass_fields(FilmParams))
_SHARPEN_FIELDS = frozenset(field.name for field in dataclass_fields(SharpenParams))


def _check_keys(value: dict | None, allowed: frozenset[str], label: str) -> dict | None:
    if value is None:
        return None
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"Unknown {label} setting(s): {', '.join(unknown)}")
    return value


def _check_quad(
    value: list[float] | None, label: str, *, ordered: bool = False
) -> list[float] | None:
    if value is None:
        return None
    if len(value) != 4:
        raise ValueError(f"{label} must be four numbers")
    if any(not 0.0 <= number <= 1.0 for number in value):
        raise ValueError(f"{label} values must be between 0 and 1")
    if ordered and not (value[0] < value[2] and value[1] < value[3]):
        raise ValueError(f"{label} must have x0 < x1 and y0 < y1")
    return value


class _SettingsModel(BaseModel):
    film: dict | None = None
    sharpen: dict | None = None

    @field_validator("film")
    @classmethod
    def _film(cls, value: dict | None) -> dict | None:
        return _check_keys(value, _FILM_FIELDS, "film")

    @field_validator("sharpen")
    @classmethod
    def _sharpen(cls, value: dict | None) -> dict | None:
        return _check_keys(value, _SHARPEN_FIELDS, "sharpen")


class EditRequest(_SettingsModel):
    crop: list[float] | None = None
    angle: float | None = Field(default=None, ge=-45, le=45)
    rotate90: int | None = Field(default=None, ge=0, le=3)
    mirror: bool | None = None
    base_region: list[float] | None = None

    @field_validator("crop")
    @classmethod
    def _crop(cls, value: list[float] | None) -> list[float] | None:
        return _check_quad(value, "crop", ordered=True)

    @field_validator("base_region")
    @classmethod
    def _region(cls, value: list[float] | None) -> list[float] | None:
        return _check_quad(value, "base_region")


class RollDefaultsRequest(_SettingsModel):
    mirror: bool | None = None
    rotate90: int | None = Field(default=None, ge=0, le=3)


class OpenRequest(BaseModel):
    path: str


class RejectRequest(BaseModel):
    rejected: bool


class ApplyRequest(BaseModel):
    from_: int = Field(alias="from")
    to: Literal["all", "selected", "auto"]
    frames: list[int] | None = None


class PresetRequest(BaseModel):
    name: str
    from_: int = Field(alias="from")


class ExportRequest(BaseModel):
    frames: Literal["all"] | list[int]


def create_app(
    config: Config | None = None, *, session: Session | None = None
) -> FastAPI:
    """Build the app. Injecting a config or a Session keeps it testable."""
    config = config or load_config()
    session = session if session is not None else Session(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        session.shutdown()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.session = session
    app.state.config = config
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def state_or_409() -> dict:
        state = session.state()
        if state.get("path") is None:
            raise HTTPException(status_code=409, detail="No roll is open")
        return state

    def frame_or_404(n: int) -> None:
        state = state_or_409()
        if n not in {frame["n"] for frame in state["frames"]}:
            raise HTTPException(status_code=404, detail=f"No frame {n} in this roll")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status() -> dict:
        return {
            "app": APP_NAME,
            "version": __version__,
            "platform": sys.platform,
            "photos_available": photos_available(),
            "export": {
                "format": config.export.format,
                "tiff_compression": config.export.tiff_compression,
                "jpeg_quality": config.export.jpeg_quality,
                "folder": config.export.folder,
            },
            "detect": {"min_confidence": config.detect.min_confidence},
        }

    @app.get("/api/library")
    def library() -> list[dict]:
        return session.library()

    @app.post("/api/roll/open")
    def roll_open(request: OpenRequest) -> dict:
        path = Path(request.path)
        if not path.is_dir():
            raise HTTPException(status_code=404, detail=f"No such folder: {request.path}")
        return session.open(path)

    @app.post("/api/roll/refresh")
    def roll_refresh() -> dict:
        state_or_409()
        try:
            return session.refresh()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/roll")
    def roll_get() -> dict:
        return state_or_409()

    @app.post("/api/roll/defaults")
    def roll_defaults(request: RollDefaultsRequest) -> dict:
        state_or_409()
        return session.set_roll_defaults(request.model_dump(exclude_unset=True))

    @app.get("/api/frames/{n}/thumb")
    def thumb(n: int) -> Response:
        frame_or_404(n)
        return Response(
            content=session.thumb(n),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/frames/{n}/preview")
    def preview(n: int, view: Literal["positive", "negative"] = "positive") -> Response:
        frame_or_404(n)
        return Response(
            content=session.preview(n, view),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/frames/{n}/edit")
    def edit(n: int, request: EditRequest) -> dict:
        frame_or_404(n)
        return session.edit(n, request.model_dump(exclude_unset=True))

    @app.post("/api/frames/{n}/reset")
    def reset(n: int) -> dict:
        frame_or_404(n)
        return session.reset(n)

    @app.post("/api/frames/{n}/reject")
    def reject(n: int, request: RejectRequest) -> dict:
        frame_or_404(n)
        return session.reject(n, request.rejected)

    @app.post("/api/apply")
    def apply(request: ApplyRequest) -> dict:
        state_or_409()
        return session.apply(request.from_, request.to, request.frames)

    @app.get("/api/presets")
    def presets() -> dict:
        return load_presets(session._presets_path)

    @app.post("/api/presets")
    def save_preset(request: PresetRequest) -> dict:
        state_or_409()
        session.save_preset(request.name, request.from_)
        return load_presets(session._presets_path)

    @app.post("/api/export")
    def export(request: ExportRequest) -> dict:
        state_or_409()
        try:
            return {"job": session.start_export(request.frames)}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict:
        try:
            return session.job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No job {job_id}") from exc

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict:
        try:
            session.cancel(job_id)
            return session.job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No job {job_id}") from exc

    @app.post("/api/contact-sheet")
    def contact_sheet() -> dict:
        state_or_409()
        return {"path": str(session.contact_sheet())}

    @app.post("/api/frames/{n}/open-in-photos")
    def open_in_photos(n: int) -> dict:
        frame_or_404(n)
        if not photos_available():
            raise HTTPException(
                status_code=409, detail="Open in Photos is only available on macOS."
            )
        return {"path": str(session.open_in_photos(n))}

    return app


def prepare_user_folder() -> Path | None:
    """Seed the user's folder and a commented config on first launch of a bundle.

    Returns the config path, or None from a checkout, where the repository
    already is the user folder.
    """
    if not is_frozen():
        return None

    folder = user_data_dir()
    folder.mkdir(parents=True, exist_ok=True)

    destination = folder / "config.yaml"
    if not destination.is_file():
        example = example_config_path()
        if example.is_file():
            destination.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Created %s", destination)

    rolls_dir = folder / "rolls"
    if not find_rolls([rolls_dir]):
        example_roll = rolls_dir / "Example Roll"
        write_example_roll(["--write", str(example_roll), "--frames", "4"])
        logger.info("Created example roll at %s", example_roll)
    return destination


def open_browser_when_ready(url: str, server: Any) -> None:
    """Open the UI once the server is actually accepting connections."""

    def wait_then_open() -> None:
        deadline = time.perf_counter() + 20.0
        while time.perf_counter() < deadline:
            if getattr(server, "started", False):
                webbrowser.open(url)
                return
            time.sleep(0.1)
        logger.warning("Server did not start within 20 s; not opening a browser.")

    threading.Thread(target=wait_then_open, name="fsd-browser", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="Path to a config.yaml to load.")
    parser.add_argument(
        "--browser",
        dest="browser",
        action="store_true",
        default=None,
        help="Open the UI in a browser once the server is up (default when packaged).",
    )
    parser.add_argument(
        "--no-browser", dest="browser", action="store_false", help="Never open a browser."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    prepare_user_folder()
    config = load_config(Path(args.config) if args.config else None)

    host = args.host or config.server.host
    port = args.port or config.server.port

    import uvicorn

    url = f"http://{host}:{port}/"
    print(f"{APP_NAME} -- turn scanned negatives into positives")
    print(f"Open {url} in a browser.")
    if is_frozen():
        print(f"Settings and presets: {user_data_dir()}")
    print()

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host=host, port=port, log_level="warning")
    )
    if args.browser or (args.browser is None and is_frozen()):
        open_browser_when_ready(url, server)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
