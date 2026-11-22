# PyInstaller spec for a double-clickable Film Scan Darkroom.
#
# Build with `python packaging/build_app.py`, not by invoking PyInstaller
# directly -- the script checks the toolchain first and reports what is
# missing, which PyInstaller does not.
#
# The datas are listed explicitly rather than by directory glob: the browser
# UI and the config seed are the only repository files the app needs at
# runtime, so an accidental include should be impossible, not unlikely.

import sys
from pathlib import Path

REPO = Path(SPECPATH).resolve().parent          # noqa: F821 - PyInstaller global
APP_NAME = "Film Scan Darkroom"

datas = [
    # The browser UI. Without this the app starts and serves nothing.
    (str(REPO / "src" / "fsd" / "app" / "static"), "fsd/app/static"),
    # Seeds the user's config on first launch, comments and all.
    (str(REPO / "config.example.yaml"), "."),
]

# Native extensions PyInstaller cannot always see through. rawpy carries
# LibRaw and pillow_heif carries libheif; both load their binaries at import,
# so a missed hook shows up as a working app that cannot open a scan. uvicorn
# selects its loop and protocol modules at runtime, so name those too.
#
# OpenCV is deliberately absent: nothing here uses it.
hiddenimports = [
    "rawpy",
    "pillow_heif",
    "tifffile",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

analysis = Analysis(                             # noqa: F821
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is needed at runtime, and each drags in a lot.
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PySide6", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)                         # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    # Keep a console on Windows: the app prints where its folder is, and a
    # startup failure with no window is indistinguishable from nothing
    # happening at all.
    console=True,
)

collect = COLLECT(                               # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(                                # noqa: F821
        collect,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="com.rafathossain.fsd",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
