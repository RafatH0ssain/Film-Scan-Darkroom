"""Copied from Canon-EDSDK-Film-Scanner src/cefs/processing/develop.py at abf739d. Port fixes by hand.

Source decoding: one entry point that maps a scanned negative to RGB pixels
and the linear values those pixels stand for.

Depth is part of the answer, not an implementation detail. RAW stays 16-bit
sensor-linear, PQ HEIF stays 16-bit PQ codes, 16-bit TIFF stays sRGB-encoded
16-bit, and the 8-bit formats stay 8-bit. :meth:`Decoded.linear` then applies
the transfer the *source* actually used, so inversion never has to guess which
kind of file it was handed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from fsd.processing import raw
from fsd.processing.film import linear16_to_linear, pq16_to_linear_lut, srgb_to_linear
from fsd.processing.tiffio import TiffError, read_tiff

logger = logging.getLogger(__name__)

#: What raw.is_raw accepts, normalised to lower-case with a leading dot.
RAW_EXTENSIONS: frozenset[str] = frozenset(e.lower() for e in raw.RAW_EXTENSIONS)

#: What a Canon body calls HEIF, plus the names everything else uses.
HEIF_EXTENSIONS = {".hif", ".heif", ".heic"}

#: Formats Pillow/TIFF read at their native depth.
DIRECT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

_TIFF_EXTENSIONS = {".tif", ".tiff"}
_SUPPORTED = RAW_EXTENSIONS | HEIF_EXTENSIONS | DIRECT_EXTENSIONS

_SRGB_THRESHOLD = 0.04045
_SRGB_LINEAR_SCALE = 12.92
_SRGB_ALPHA = 0.055


class SourceError(RuntimeError):
    """A source image could not be identified or decoded."""


@dataclass(frozen=True)
class Decoded:
    """Pixels at the source's native depth, plus how to linearise them."""

    kind: Literal["raw", "pq", "srgb16", "srgb8"]
    pixels: np.ndarray

    def linear(self) -> np.ndarray:
        """Float32 RGB in this kind's own linear scale, usually 0-1."""
        if self.kind == "raw":
            return linear16_to_linear(self.pixels)
        if self.kind == "pq":
            return pq16_to_linear_lut()[self.pixels]
        if self.kind == "srgb16":
            return _srgb16_to_linear(self.pixels)
        return srgb_to_linear(self.pixels)


def _srgb16_to_linear(image: np.ndarray) -> np.ndarray:
    """sRGB EOTF for 16-bit samples, in float32.

    :func:`fsd.processing.film.srgb_to_linear` is an 8-bit LUT; a 16-bit LUT
    would be 256 KB of near-duplicates, so the same curve is evaluated here.
    """
    value = image.astype(np.float32) / np.float32(65535.0)
    return np.where(
        value <= np.float32(_SRGB_THRESHOLD),
        value / np.float32(_SRGB_LINEAR_SCALE),
        (
            (value + np.float32(_SRGB_ALPHA))
            / np.float32(1.0 + _SRGB_ALPHA)
        )
        ** np.float32(2.4),
    ).astype(np.float32)


def is_heif(path: Path | str) -> bool:
    return Path(path).suffix.lower() in HEIF_EXTENSIONS


def decodable(path: Path) -> bool:
    """Whether the extension names a source format we know how to decode."""
    return Path(path).suffix.lower() in _SUPPORTED


def _read_heif(source: Path) -> tuple[np.ndarray, bool]:
    """Decode a HEIF at native depth. Returns ``(rgb, is_pq)`` -- uint16 PQ
    codes, or uint8 sRGB."""
    try:
        import pillow_heif
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SourceError(
            f"Cannot read {source.name}: pillow-heif is not installed.\n"
            "  pip install pillow-heif\n"
            "  Pillow alone cannot read HEIF in this build."
        ) from exc

    try:
        # Keep HDR: dropping to 8 bits here would discard two of the camera's
        # ten before the one step that stretches the range hard enough to need them.
        heif = pillow_heif.open_heif(str(source), convert_hdr_to_8bit=False)
        rgb = np.asarray(heif)
    except Exception as exc:  # pillow-heif raises several unrelated types
        raise SourceError(f"Could not decode {source.name}: {exc}") from exc

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise SourceError(f"{source.name} is not a 3-channel image ({rgb.shape}).")
    rgb = rgb[:, :, :3]  # drop alpha if the file has one

    # 16 is SMPTE ST 2084 (PQ). Read, not inferred from depth: an 8-bit sRGB
    # HEIF exists and running it through the PQ curve would wreck it.
    nclx = heif.info.get("nclx_profile") or {}
    is_pq = nclx.get("transfer_characteristics") == 16

    if is_pq and rgb.dtype != np.uint16:
        raise SourceError(
            f"{source.name} declares PQ but decoded as {rgb.dtype}, not 16-bit."
        )
    if not is_pq and rgb.dtype == np.uint16:
        # Deep but not PQ: no transfer we can name, so drop to 8 bits rather
        # than guess one.
        logger.warning(
            "%s is %s-bit but not PQ (transfer=%s); treating it as 8-bit sRGB.",
            source.name,
            heif.info.get("bit_depth"),
            nclx.get("transfer_characteristics"),
        )
        rgb = (rgb >> 8).astype(np.uint8)

    # Copy, deliberately: np.asarray on a HeifFile is a view into libheif's
    # buffer, freed with it, and touching it later is an access violation that
    # kills the process with no traceback.
    return np.array(rgb, copy=True, order="C"), is_pq


def _decode_raw(path: Path, half: bool) -> Decoded:
    try:
        pixels = raw.decode_raw(path, half=half)
    except Exception as exc:  # rawpy raises several unrelated types
        raise SourceError(f"Could not decode {path.name}: {exc}") from exc
    return Decoded(kind="raw", pixels=pixels)


def _decode_heif(path: Path, half: bool) -> Decoded:
    pixels, is_pq = _read_heif(path)
    if half:
        pixels = pixels[::2, ::2]
    kind = "pq" if is_pq else "srgb8"
    return Decoded(kind=kind, pixels=np.ascontiguousarray(pixels))


def _decode_tiff(path: Path, half: bool) -> Decoded:
    try:
        pixels = read_tiff(path)
    except TiffError as exc:
        raise SourceError(f"Could not decode {path.name}: {exc}") from exc
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, None], 3, axis=2)
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        raise SourceError(f"{path.name} is not a 3-channel image ({pixels.shape}).")
    pixels = pixels[:, :, :3]
    if pixels.dtype == np.uint16:
        kind = "srgb16"
    else:
        if pixels.dtype != np.uint8:
            pixels = np.clip(pixels, 0, 255).astype(np.uint8)
        kind = "srgb8"
    if half:
        pixels = pixels[::2, ::2]
    return Decoded(kind=kind, pixels=np.ascontiguousarray(pixels))


def _decode_pillow(path: Path, half: bool) -> Decoded:
    try:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
            if half:
                image = image.reduce(2)
            pixels = np.asarray(image, dtype=np.uint8)
    except OSError as exc:  # includes UnidentifiedImageError
        raise SourceError(f"Could not decode {path.name}: {exc}") from exc
    return Decoded(kind="srgb8", pixels=np.array(pixels, copy=True, order="C"))


def decode(path: Path, *, half: bool = False) -> Decoded:
    """Decode a source image, optionally at half resolution.

    16-bit TIFFs are sRGB-encoded, not linear, so they come back as ``srgb16``.
    A half-resolution RAW is decoded by LibRaw directly; every other format is
    halved after decoding.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED:
        raise SourceError(f"Unsupported source format {suffix!r}: {path.name}")
    if not path.is_file():
        raise SourceError(f"Source file not found: {path}")
    if suffix in RAW_EXTENSIONS:
        return _decode_raw(path, half)
    if suffix in HEIF_EXTENSIONS:
        return _decode_heif(path, half)
    if suffix in _TIFF_EXTENSIONS:
        return _decode_tiff(path, half)
    return _decode_pillow(path, half)
