"""Build the double-clickable app.

    python packaging/build_app.py

Produces ``dist/Film Scan Darkroom.app`` on macOS and
``dist/Film Scan Darkroom/`` with an ``.exe`` inside on Windows.

PyInstaller is a build tool, so it is deliberately not in requirements.txt.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from fsd import __version__  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "packaging" / "fsd.spec"
DIST = REPO / "dist"
APP_NAME = "Film Scan Darkroom"


def check_toolchain() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "PyInstaller is not installed.\n"
            "  pip install pyinstaller\n"
            "It is a build tool, so it is deliberately not in requirements.txt."
        )


def archive(built: Path, version: str) -> Path:
    """Zip the build under a name that carries the version and the platform.

    Named from ``__version__`` rather than typed in by hand, so the archive can
    never drift from the code it contains. ``ditto`` on macOS, not
    ``shutil.make_archive``: an .app is full of symlinks, and a zip that
    resolves them into copies is both much larger and no longer a valid bundle.
    """
    system = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform)
    arch = platform.machine()
    out = DIST / f"{APP_NAME.replace(' ', '-')}-{version}-{system}-{arch}.zip"
    out.unlink(missing_ok=True)

    if sys.platform == "darwin":
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(built), str(out)],
            check=True,
        )
    else:
        made = shutil.make_archive(str(out.with_suffix("")), "zip", built.parent, built.name)
        out = Path(made)
    return out


def main() -> int:
    check_toolchain()

    for stale in (DIST, REPO / "build"):
        if stale.exists():
            shutil.rmtree(stale)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=REPO,
    )
    if result.returncode != 0:
        return result.returncode

    built = DIST / (f"{APP_NAME}.app" if sys.platform == "darwin" else APP_NAME)
    if not built.exists():
        sys.exit(f"Build reported success but {built} is missing.")

    # Skip symlinks: PyInstaller points Contents/Resources at the files in
    # Contents/Frameworks, and following those counts the same bytes twice.
    size_mb = (
        sum(
            f.stat().st_size
            for f in built.rglob("*")
            if f.is_file() and not f.is_symlink()
        )
        / 1e6
    )
    print(f"\nBuilt {built}  ({size_mb:.0f} MB)")
    archive_path = archive(built, __version__)
    print(f"Packaged {archive_path.name}  ({archive_path.stat().st_size / 1e6:.0f} MB)")
    if sys.platform == "darwin":
        print(
            "\nUnsigned, so Gatekeeper will block it on another Mac. To share it:\n"
            "  codesign --deep --force --sign <Developer ID> "
            f'"{built}"\n'
            "  then notarise with notarytool. Locally, right-click > Open."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
