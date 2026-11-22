# Film Scan Darkroom

[![tests](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml)

Scanning my negatives was already automated, but after every roll I still sat there cropping out
each frame by hand, inverting it, making it black and white and sharpening it a bit. This does all
of that for a whole roll at once. It opens a roll from either of my scanners,
[Canon EDSDK Film Scanner](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner) or
[Canon Smart Film Scanner](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner), finds the
frame in every capture, crops and straightens it, inverts it, and exports finished positives.

Automatic cropping gets some frames wrong, so it doesn't just write files blind. You get the whole
roll in the browser, the frames it's unsure about are flagged with a reason, and you can drag the
crop or change the settings on any frame before exporting. The original scans are never touched;
it only writes its own `darkroom.json` next to them and the positives.

## Running it

With Python 3.10 to 3.13:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
.venv/bin/film-scan-darkroom
```

It opens <http://127.0.0.1:8001/> and lists the rolls it finds in both scanners' `captures`
folders. Add other folders under `library.roots` in `config.yaml` (copy it from
`config.example.yaml`). Exports go to `positives/` inside the roll as 16-bit TIFF, JPEG or both,
and `export.folder` can point anywhere else, like a Pictures library.

Keys: <kbd>←</kbd><kbd>→</kbd> frames, <kbd>N</kbd> show the negative and crop box, <kbd>R</kbd>
reject, <kbd>E</kbd> export the frame.

For dust and scratches I use Clean Up in Apple Photos, which beats anything I'd build, so on a Mac
each frame has an Open in Photos button. Photos keeps its own copy of whatever you clean up there.

`python packaging/build_app.py` (after `pip install pyinstaller`) builds a double-clickable app.
On first launch it sets up `Documents/Film Scan Darkroom/` with a config and an example roll.

## How it works

Detection is plain numpy. Starting from the centre of the capture, it walks outward until it hits
the black film holder or the clear film between frames, fits a line to each edge to measure the
tilt, then trims the holder's shadow and any frame line showing inside the window. The film base
for the inversion is sampled from the sprocket strip. Previews and exports run through the same
render code, so what you see is what gets written.

| Folder | What's in it |
|---|---|
| `src/fsd/processing/` | decoding, frame detection, cropping, inversion, sharpening |
| `src/fsd/darkroom/` | finding rolls, saved edits, presets, export |
| `src/fsd/app/` | the local web server and browser UI |
| `src/fsd/mock/` | synthetic scans with known answers, for the tests |

`pytest` runs the tests. Set `FSD_REAL_ROLL` to a roll folder to also check a real roll; it works
on a copy.

## Licence

The source is MIT. A packaged build is GPL v2, because pillow-heif's wheel bundles libx265; see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
