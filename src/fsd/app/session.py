"""A live editing session: open a roll, analyse frames in the background and
serve cached previews.

One re-entrant lock guards the roll, its edits and every write of the sidecar.
Decoding, detection, rendering and exporting happen on a thread pool outside
that lock; their results are merged back under it.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from PIL import Image

from fsd.config import Config
from fsd.darkroom.edits import (
    APPLY_FIELDS,
    Edits,
    FrameEdit,
    apply as apply_edits,
    effective,
    load,
    needs_detection,
    save,
    settings_hash,
)
from fsd.darkroom.export import (
    contact_sheet as build_contact_sheet,
    export_frames,
    open_in_photos as launch_photos,
)
from fsd.darkroom.presets import load_presets, match_preset
from fsd.darkroom.presets import save_preset as write_preset
from fsd.darkroom.roll import Roll, find_rolls, open_roll
from fsd.paths import cache_dir as default_cache_dir, user_data_dir
from fsd.processing.detect import detect
from fsd.processing.geometry import straighten
from fsd.processing.render import render
from fsd.processing.source import Decoded, decode

THUMB_SIZE = 320
PREVIEW_SIZE = 1600
JPEG_QUALITY = 85
_USER_FIELDS = frozenset({"crop", "angle", "rotate90", "mirror", "base_region", "film", "sharpen"})


@lru_cache(maxsize=6)
def _cached_half(path: str, fingerprint: tuple[int, int] | None) -> Decoded:
    # lazy: in-memory LRU of 6 half decodes (~300 MB); move to a disk cache if memory is tight.
    return decode(Path(path), half=True)


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


@dataclass
class _Job:
    kind: str
    total: int
    done: int = 0
    status: str = "running"
    results: list[dict] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)


class Session:
    """Everything the UI needs to work on one roll at a time."""

    def __init__(
        self,
        cfg: Config,
        *,
        workers: int = max(1, os.cpu_count() // 2),
        presets_path: Path | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self._presets_path = (
            Path(presets_path) if presets_path is not None else user_data_dir() / "presets.yaml"
        )
        self._cache_path = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self._cache_path.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._pool = ThreadPoolExecutor(max_workers=workers)

        self._roll: Roll | None = None
        self._edits = Edits()
        self._preset: dict | None = None
        self._analysing = {"done": 0, "total": 0}
        self._status: dict[int, str] = {}
        self._jobs: dict[str, _Job] = {}
        self._outstanding = 0
        self._closed = False

    # ------------------------------------------------------------------ pool

    def _submit(self, fn: Callable, *args) -> None:
        with self._cond:
            if self._closed:
                raise RuntimeError("Session is shut down")
            self._outstanding += 1
        try:
            self._pool.submit(self._call, fn, *args)
        except Exception:
            with self._cond:
                self._outstanding -= 1
                self._cond.notify_all()
            raise

    def _call(self, fn: Callable, *args):
        try:
            return fn(*args)
        finally:
            with self._cond:
                self._outstanding -= 1
                self._cond.notify_all()

    def wait_idle(self, timeout: float = 20) -> bool:
        """Block until every submitted job has finished, up to ``timeout`` s."""
        with self._cond:
            return self._cond.wait_for(lambda: self._outstanding == 0, timeout)

    def shutdown(self) -> None:
        """Stop accepting work and let already-submitted jobs finish."""
        with self._cond:
            self._closed = True
        self._pool.shutdown(wait=True)

    # ----------------------------------------------------------------- roll

    @staticmethod
    def _stock(metadata: dict) -> str:
        for key in ("stock", "film_stock", "filmStock", "film"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _source_name(self, n: int) -> str:
        for frame in self._roll.frames if self._roll else []:
            if frame.number == n:
                return frame.source.name
        return ""

    def open(self, path: Path) -> dict:
        path = Path(path)
        with self._lock:
            roll = open_roll(path)
            edits = load(path)
            presets = load_presets(self._presets_path)
            stock = self._stock(roll.metadata)
            if stock and not edits.preset:
                matched = match_preset(presets, stock)
                if matched is not None:
                    edits.preset = matched
                    save(path, edits)
            self._roll = roll
            self._edits = edits
            self._preset = presets.get(edits.preset) if edits.preset else None
            self._analysing = {"done": 0, "total": 0}
            self._status = {}
        self._queue_detection()
        return self.state()

    def refresh(self) -> dict:
        with self._lock:
            if self._roll is None:
                raise RuntimeError("No roll is open")
            self._roll = open_roll(self._roll.path)
        self._queue_detection()
        return self.state()

    def _queue_detection(self) -> None:
        with self._lock:
            if self._roll is None:
                return
            roll_path = self._roll.path
            targets = [
                (frame.number, frame.source)
                for frame in self._roll.frames
                if needs_detection(self._edits.frames.get(frame.number), frame.source)
            ]
            self._analysing = {"done": 0, "total": len(targets)}
            for n, _ in targets:
                self._status.pop(n, None)
        for n, source in targets:
            self._submit(self._detect_one, n, source, roll_path)

    def _detect_one(self, n: int, source: Path, roll_path: Path) -> None:
        error: Exception | None = None
        detection = None
        fingerprint: tuple[int, int] | None = None
        try:
            fingerprint = _fingerprint(source)
            decoded = _cached_half(str(source), fingerprint)
            detection = detect(
                decoded.linear(),
                aspect=self.cfg.film.aspect,
                margin=self.cfg.detect.margin,
            )
        except Exception as exc:
            error = exc

        with self._cond:
            if self._roll is None or self._roll.path != roll_path:
                return
            if error is not None:
                self._status[n] = "error"
            else:
                edit = self._edits.frames.get(n)
                if edit is None:
                    edit = FrameEdit(source=source.name)
                    self._edits.frames[n] = edit
                edit.source = source.name
                edit.fingerprint = fingerprint
                edit.auto = detection
                save(roll_path, self._edits)
                self._status[n] = "ready"
            self._analysing["done"] += 1
            self._cond.notify_all()

    def library(self) -> list[dict]:
        roots = self.cfg.library.resolved_roots()
        with self._lock:
            opened = self._roll.path if self._roll is not None else None
        if opened is not None and not any(
            opened == root or opened.parent == root for root in roots
        ):
            roots.append(opened.parent)

        result = []
        for path in find_rolls(roots):
            roll = open_roll(path)
            edits = load(path)
            exported = 0
            flagged = 0
            for frame in roll.frames:
                edit = edits.frames.get(frame.number)
                if edit is None:
                    continue
                if edit.exported.get("settings_hash"):
                    exported += 1
                if edit.auto is not None and edit.auto.confidence < self.cfg.detect.min_confidence:
                    flagged += 1
            result.append({
                "path": str(path),
                "name": roll.name,
                "stock": self._stock(roll.metadata),
                "frames": len(roll.frames),
                "exported": exported,
                "flagged": flagged,
            })
        return result

    # ---------------------------------------------------------------- state

    def _frame_state(self, n: int) -> dict:
        with self._lock:
            frame = next((f for f in self._roll.frames if f.number == n), None) if self._roll else None
            edit = self._edits.frames.get(n)
            source = frame.source.name if frame else (edit.source if edit else "")
            status = self._status.get(n)
            if status is None:
                status = "ready" if (edit is not None and edit.auto is not None) else "pending"
            if status != "error" and frame is not None and needs_detection(edit, frame.source):
                status = "pending"
            settings = effective(replace(self._edits, warnings=[]), n, self._preset, self.cfg)
            confidence = edit.auto.confidence if edit is not None and edit.auto is not None else None
            recorded = (edit.exported or {}).get("settings_hash") if edit is not None else None
            return {
                "n": n,
                "source": source,
                "status": status,
                "confidence": confidence,
                "flagged": confidence is not None and confidence < self.cfg.detect.min_confidence,
                "rejected": bool(edit.rejected) if edit is not None else False,
                "exported_current": bool(recorded) and recorded == settings_hash(settings),
                "settings": asdict(settings),
            }

    def state(self) -> dict:
        with self._lock:
            roll = self._roll
            if roll is None:
                return {
                    "name": None,
                    "path": None,
                    "metadata": {},
                    "preset": None,
                    "warnings": [],
                    "analysing": dict(self._analysing),
                    "frames": [],
                }
            return {
                "name": roll.name,
                "path": str(roll.path),
                "metadata": dict(roll.metadata),
                "preset": self._edits.preset,
                "warnings": list(roll.warnings) + list(self._edits.warnings),
                "analysing": dict(self._analysing),
                "frames": [self._frame_state(frame.number) for frame in roll.frames],
            }

    # --------------------------------------------------------------- images

    def _render_context(self, n: int):
        """Source, fingerprint and settings from one consistent lock holding.

        Taking these together means a roll switch cannot pair one roll's frame
        with another's settings, or leave us with a frame that has vanished.
        """
        with self._lock:
            frame = next((f for f in self._roll.frames if f.number == n), None) if self._roll else None
            if frame is None:
                raise KeyError(f"Frame {n} is not in the open roll")
            edit = self._edits.frames.get(n)
            fingerprint = edit.fingerprint if edit is not None else None
            settings = effective(replace(self._edits, warnings=[]), n, self._preset, self.cfg)
            return frame.source, fingerprint, settings

    @staticmethod
    def _encode(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()

    def thumb(self, n: int) -> bytes:
        source, fingerprint, settings = self._render_context(n)
        key = json.dumps(
            [source.name, fingerprint, settings_hash(settings)], sort_keys=True
        )
        path = self._cache_path / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.jpg"
        if path.is_file():
            return path.read_bytes()

        rendered = render(_cached_half(str(source), fingerprint), settings, scale=0.5)
        image = Image.fromarray((rendered >> 8).astype(np.uint8), "RGB")
        image.thumbnail((THUMB_SIZE, THUMB_SIZE))
        data = self._encode(image)
        path.write_bytes(data)
        return data

    def preview(self, n: int, view: Literal["positive", "negative"]) -> bytes:
        source, fingerprint, settings = self._render_context(n)
        decoded = _cached_half(str(source), fingerprint)
        if view == "positive":
            rendered = render(decoded, settings, scale=0.5)
            pixels = (rendered >> 8).astype(np.uint8)
        elif view == "negative":
            linear = straighten(decoded.linear(), settings.angle)
            display = np.clip(linear, 0.0, 1.0) ** (1.0 / 2.2)
            pixels = (display * 255.0 + 0.5).astype(np.uint8)
        else:
            raise ValueError(f"Unknown preview view {view!r}")
        image = Image.fromarray(pixels, "RGB")
        image.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
        return self._encode(image)

    # ---------------------------------------------------------------- edits

    def edit(self, n: int, changes: dict) -> dict:
        with self._lock:
            edit = self._edits.frames.get(n)
            if edit is None:
                edit = FrameEdit(source=self._source_name(n))
                self._edits.frames[n] = edit
            for key, value in changes.items():
                if key in _USER_FIELDS:
                    edit.user[key] = value
            save(self._roll.path, self._edits)
        return self._frame_state(n)

    def reset(self, n: int) -> dict:
        with self._lock:
            edit = self._edits.frames.get(n)
            if edit is not None:
                edit.user = {}
                save(self._roll.path, self._edits)
        return self._frame_state(n)

    def apply(
        self, n: int, to: Literal["all", "selected", "auto"], frames: list[int] | None
    ) -> dict:
        with self._lock:
            numbers = [frame.number for frame in self._roll.frames]
            if to == "all":
                targets = numbers
            elif to == "selected":
                targets = list(frames or [])
            elif to == "auto":
                targets = [
                    number for number in numbers
                    if not any(
                        field_name in (self._edits.frames[number].user if number in self._edits.frames else {})
                        for field_name in APPLY_FIELDS
                    )
                ]
            else:
                raise ValueError(f"Unknown apply target {to!r}")
            apply_edits(self._edits, n, targets)
            save(self._roll.path, self._edits)
        return self.state()

    def reject(self, n: int, rejected: bool) -> dict:
        with self._lock:
            edit = self._edits.frames.get(n)
            if edit is None:
                edit = FrameEdit(source=self._source_name(n))
                self._edits.frames[n] = edit
            edit.rejected = bool(rejected)
            save(self._roll.path, self._edits)
        return self._frame_state(n)

    def set_roll_defaults(self, changes: dict) -> dict:
        with self._lock:
            defaults = self._edits.roll_defaults
            for key, value in changes.items():
                if key in ("film", "sharpen"):
                    merged = dict(defaults.get(key) or {})
                    if isinstance(value, dict):
                        merged.update(value)
                    defaults[key] = merged
                elif key in ("mirror", "rotate90"):
                    defaults[key] = value
            save(self._roll.path, self._edits)
        return self.state()

    def save_preset(self, name: str, from_n: int) -> dict:
        with self._lock:
            settings = effective(replace(self._edits, warnings=[]), from_n, self._preset, self.cfg)
            film = {key: value for key, value in asdict(settings.film).items() if key != "base"}
            write_preset(self._presets_path, name, film, asdict(settings.sharpen))
            self._edits.preset = name
            self._preset = load_presets(self._presets_path).get(name)
            save(self._roll.path, self._edits)
        return self.state()

    # --------------------------------------------------------------- export

    def _export(
        self,
        targets: list[int],
        *,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ):
        with self._lock:
            roll = self._roll
            roll_path = roll.path
            edits_copy = copy.deepcopy(self._edits)
            preset = self._preset
        def on_exported(n: int, exported_edit: FrameEdit) -> None:
            with self._lock:
                if self._roll is None or self._roll.path != roll_path:
                    return
                current = self._edits.frames.get(n)
                if current is None:
                    current = FrameEdit(source=exported_edit.source)
                    self._edits.frames[n] = current
                current.exported = copy.deepcopy(exported_edit.exported)
                save(roll_path, self._edits)

        return export_frames(
            roll, edits_copy, targets, self.cfg, preset,
            progress=progress, cancelled=cancelled, on_exported=on_exported,
        )

    @staticmethod
    def _result_dict(result, roll_path: Path) -> dict:
        files = []
        for path in result.files:
            try:
                files.append(path.relative_to(roll_path).as_posix())
            except ValueError:
                files.append(str(path))
        return {"frame": result.frame, "files": files, "error": result.error}

    def _run_export(
        self,
        job_id: str,
        targets: list[int],
        progress: Callable[[int, int], None],
        roll_path: Path,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]

        try:
            results = self._export(
                targets, progress=progress, cancelled=lambda: job.cancel.is_set()
            )
            payload = [self._result_dict(result, roll_path) for result in results]
            with self._cond:
                job.results = payload
                if job.cancel.is_set():
                    job.status = "cancelled"
                elif any(result.error for result in results):
                    job.status = "done_with_errors"
                else:
                    job.status = "done"
                self._cond.notify_all()
        except Exception as exc:
            with self._cond:
                job.results = [{"frame": n, "files": [], "error": str(exc)} for n in targets]
                job.status = "cancelled" if job.cancel.is_set() else "done_with_errors"
                self._cond.notify_all()

    def start_export(self, frames: list[int] | Literal["all"]) -> str:
        with self._lock:
            if self._roll is None:
                raise RuntimeError("No roll is open")
            if frames == "all":
                targets = [
                    frame.number for frame in self._roll.frames
                    if not (frame.number in self._edits.frames and self._edits.frames[frame.number].rejected)
                ]
            else:
                targets = list(frames)
            job_id = uuid.uuid4().hex
            job = _Job(kind="export", total=len(targets))
            self._jobs[job_id] = job
            roll_path = self._roll.path

        def progress(done: int, total: int) -> None:
            with self._cond:
                job.done = done
                self._cond.notify_all()

        self._submit(self._run_export, job_id, targets, progress, roll_path)
        return job_id

    def job(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs[job_id]
            return {
                "kind": job.kind,
                "done": job.done,
                "total": job.total,
                "status": job.status,
                "results": copy.deepcopy(job.results),
            }

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status == "running":
                job.cancel.set()

    def contact_sheet(self) -> Path:
        with self._lock:
            roll, edits = self._roll, copy.deepcopy(self._edits)
        return build_contact_sheet(roll, edits, {}, self.cfg)

    def open_in_photos(self, n: int) -> Path:
        if not self._frame_state(n)["exported_current"]:
            self._export([n])
        with self._lock:
            edit = self._edits.frames.get(n)
            exported = edit.exported if edit is not None else {}
            roll_path = self._roll.path
        relative = exported.get("tif") or exported.get("jpg")
        if not relative:
            raise RuntimeError(f"Frame {n} has no positive to open")
        path = roll_path / relative
        launch_photos(path)
        return path
