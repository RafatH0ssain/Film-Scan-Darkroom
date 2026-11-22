# CONTEXT — handoff for the next session

Film Scan Darkroom is at v0.1.0, all twelve tasks implemented. The suite is
green; the only skipped test is the one that needs a real roll. This file is
the map for whoever picks it up next.

---

## What exists

- `src/fsd/__init__.py` — version string, `0.1.0`.
- `src/fsd/paths.py` — resource vs user-data roots (`Documents/Film Scan Darkroom` when frozen, repo root from source); example config and cache paths.
- `src/fsd/config.py` — defaults → `config.yaml` → `FSD_*` env vars, validation, and `LibraryConfig.resolved_roots()` (`~` expanded, relatives under the user folder).
- `src/fsd/naming.py` — `{roll}_Frame{frame:02d}` paths and collision-free unique names.
- `src/fsd/processing/arrays.py` — percentile/log helpers shared by detection and rendering.
- `src/fsd/processing/codec.py` — small pixel-format conversions.
- `src/fsd/processing/raw.py` — LibRaw/rawpy decode, half-size for analysis.
- `src/fsd/processing/source.py` — one `decode()` for RAW, HEIF, TIFF and JPEG, with `Decoded.linear()` applying each source's own transfer function.
- `src/fsd/processing/tiffio.py` — 16-bit TIFF read/write and the allowed compression modes.
- `src/fsd/processing/film.py` — `FilmParams` and the B&W/colour inversion tone curve.
- `src/fsd/processing/grey.py` — luminance and colour-cast measurements.
- `src/fsd/processing/invert.py` — linear inversion and neutralisation primitives.
- `src/fsd/processing/metadata.py` — metadata written into exported positives.
- `src/fsd/processing/detect.py` — finds the crop, skew and base-sampling region; carries a confidence.
- `src/fsd/processing/geometry.py` — `straighten()` and crop/rotate helpers.
- `src/fsd/processing/sharpen.py` — `SharpenParams` and the gated sharpening pass.
- `src/fsd/processing/render.py` — the one render path shared by preview, thumbnails and export.
- `src/fsd/mock/frames.py` — synthetic negatives with a known mask and density range.
- `src/fsd/mock/synth.py` — synthetic holder scans with known crop/angle, plus a CLI that writes `Example Roll`.
- `src/fsd/darkroom/roll.py` — roll discovery, RAW-beats-JPEG frame grouping, never writes `roll.json`.
- `src/fsd/darkroom/edits.py` — `darkroom.json` model, atomic save with quarantine, settings hashes.
- `src/fsd/darkroom/presets.py` — named presets matched from a stock name.
- `src/fsd/darkroom/export.py` — positives, contact sheet, and macOS `Open in Photos`.
- `src/fsd/app/session.py` — one roll at a time, background analysis, cached previews, export jobs.
- `src/fsd/app/server.py` — FastAPI routes, first-launch seeding, browser entry point.
- `src/fsd/app/static/` — the dark-only browser UI (`index.html`, `style.css`, `app.js`).
- `packaging/` — `entry.py`, `fsd.spec`, `build_app.py` for the double-clickable app.
- `tests/` — unit and HTTP tests; `test_real_roll.py` runs only with `FSD_REAL_ROLL` set.
- `README.md` / `TESTING.md` / `config.example.yaml` — user-facing docs and the config seed.

## Copied files

These carry the header `Copied from Canon-EDSDK-Film-Scanner src/cefs/<path> at
abf739d. Port fixes by hand.` (camera, SDK and main-thread code removed):

- `naming.py`, `paths.py`, `processing/{arrays,codec,film,grey,invert,raw,source,tiffio}.py`, `app/server.py` (startup helpers only), `mock/frames.py`, and `darkroom/roll.py` (`read()` only).
- `THIRD-PARTY-NOTICES.md` and the tests workflow were adapted from the same source commit.

`packaging/{entry.py,fsd.spec,build_app.py}` and `tests/test_packaging.py` were
adapted from the sibling's reference copies of the same vintage; no EDSDK or
camera code was kept.

## How detection works

- Work on a luminance image at most 1000 px wide, in log10 units, so absolute scale (sensor-linear, sRGB-linear, PQ-linear) does not matter.
- Starting at the frame centre, walk outward in 24 thin bands until a barrier is met: the holder's dark bars, or the clear base between frames on a bare strip.
- Fit a line to the hit points on each side to get the skew, straighten, then measure the frame again on the corrected image.
- Refine in holder mode: cut inside the innermost full-height base-bright frame line, then trim the holder lip's shadow; sample the film base from the brightest run on the long (rebate) sides only.
- Confidence falls with a poor aspect/contrast fit; below `detect.min_confidence` the frame is flagged rather than trusted.

## Known limits and parked items

- **Parked (minor):** if quarantining a corrupt `darkroom.json` rename fails, a later save overwrites the bad file — but a rename in the same directory failing means the save fails too.
- **Parked (minor):** `export.format: both` where TIFF succeeds and JPEG fails leaves an unrecorded TIFF; the next export writes a `-1` beside it.
- **Ruling:** `darkroom.json.tmp` is accepted in the roll folder — it is needed for an atomic replace.
- **Ruling:** detection models both the opaque-holder rig and the bare strip, and tolerates a shape fit down to 0.4 log because trimmed real frames are ~1.44:1.
- **Ruling:** the UI is dark-only, like both scanner apps, rather than following `prefers-color-scheme`.
- **Ruling:** Example Roll lives in `<user folder>/rolls/Example Roll`; the library's third default root is the relative path `rolls`, resolved against `user_data_dir()`.
- Analysis decodes at half size and export at full size; a first-run analysis of a large roll is the slow part.
- On Roll001, eight of 72 frames were flagged (flat or thin frames). No automated dust removal.

## Next ideas

- **Auto dust removal is deferred.** It is hard to distinguish from grain without a model, and macOS **Open in Photos** already hands a positive to Apple Photos for blemish removal. Photos keeps its own copy, so Darkroom's files stay pristine.
- Batch re-invert / re-export of an existing roll after changing settings is not built; the current workflow is to adjust and export again.
- Corner-by-corner alignment is unstarted; `corner_sharpness()` exists and is tested.
- Per-stock profiles and exposure locking are ideas for a later version.

## Open questions

- The wireless sibling's repository URL was not available; README names it without a link.
- No screenshots exist yet; README leaves HTML comment placeholders rather than broken images.
- Real-roll results depend on hardware the controller runs (`FSD_REAL_ROLL`); report its per-frame confidences if it is exercised.
