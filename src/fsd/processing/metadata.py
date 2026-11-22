"""Human-readable description embedded in every positive.

The same string goes into a TIFF's ImageDescription and a JPEG's EXIF
ImageDescription, so a positive carries its own provenance when it is pulled
out of the roll folder and sent on its own.
"""

from __future__ import annotations

import fsd
from fsd.darkroom.roll import Roll

SOFTWARE = f"Film Scan Darkroom {fsd.__version__}"

#: Roll metadata keys appended to the description, in order, when non-empty.
_PARTS = ("stock", "developer", "date")


def describe(roll: Roll, frame: int) -> str:
    """``"Roll014 frame 7 · Kentmere 400 · ID-11 · 2026-11-02"``.

    The roll name and frame number always lead; stock, developer and date are
    added only when the roll.json has them, so a bare roll does not end up with
    dangling separators.
    """
    parts = [f"{roll.name} frame {frame}"]
    for key in _PARTS:
        value = roll.metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    return " · ".join(parts)
