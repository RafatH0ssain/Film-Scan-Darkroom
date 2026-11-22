# Film Scan Darkroom

[![tests](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml)

Turn a roll of scanned film negatives into finished positives — auto-detect, crop, straighten, invert and sharpen every frame, review the roll in a strip, then export.

Colour and black-and-white are both first-class. The negatives can come from either of the two scanners below; Darkroom reads their rolls as-is.

## Preview

<!-- Screenshot placeholder: the library, with a real roll listed. -->
<!-- Screenshot placeholder: the strip with analysis badges and one frame selected. -->
<!-- Screenshot placeholder: the negative view with the crop overlay and a sampled film base. -->
<!-- Screenshot placeholder: a finished positive next to its raw negative. -->

No screenshots yet — this section is a placeholder until a real roll has been captured and reviewed end to end.

## The family

Darkroom is the darkroom, not the camera. It opens rolls from both scanners:

- **[Canon EDSDK Film Scanner](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner)** — USB live view, remote focus and shutter over a copy stand.
- **[Canon Smart Film Scanner](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner)** — the wireless sibling, driven over Canon's CCAPI with no native dependencies.

Both write the same roll layout — `{roll}/{roll}_Frame{frame:02d}` with a `roll.json` beside the files — so a roll scanned on either one opens here without conversion. RAW beats HEIF beats JPEG/TIFF when a frame was saved in more than one format. Darkroom also opens folders with no `roll.json`: frames are found from their names.

## What it does

Open a roll and each frame is analysed in the background: the picture is found inside the scan (including through an opaque holder), the crop and angle are corrected, and the film base is sampled. The strip shows progress per frame and flags low-confidence detections for review.

The negative view shows the raw scan with the crop overlay. Drag the corners to fix a crop, drag the rotate handle to straighten, or pick a rectangle of clear film base to measure the orange mask. Exposure, contrast, black and white points, film mode (B&W or colour), and sharpen amount/radius/threshold all drive the saved positive, not just the preview. Settings can be applied to all frames, to selected frames, or only to frames still untouched. A named preset remembers a stock's look. Reject a frame to leave it out of a roll export.

Export the whole roll, a selection, or one frame to TIFF (lossless deflate by default), JPEG, or both, into `positives/` beside the originals. A contact sheet puts every frame on one page. Exports run as a job with a progress bar and a Cancel button.

## What it never does

**It never modifies your originals.** The scan files and `roll.json` belong to the scanner that may still be appending to them. Darkroom writes only its own sidecar, `darkroom.json`, and files under `positives/`. A corrupt sidecar is renamed to `darkroom.json.bad-<timestamp>` rather than overwritten, so a log you might salvage by hand survives.

The web server binds to `127.0.0.1`. It can change your files, so it is deliberately not exposed to a network you do not trust.

## Photos Clean Up

On macOS, **Open in Photos** hands a finished positive to Apple Photos for blemish removal — dust, scratches and the odd hair that no automated pass should guess at. Photos keeps **its own copy** of the image; editing there does not change the positive in `positives/` or the original scan in the roll. If you want the cleanup to stick, export the edited version from Photos. Re-exporting from Darkroom always starts again from the original negative.

The button only appears on macOS; elsewhere the API reports that it is unavailable.

## Install and run from source

```bash
python -m venv .venv                              # 3.10-3.13, 64-bit
.venv/bin/pip install -r requirements.txt -e .   # Windows: .venv\Scripts\
film-scan-darkroom
```

Then open <http://127.0.0.1:8001/>. From a checkout the user folder *is* the repository root, so `config.yaml`, `presets.yaml` and the cache appear there. The three default library roots include `rolls`, a folder inside the user folder — point it at your scanner's capture folder, or open any roll by hand from the library.

## Build the app

```bash
pip install pyinstaller
python packaging/build_app.py
```

The result is `dist/Film Scan Darkroom.app` on macOS (or `dist/Film Scan Darkroom/` with an `.exe` on Windows), plus a versioned zip. On first launch it creates `Documents/Film Scan Darkroom/`, copies the commented `config.example.yaml` there, and writes a four-frame `Example Roll` into `rolls/` so there is something to look at immediately. macOS builds are unsigned: right-click → Open locally; sharing one properly needs a Developer ID and notarisation.

## Config reference

Every value in `config.example.yaml` can also be set by an environment variable named `FSD_<SECTION>_<KEY>`, uppercased — for example `FSD_SERVER_PORT`. Environment variables win over the file. Library roots are separated by the OS path separator (`:` on macOS/Linux, `;` on Windows) when set through the environment.

| Section | Key | Default | What it sets |
|---|---|---|---|
| `library` | `roots` | `~/Documents/Canon EDSDK Film Scanner/captures`, `~/Documents/Canon Smart Film Scanner/captures`, `rolls` | Folders searched for rolls. `~` expands; relatives resolve against the user data folder. |
| `film` | `mode` | `bw` | Starting inversion mode: `bw` or `color`. |
| `film` | `aspect` | `1.5` | Expected frame aspect ratio; any value greater than zero is accepted. |
| `detect` | `margin` | `0.01` | Inset of the detected crop, as a fraction of the short side. |
| `detect` | `min_confidence` | `0.75` | Frames below this confidence are flagged for review. |
| `export` | `format` | `tiff` | Positive container: `tiff`, `jpeg` or `both`. |
| `export` | `tiff_compression` | `deflate` | Lossless TIFF compression. |
| `export` | `jpeg_quality` | `95` | JPEG quality, 1–100; does not affect TIFF. |
| `export` | `folder` | `positives` | Subfolder of the roll where positives are written. |
| `server` | `host` | `127.0.0.1` | Interface the local web UI binds to. Loopback by default. |
| `server` | `port` | `8001` | Port the local web UI listens on. |

## Known limits

- Auto dust removal is deliberately deferred: Photos Clean Up does it better than a filter that cannot tell dust from grain.
- Analysis uses a half-size decode; export uses the full file.
- Linux runs from source but is not a packaged target.

## Licence

MIT. See [LICENSE](LICENSE).
