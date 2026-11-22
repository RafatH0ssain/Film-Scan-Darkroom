# Film Scan Darkroom

[![tests](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Film-Scan-Darkroom/actions/workflows/tests.yml)

After scanning a roll with my [USB](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner) or
[Wi-Fi](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner) scanner, I used to crop, invert
and sharpen every frame by hand. This does it for the whole roll. It finds each frame, crops and
straightens it, inverts it, and lets me check everything in the browser before exporting.

It never changes the original scans. For dust I use Clean Up in Apple Photos, so there's an
Open in Photos button for that.

## Running it

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
.venv/bin/film-scan-darkroom
```

It opens in your browser and lists the rolls from both scanners. Exports go into a `positives`
folder in the roll, or wherever `export.folder` in `config.yaml` points.

## Licence

MIT. Packaged builds are GPL v2 because of pillow-heif, see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
