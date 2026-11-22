# Film Scan Darkroom — design

Date: 2026-11-21. Status: draft for approval.

## Intent

Rafat scans film negatives with a Canon camera on a copy stand using one of two
sibling apps — **Canon EDSDK Film Scanner** (USB) or **Canon Smart Film Scanner**
(Wi-Fi). Capture is automated. What is not: for every frame he then crops out the
frame by hand, inverts it, converts to B&W, and sharpens a little.

Film Scan Darkroom is the third app in the family. It opens a roll the scanners
wrote and turns it into finished positives, automatically, with a review screen
to catch the frames automation got wrong. It never talks to a camera.

## Goals

- Open a roll folder (`Roll014/` with `Roll014_FrameNN.CR3` and `roll.json`) and
  propose a finished positive for every frame with no input.
- Per frame, automatically: detect the image area, crop, straighten, mirror if
  scanned emulsion-up, invert (colour or B&W), sharpen.
- Review screen: thumbnail strip of the roll, a large editor for the selected
  frame, low-confidence detections flagged (⚠) so only those need looking at.
- Edit any frame by hand: drag crop corners, rotate, change film/tone/sharpen
  settings. Apply one frame's settings to all frames or a selection.
- Per-stock presets (e.g. "Kentmere 400" remembers its own settings), picked
  automatically from `roll.json`'s `stock` when one matches.
- Edits saved non-destructively in a sidecar next to the roll; re-open and
  re-export any time.
- Export to 16-bit TIFF, JPEG, or both, into `Roll014/positives/`, named after
  the source (`Roll014_Frame07.tif`). Stock/developer/date from `roll.json`
  written into file metadata.
- Mark frames rejected (skipped on export). Contact-sheet export (one JPEG of
  the whole roll).
- Same family look, layout, packaging and repo conventions as the scanners.

## Non-goals

- Camera control or capture of any kind.
- Dust and scratch removal (deferred; large on its own).
- Catalogue/library management across many rolls beyond opening one at a time
  from a list.
- A shared code package across the three apps (see Approaches).

## Anti-goals

- **Never modify, move or delete an original capture.** Every write goes to the
  sidecar or `positives/`.
- Never overwrite a positive silently with something the user did not see: export
  overwrites only its own earlier output for the same frame, and only on an
  explicit export.
- No auto-detection that fails silently: every automatic decision carries a
  confidence, and below threshold the frame is flagged, not quietly accepted.

## Constraints

- Python 3.10–3.13, FastAPI + uvicorn, vanilla HTML/CSS/JS browser UI —
  same stack as the siblings. Dependencies limited to what the EDSDK app already
  ships (numpy, Pillow, tifffile, rawpy, pillow-heif, PyYAML, fastapi, uvicorn,
  python-multipart). **No OpenCV** — the EDSDK app removed it to keep builds small.
- Inputs: CR3/CR2 (and other LibRaw RAW), HEIF, JPEG, TIFF — whatever the
  EDSDK app's `develop.py` accepts.
- Runs alongside a scanner: default port **8001** (scanners use 8000).
- Loopback only by default.

## Approaches

Considered how to get the inversion pipeline, which lives only in the EDSDK app
(`processing/film.py`, `develop.py`, `raw.py`, `tiffio.py`, `grey.py`,
`arrays.py`, ~1,250 lines with tests):

- **A. Copy the processing modules into this repo (chosen).** Matches how the
  family already works (the two scanners carry diverged copies). Standalone repo,
  one `pip install`, own packaging. Cost: fixes must be ported by hand. Each
  copied file gets a header naming its source repo, path and commit.
- B. Extract a shared `filmscan-core` package used by all three. No drift, but
  restructures two working, packaged apps and adds a release step to every
  change. Not justified by any current need.
- C. Add Darkroom as a screen in the EDSDK app. Least code, but Wi-Fi-only users
  would need Canon's SDK to edit, and Rafat asked for its own repo.

Rafat picked A.

Frame detection: classical image processing in numpy (my call, not objected to).
On a copy stand the film base, inter-frame gaps, holder mask and sprocket strip
form strong, uniform brightness edges; an ML model would add a heavy dependency
for little gain. Low-confidence results are flagged for review.

## Design

### Repo layout (mirrors the siblings)

```
Film-Scan-Darkroom/
  README.md  LICENSE (MIT)  THIRD-PARTY-NOTICES.md  CONTEXT.md  TESTING.md
  config.example.yaml  pyproject.toml  requirements.txt  .gitignore
  .github/workflows/tests.yml
  packaging/  entry.py  build_app.py  fsd.spec
  src/fsd/
    __init__.py  config.py  paths.py  sidecar.py  naming.py
    processing/   film.py develop.py raw.py tiffio.py grey.py arrays.py   (copied)
                  detect.py  geometry.py  sharpen.py  metadata.py  contact.py (new)
    darkroom/     roll.py  edits.py  presets.py  export.py
    app/          server.py  session.py  static/{index.html,style.css,app.js}
    mock/         fixture rolls for tests and first launch
  tests/
  docs/superpowers/specs/
```

Package name `fsd`, pip name `film-scan-darkroom`, user folder
`Documents/Film Scan Darkroom/` (frozen) or the repo root (from source), same
rule as the siblings' `paths.user_data_dir()`.

### Compatibility with the scanners

- **Reads** `roll.json` (schema 1, unknown keys tolerated) and the naming scheme
  `{roll}/{roll}_Frame{frame:02d}` with RAW+JPEG pairs sharing a frame number.
  When both exist for a frame, RAW is the source.
- **Never writes** `roll.json` (it belongs to the scanner that may still be
  appending to it). Darkroom state goes in its own sidecar, `darkroom.json`,
  next to it.
- Rolls without `roll.json` still open: frames are discovered from file names,
  falling back to sorted order for files not matching the scheme.
- **Roll library:** `config.yaml` lists roots to look for rolls in. Defaults:
  `~/Documents/Canon EDSDK Film Scanner/captures` and
  `~/Documents/Canon Smart Film Scanner/captures`, plus any folder opened by hand.
- Config file style, `FilmParams` field names and UI vocabulary match the EDSDK
  app so settings mean the same thing in both.

### Pipeline per frame

1. **Decode** the source with the copied `develop.py` path (LibRaw for RAW, linear
   16-bit). Analysis runs on a half-size decode; export uses full size.
2. **Detect** (`detect.py`), on the half-size linear image:
   - Compute row and column brightness profiles and their gradients.
   - Find the inter-frame gaps (vertical bands of uniform film-base density) and
     the top/bottom image edges (where the sprocket/edge-print strip begins).
   - Choose the rectangle that contains the capture centre; this is the target
     frame (neighbouring partial frames are excluded).
   - Estimate skew from the dominant edge angles (fit lines to the four edges);
     straighten and refine the rectangle after rotation.
   - Measure the film base from the inter-frame gap / rebate, giving
     `base_region` to the inverter automatically.
   - Inset the crop by a small configurable margin (default 1% of the short side)
     so no border survives.
   - Confidence from: edge contrast, how well the four edges fit lines, and how
     close the aspect is to the expected format (`config.film.aspect`, default
     3:2 for 35 mm; 1:1, 4:5, 7:6, 4:3 selectable). Below
     `config.detect.min_confidence` → flag ⚠.
   - Orientation: portrait frames stay portrait (no auto-rotation by 90°; the
     user rotates by hand, and the choice is saved).
3. **Geometry** (`geometry.py`): rotate (bicubic, numpy/Pillow), crop, optional
   horizontal mirror (per roll default + per frame override), 90° rotations.
4. **Invert** with the copied `film.py` (`invert_raw` etc.), `mode` colour or B&W,
   base taken from step 2 unless the user picked a region.
5. **Sharpen** (`sharpen.py`): unsharp mask on luminance, amount/radius/threshold,
   default off; applied last.
6. **Write** 16-bit TIFF via `tiffio.py` and/or 8-bit JPEG via Pillow. Metadata
   (`metadata.py`): ImageDescription/XMP with stock, developer, date, roll,
   frame, and "Film Scan Darkroom <version>".

Previews in the UI use the same code on the half-size decode so what is seen is
what is exported.

### Edits sidecar `darkroom.json`

```json
{
  "schema": 1,
  "roll_defaults": { "film": { ...FilmParams... }, "sharpen": {...}, "mirror": false },
  "frames": {
    "7": {
      "source": "Roll014_Frame07.CR3",
      "auto":  { "crop": [x0,y0,x1,y1], "angle": 0.4, "base_region": [...], "confidence": 0.93 },
      "user":  { "crop": null, "angle": null, "rotate90": 0, "mirror": null,
                 "film": { ...overrides... }, "sharpen": null },
      "rejected": false,
      "exported": { "tif": "positives/Roll014_Frame07.tif", "at": "2026-11-21T..." }
    }
  }
}
```

Effective settings = roll defaults ← stock preset ← frame overrides. Coordinates
are fractions of the source image so they survive a change of decode size.
Written atomically (temp file + rename); unreadable sidecar is kept aside as
`darkroom.json.bad-<timestamp>`, never overwritten silently.

### Presets

`presets.yaml` in the user folder: named `FilmParams` + sharpen settings.
Matched to `roll.json` `stock` case-insensitively; "Save as preset" in the UI.

### UI

Same chrome/inspector design language as the EDSDK app's rebuilt UI
(translucent toolbar over the image, 340 px inspector with collapsible sections,
`ⓘ` disclosures for explanations).

- **Roll picker:** list of rolls found under the library roots (name, stock,
  frame count, how many exported), plus "Open folder…".
- **Strip:** thumbnails of every frame with ⚠ / rejected / exported badges.
  Keyboard: ←/→ move, R reject, E export frame.
- **Editor:** large positive preview, crop box with corner handles and rotate
  handle, toggle to show the raw negative with the detected box.
- **Inspector sections:** Crop (auto on/off, angle, aspect lock, reset),
  Film (B&W / colour, exposure, contrast, black/white point, base region pick),
  Sharpen, Output (format, compression, JPEG quality), Roll (defaults, preset).
- **Apply to:** all frames / selected frames / frames still on auto.
- **Export:** whole roll, selected, or current frame; progress bar; cancellable;
  contact sheet as a separate button.
- Processing runs in a worker thread pool; the UI never blocks.

### Packaging, tests, CI

- PyInstaller like the siblings (`packaging/entry.py`, `build_app.py`, `fsd.spec`);
  first launch creates the user folder with a commented `config.yaml` and opens
  the browser. Ships a small mock roll so there is something to see.
- pytest, run in GitHub Actions on macOS and Windows.
- Test fixtures: synthetic negatives generated in code (frame + gaps + sprocket
  strip + known skew + known base) so detection has ground truth; plus the real
  Roll001 frames as an opt-in local test (not committed — they are personal
  scans; path via env var).
- Detection acceptance: on synthetic fixtures, crop within 0.5% of ground truth
  and angle within 0.1°.

## Left to the builder

- Exact detection heuristics and thresholds, within the acceptance targets.
- Thread-pool size, preview resolution, caching of half-size decodes.
- Visual details of the UI within the family style.
- Contact sheet layout.
