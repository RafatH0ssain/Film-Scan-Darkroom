# Film Scan Darkroom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browser-UI desktop app that opens a roll written by the Canon scanners, auto-detects, crops, straightens, inverts and sharpens every frame, lets the user review/fix, and exports positives.

**Architecture:** Python package `fsd` with a FastAPI server and vanilla JS UI, same shape as `cefs`/`csfs`. One render function (`fsd.processing.render.render`) is used for previews (half-size decode) and export (full size), so the screen matches the file. Per-roll state lives in `darkroom.json` beside `roll.json`; originals are never written.

**Tech Stack:** Python 3.10–3.13, numpy, Pillow, tifffile, rawpy, pillow-heif, PyYAML, FastAPI, uvicorn, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-11-21-film-scan-darkroom-design.md`

## Global Constraints

- Repo: `~/Projects/Film-Scan-Darkroom`, package `src/fsd`, pip name `film-scan-darkroom`, version `0.1.0`, MIT, author "Rafat Hossain".
- `requires-python = ">=3.10,<3.14"`. Runtime deps exactly the EDSDK app's: `PyYAML>=6.0,<7`, `numpy>=1.26,<3`, `Pillow>=10.0,<13`, `tifffile>=2024.1,<2027`, `rawpy>=0.22,<1`, `pillow-heif>=0.16,<2`, `fastapi>=0.115,<1`, `uvicorn[standard]>=0.30,<1`, `python-multipart>=0.0.9`. Dev: `pytest>=8.0,<9`, `httpx>=0.27,<1`. **No OpenCV, no scipy, no ML models.**
- User folder: `APP_DIR_NAME = "Film Scan Darkroom"`; frozen → `~/Documents/Film Scan Darkroom/`, from source → repo root.
- Server default `127.0.0.1:8001`.
- **Never write, move or delete anything in a roll folder except `darkroom.json`, `darkroom.json.bad-<timestamp>`, and files under `positives/`.** Never write `roll.json`.
- Copied files from the EDSDK app carry this header as their first docstring line block:
  `Copied from Canon-EDSDK-Film-Scanner src/cefs/<path> at abf739d. Port fixes by hand.`
- Coordinates in `darkroom.json` are fractions (0–1). Commit messages: one line, no body, no trailer.
- Angles are degrees, counter-clockwise positive, meaning "pass this to `rotate()` to straighten".

## Review Focus

1. **A capture replaced or added while the roll is open** (scanner still shooting): reopening or `POST /api/roll/refresh` must pick up new frames and re-detect any frame whose source `mtime_ns`/size changed. → test in Task 7.
2. **Corrupt `darkroom.json`** (half-written, hand-edited): roll still opens with fresh state, bad file renamed to `darkroom.json.bad-<timestamp>`, UI shows a warning. Corrupt `roll.json`: roll opens with empty metadata. → tests in Tasks 6 and 7.
3. **Blank, fogged or leader frames** (uniform image, no edges): detection returns full-frame crop with confidence < `min_confidence`, never raises, and render produces an image without NaN/divide warnings. → test in Task 3 and Task 5.
4. **Export into an unwritable folder or a frame whose source vanished:** that frame's job entry gets `error`, remaining frames still export, job ends `done_with_errors`. → test in Task 8.
5. **Existing `positives/Roll014_Frame07.tif` not made by Darkroom:** never overwritten; export writes `Roll014_Frame07-1.tif`. → test in Task 8.

---

## File map

```
pyproject.toml  requirements.txt  .gitignore  LICENSE  README.md  CONTEXT.md  TESTING.md
THIRD-PARTY-NOTICES.md  config.example.yaml  .github/workflows/tests.yml
packaging/entry.py  packaging/build_app.py  packaging/fsd.spec
src/fsd/__init__.py            __version__ = "0.1.0"
src/fsd/paths.py               adapted copy (APP_DIR_NAME, cache_dir())
src/fsd/config.py              Config dataclasses + load_config
src/fsd/naming.py              copy (sanitise, unique)
src/fsd/processing/{film,raw,tiffio,grey,arrays,codec,invert}.py   copies
src/fsd/processing/source.py   decode() + Decoded       (from develop.py)
src/fsd/processing/detect.py   detect()                 new
src/fsd/processing/geometry.py straighten/crop/orient   new
src/fsd/processing/sharpen.py  unsharp mask             new
src/fsd/processing/render.py   FrameSettings + render() new
src/fsd/processing/metadata.py describe()               new
src/fsd/mock/frames.py         copy (make_negative)
src/fsd/mock/synth.py          synthetic scans with ground truth  new
src/fsd/darkroom/roll.py       roll discovery, roll.json read, library
src/fsd/darkroom/edits.py      darkroom.json model, load/save, effective settings
src/fsd/darkroom/presets.py    presets.yaml
src/fsd/darkroom/export.py     export, contact sheet, open in Photos
src/fsd/app/session.py         open roll, caches, job runner
src/fsd/app/server.py          FastAPI routes, main()
src/fsd/app/static/{index.html,style.css,app.js}
tests/...
```

---

### Task 1: Scaffold the repo and bring over the inversion code

**Files:**
- Create: everything top-level in the file map except packaging and README/CONTEXT/TESTING (Task 12); `src/fsd/__init__.py`, `src/fsd/paths.py`, `src/fsd/naming.py`
- Copy (with header) from `~/Projects/Canon-EDSDK-Film-Scanner` @ `abf739d`: `src/cefs/processing/{film,raw,tiffio,grey,arrays,codec,invert}.py` → `src/fsd/processing/`; `src/cefs/mock/frames.py` → `src/fsd/mock/frames.py`; `src/cefs/naming.py` → `src/fsd/naming.py`; `tests/support.py`, `tests/test_film.py` → `tests/`
- Test: `tests/test_paths.py`, copied `tests/test_film.py`

**Interfaces:**
- Produces: `fsd.paths.{REPO_ROOT, APP_DIR_NAME, is_frozen(), resource_dir(), user_data_dir(), static_dir(), example_config_path(), cache_dir()}`; `cache_dir() -> Path` = `user_data_dir() / "cache"`, created on call. All copied functions keep their names and signatures (`FilmParams`, `analyse`, `sample_base`, `invert_raw`, `invert_pq`, `invert_preview`, `linear16_to_linear`, `pq16_to_linear_lut`, `srgb_to_linear`, `decode_raw`, `is_raw`, `RawUnavailable`, `write_tiff`, `read_tiff`, `COMPRESSIONS`, `naming.unique`, `naming.sanitise`).

- [ ] **Step 1:** Copy the files listed; rewrite `cefs.` imports to `fsd.`; add the header line. In `tiffio.write_tiff`, add keyword args `description: str | None = None, software: str | None = None` passed to `tifffile.imwrite(..., description=description, software=software)` (header note: "adds metadata kwargs").
- [ ] **Step 2:** Write `pyproject.toml` (setuptools, `src` layout, deps from Global Constraints, `[project.optional-dependencies] dev`, `[project.scripts] film-scan-darkroom = "fsd.app.server:main"`, `[tool.pytest.ini_options] testpaths=["tests"], pythonpath=["src","tests"]`), `requirements.txt` (same pins, commented like the siblings'), `.gitignore` (Python, `build/`, `dist/`, `config.yaml`, `cache/`, `presets.yaml`, `rolls/`), MIT `LICENSE`, `THIRD-PARTY-NOTICES.md` (LibRaw via rawpy LGPL/CDDL, pillow-heif/libheif LGPL, others MIT/BSD — copy the EDSDK app's file and drop EDSDK lines), and `.github/workflows/tests.yml` copied from the EDSDK app with the comment about camera/SDK removed (keep the "No OpenCV" step).
- [ ] **Step 3: Write failing test** `tests/test_paths.py::test_user_data_dir_from_source_is_repo_root` (`assert user_data_dir() == REPO_ROOT`) and `::test_app_dir_name` (`assert APP_DIR_NAME == "Film Scan Darkroom"`).
- [ ] **Step 4:** Run `pip install -r requirements.txt -e ".[dev]" && pytest -q`. Expected: test_paths fails until `paths.py` exists; then implement `paths.py` (adapted copy of `cefs/paths.py`, + `cache_dir()`).
- [ ] **Step 5:** Run `pytest -q`. Expected: all pass, including copied `test_film.py`.
- [ ] **Step 6: Commit** `git add -A && git commit -m "Scaffold repo and copy inversion code from EDSDK scanner"`

### Task 2: Source decoding

**Files:**
- Create: `src/fsd/processing/source.py`
- Test: `tests/test_source.py`

**Interfaces:**
- Consumes: Task 1 copies.
- Produces:
  ```python
  class SourceError(RuntimeError): ...
  RAW_EXTENSIONS: frozenset[str]   # whatever raw.is_raw accepts, lower-case with dot
  HEIF_EXTENSIONS = {".hif", ".heif", ".heic"}
  DIRECT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
  @dataclass(frozen=True)
  class Decoded:
      kind: Literal["raw", "pq", "srgb16", "srgb8"]
      pixels: np.ndarray        # (H, W, 3) RGB; uint16 for raw/pq/srgb16, uint8 for srgb8
      def linear(self) -> np.ndarray   # float32 (H, W, 3) in the kind's own linear scale
  def decodable(path: Path) -> bool
  def decode(path: Path, *, half: bool = False) -> Decoded
  ```
- `half=True` halves each dimension: RAW via `decode_raw(path, half=True)`, others via Pillow `reduce(2)` / numpy `[::2, ::2]`.
- Port HEIF reading (PQ detection from NCLX) from EDSDK `develop.py:_read_heif` with the copy header. 16-bit TIFFs (`srgb16`) are treated as sRGB-encoded 16-bit.

- [ ] **Step 1: Failing tests:** `test_decode_jpeg_is_srgb8` (write a 64×48 JPEG with Pillow → `kind == "srgb8"`, `pixels.shape == (48, 64, 3)`, dtype uint8); `test_decode_half_halves_dimensions` (same JPEG, `half=True` → `(24, 32, 3)`); `test_decode_16bit_tiff` (write uint16 via `write_tiff` → `kind == "srgb16"`, dtype uint16); `test_decode_unknown_extension_raises` (`.txt` → `SourceError`); `test_decode_missing_file_raises` → `SourceError`; `test_linear_is_float32_in_0_1` for srgb8 and srgb16.
- [ ] **Step 2:** `pytest tests/test_source.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement `source.py`.
- [ ] **Step 4:** `pytest tests/test_source.py -v` → PASS. RAW is covered by Task 12's opt-in real-roll test (no CR3 fixtures are committed).
- [ ] **Step 5: Commit** `git commit -m "Add source decoding"`

### Task 3: Synthetic scans and frame detection

**Files:**
- Create: `src/fsd/mock/synth.py`, `src/fsd/processing/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Produces:
  ```python
  # synth.py
  @dataclass(frozen=True)
  class Truth:
      crop: tuple[float, float, float, float]   # x0,y0,x1,y1 fractions of the straightened image
      angle: float                               # degrees to pass to rotate() to straighten
      base: tuple[float, float, float]           # linear film base used
  def synth_scan(width=1200, height=800, *, frame_aspect=1.5, angle=0.0,
                 base=(0.80, 0.55, 0.35), portrait=False, holder=False,
                 blank=False, seed=0) -> tuple[np.ndarray, Truth]
      # returns uint16 linear RGB (H, W, 3)
  # detect.py
  CropBox = tuple[float, float, float, float]   # x0, y0, x1, y1
  Region = tuple[float, float, float, float]    # x, y, w, h  (sample_base convention)
  @dataclass
  class Detection:
      crop: CropBox
      angle: float
      base_region: Region | None   # in source (unrotated) fractions
      confidence: float            # 0..1
      reasons: list[str]           # human-readable, e.g. "no gap found on the right"
  def detect(linear: np.ndarray, *, aspect: float = 1.5, margin: float = 0.01) -> Detection
  ```
- `synth_scan` draws what Rafat's captures contain (seen in `Roll001_Frame03.CR3`): a centred frame of image content (smooth gradients + random blobs + grain, darker than base) filling ~75% of width; partial neighbour frames left and right separated by clear-base gaps ~4% of width; top and bottom clear-base strips ~12% of height containing brighter sprocket holes and darker edge-print blocks; `holder=True` adds a dark, low-variance border outside the strips; the whole film rotated by `-angle` about the centre. `portrait=True` swaps to a 2:3 frame in a vertical strip. `blank=True` makes the frame area uniform base (an unexposed frame).

**Algorithm for `detect` (the part the tests do not fully determine):**
1. Luminance of `linear`, downsample so the long side ≤ 1000 px, `log(lum + 1e-4)`.
2. `base_level` = 99th percentile; a pixel is **non-image** if it is within 0.15 log-units of `base_level` or brighter (clear base, sprocket holes), or if `holder` is detected: below the 2nd percentile with local std below the median local std (dark mask).
3. Skew: for each of the four sides, in 24 bands perpendicular to that side, walk outward from the centre and record the first position where the non-image fraction of a 5-px window exceeds 0.6. Fit a line (least squares, drop the worst 25% of points, refit). `angle` = negative of the median of the four fitted slopes' angles (only sides with ≥ 8 inlier points count). Clamp to ±5°.
4. Rotate the luminance by `angle` (Pillow `F` mode, bicubic, `expand=False`) and repeat step 3's outward walk to get the four edges as the median band position; that is the frame box. A side with no edge found uses the image border and appends a reason.
5. Inset each side by `margin × short side`.
6. `base_region`: the largest axis-aligned rectangle inside a gap or strip adjacent to the frame that is entirely non-image and not a sprocket hole (exclude pixels > base_level + 0.3 log-units), expressed in source fractions; `None` if under 0.2% of the image area.
7. Confidence = product of: sides found (1.0, 0.75, 0.5, 0.25, 0.1 for 4..0), line fit (`max(0, 1 - rms_px / 6)` averaged over sides), aspect (`max(0, 1 - abs(log(r / aspect)) / 0.25)` where `r` = long/short of the box, so portrait and landscape both match), and contrast (`min(1, (median_frame_log_distance_from_base) / 0.5)`).

- [ ] **Step 1: Failing tests** (all on `synth_scan`, tolerances from the spec):
  ```python
  @pytest.mark.parametrize("angle", [0.0, 0.8, -1.5, 3.0])
  def test_detects_crop_and_angle(angle):
      img, truth = synth_scan(angle=angle)
      d = detect(linear16_to_linear(img))
      assert abs(d.angle - truth.angle) <= 0.1
      assert all(abs(a - b) <= 0.005 for a, b in zip(d.crop, truth.crop))
      assert d.confidence >= 0.8
  def test_portrait_frame():            # portrait=True → same tolerances, box taller than wide
  def test_holder_border():             # holder=True → same tolerances
  def test_base_region_samples_base():  # sample_base(linear, d.base_region) within 2% of truth.base per channel
  def test_crop_excludes_neighbours():  # crop x0 > left gap end, x1 < right gap start (from truth)
  def test_blank_frame_low_confidence():  # blank=True → no exception, confidence < 0.5, reasons non-empty
  def test_uniform_image_does_not_raise():  # np.full((400,600,3), 0.5, float32) → confidence < 0.5
  def test_wrong_aspect_lowers_confidence():  # synth frame_aspect=1.0 with detect(aspect=1.5) → confidence < detect(aspect=1.0)
  ```
  `truth.crop` in `synth.py` is the true frame box *already inset by the default 1% margin*, so 0.005 is the spec's "within 0.5% of ground truth".
- [ ] **Step 2:** `pytest tests/test_detect.py -v` → FAIL.
- [ ] **Step 3:** Implement `synth.py`, then `detect.py`.
- [ ] **Step 4:** `pytest tests/test_detect.py -v` → PASS. Also time `detect` on a 3000×2000 synth: must be < 1.5 s (`test_detect_is_fast`, mark `@pytest.mark.slow` only if CI is flaky).
- [ ] **Step 5: Commit** `git commit -m "Add synthetic scans and frame detection"`

### Task 4: Geometry and sharpening

**Files:**
- Create: `src/fsd/processing/geometry.py`, `src/fsd/processing/sharpen.py`
- Test: `tests/test_geometry.py`, `tests/test_sharpen.py`

**Interfaces:**
- Produces:
  ```python
  # geometry.py  (all keep dtype and channel count)
  def straighten(img: np.ndarray, angle: float) -> np.ndarray   # same size, per-channel Pillow "F" bicubic, fill = edge median; angle==0 returns img unchanged
  def crop(img: np.ndarray, box: CropBox) -> np.ndarray          # fractions → pixels, at least 1×1
  def orient(img: np.ndarray, rotate90: int, mirror: bool) -> np.ndarray   # rotate90 in 0..3 CCW quarter turns via np.rot90, then mirror = np.fliplr
  # sharpen.py
  @dataclass(frozen=True)
  class SharpenParams:
      amount: float = 0.0      # 0 = off; 0..3
      radius: float = 1.0      # px at full resolution, 0.3..5
      threshold: float = 0.0   # 0..0.1 in 0-1 luminance units
  def sharpen(rgb16: np.ndarray, p: SharpenParams, *, scale: float = 1.0) -> np.ndarray
      # uint16 RGB in/out. Unsharp mask on luminance only, added equally to all channels, clipped.
      # Blur = 3 passes of a box blur via cumulative sums (approximates Gaussian). `scale` multiplies radius (0.5 for half-size previews).
  ```
  Mark the box-blur choice with `# lazy: 3x box blur approximates a Gaussian; switch to a true separable Gaussian if halos show.`

- [ ] **Step 1: Failing tests:** `test_straighten_zero_is_identity`; `test_straighten_round_trip` (rotate a uint16 test card by 2° then −2°, centre 50% region mean abs error < 1% of range); `test_crop_fractions` (`crop(np.zeros((100,200,3)), (0.1,0.2,0.6,0.7)).shape == (50,100,3)`); `test_crop_degenerate_box_returns_1px`; `test_orient_rot_and_mirror` (2×3 array known values for rotate90 = 1 and mirror=True); `test_sharpen_off_is_identity`; `test_sharpen_increases_edge_contrast` (step edge: max-min across the edge grows with amount=1); `test_sharpen_threshold_leaves_flat_noise` (low-amplitude noise unchanged within 1 LSB·10 when threshold=0.05); `test_sharpen_keeps_dtype_and_clips`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add geometry and sharpening"`

### Task 5: The one render pipeline

**Files:**
- Create: `src/fsd/processing/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Decoded` (Task 2), `Detection`/`CropBox`/`Region` (Task 3), geometry/sharpen (Task 4), `FilmParams`, `sample_base`, `analyse`, `invert_raw`, `invert_pq`, `invert_preview`, `pq16_to_linear_lut`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class FrameSettings:
      crop: CropBox = (0.0, 0.0, 1.0, 1.0)
      angle: float = 0.0
      rotate90: int = 0
      mirror: bool = False
      base_region: Region | None = None
      film: FilmParams = FilmParams(mode="bw")
      sharpen: SharpenParams = SharpenParams()
  def render(decoded: Decoded, s: FrameSettings, *, scale: float = 1.0) -> np.ndarray   # uint16 RGB
  ```
- Order (fixed by the spec, base measured before cropping):
  1. `lin = decoded.linear()`; `base = sample_base(lin, region=s.base_region)` (region `None` → percentile over the whole capture, which includes the rebate).
  2. `params = s.film.replace(base=base)`.
  3. `px = crop(straighten(decoded.pixels, s.angle), s.crop)`; `lin_c = crop(straighten(lin, s.angle), s.crop)`.
  4. `measured = analyse(lin_c, params)`; invert by kind: raw → `invert_raw(px, params, measured)`; pq → `invert_pq`; srgb16 → `invert_raw((lin_c * 65535).astype(np.uint16), params, measured)`; srgb8 → `invert_preview(px[..., ::-1], params, measured)[..., ::-1]` then `× 257` to uint16.
  5. `orient(..., s.rotate90, s.mirror)`; 6. `sharpen(..., s.sharpen, scale=scale)`.
  `FilmParams` default for Darkroom is `mode="bw"` (Rafat's workflow); colour remains selectable.

- [ ] **Step 1: Failing tests:** `test_render_inverts` (synth scan as `Decoded("raw", img)` with truth crop → output mean of frame centre is *darker* where synth content was *lighter* on the negative; assert correlation < −0.8 between input and output luminance over the crop); `test_render_output_shape_follows_crop_and_rotate90`; `test_render_half_scale_matches_full` (render half-size decode and full, downscale full by 2, mean abs diff < 2% of range — this is the "preview equals export" guarantee); `test_render_blank_frame_no_nan` (`blank=True`, with `np.errstate(all="raise")` → no exception, no NaN); `test_render_bw_is_neutral` (mode bw → R==G==B).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add render pipeline"`

### Task 6: Config, roll discovery and the library

**Files:**
- Create: `src/fsd/config.py`, `config.example.yaml`, `src/fsd/darkroom/__init__.py`, `src/fsd/darkroom/roll.py`
- Test: `tests/test_config.py`, `tests/test_roll.py`

**Interfaces:**
- Produces:
  ```python
  # config.py — same loading style as cefs.config (dataclass sections, _coerce, unknown key = error naming the key, env override FSD_<SECTION>_<KEY>)
  @dataclass class LibraryConfig:  roots: list[str] = [ "~/Documents/Canon EDSDK Film Scanner/captures",
                                                        "~/Documents/Canon Smart Film Scanner/captures" ]
  @dataclass class FilmConfig:     mode: str = "bw"; aspect: float = 1.5   # 1.0, 1.25, 7/6, 4/3 allowed too (any >0)
  @dataclass class DetectConfig:   margin: float = 0.01; min_confidence: float = 0.75
  @dataclass class ExportConfig:   format: str = "tiff"   # "tiff" | "jpeg" | "both"
                                   tiff_compression: str = "deflate"; jpeg_quality: int = 95; folder: str = "positives"
  @dataclass class ServerConfig:   host: str = "127.0.0.1"; port: int = 8001
  @dataclass class Config:         library, film, detect, export, server
  def load_config(path: Path | None = None) -> Config     # default user_data_dir()/"config.yaml"; missing file → defaults
  # roll.py
  @dataclass class FrameFile:  number: int; source: Path; companions: list[Path]   # source = RAW if present else HEIF else JPEG/TIFF
  @dataclass class Roll:       path: Path; name: str; metadata: dict; frames: list[FrameFile]; warnings: list[str]
  def open_roll(path: Path) -> Roll
  def find_rolls(roots: list[Path]) -> list[Path]   # dirs (depth ≤ 2) containing roll.json or ≥1 decodable file; newest mtime first
  ```
- Frame numbering: parse `_Frame(\d+)` from the stem. A `-N` uniqueness suffix (`Roll001_Frame01-1.CR3`, a reshoot written by the scanner's `naming.unique`) gets its own `FrameFile` with `number` = next free integer after all parsed numbers, plus a warning naming the file. Files not matching the scheme are numbered after the matched ones in name order. Ignore `*-positive.*`, anything in `positives/`, and dotfiles.
- `roll.json` is read by a copy of `cefs/sidecar.py:read` placed in `roll.py` (copy header): unreadable → `metadata = {}` and a warning.

- [ ] **Step 1: Failing tests:** config: `test_defaults`, `test_unknown_key_is_error`, `test_env_override_port`. roll: `test_raw_wins_over_jpeg_pair` (Frame07.CR3 + Frame07.JPG → one frame, source .CR3 — create empty files and monkeypatch `decodable` or use real extensions only for discovery); `test_positive_files_ignored`; `test_unmatched_names_follow`; `test_duplicate_suffix_gets_own_frame_and_warning`; `test_corrupt_roll_json_opens_with_warning`; `test_find_rolls_orders_newest_first`; `test_missing_root_is_skipped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add config and roll discovery"`

### Task 7: Edits sidecar and presets

**Files:**
- Create: `src/fsd/darkroom/edits.py`, `src/fsd/darkroom/presets.py`
- Test: `tests/test_edits.py`, `tests/test_presets.py`

**Interfaces:**
- Consumes: `FrameSettings`, `Detection`, `FilmParams`, `SharpenParams`, `Roll`.
- Produces:
  ```python
  SIDECAR = "darkroom.json"; SCHEMA = 1
  @dataclass class FrameEdit:
      source: str                     # file name relative to roll
      fingerprint: tuple[int, int] | None   # (mtime_ns, size) of source when auto was computed
      auto: Detection | None
      user: dict                      # sparse overrides: crop, angle, rotate90, mirror, base_region, film (partial dict), sharpen (partial dict)
      rejected: bool = False
      exported: dict = {}             # {"tif": "positives/x.tif", "jpg": ..., "at": iso, "settings_hash": str}
  @dataclass class Edits:
      roll_defaults: dict             # {"film": {...}, "sharpen": {...}, "mirror": bool, "rotate90": int}
      preset: str | None
      frames: dict[int, FrameEdit]
      warnings: list[str]
  def load(roll_dir: Path) -> Edits              # corrupt → rename to darkroom.json.bad-YYYYmmdd-HHMMSS, fresh Edits, warning
  def save(roll_dir: Path, edits: Edits) -> None # atomic: write darkroom.json.tmp, os.replace
  def needs_detection(edit: FrameEdit | None, source: Path) -> bool   # no auto, or fingerprint differs
  def effective(edits: Edits, n: int, preset: dict | None, cfg: Config) -> FrameSettings
      # precedence: built-in defaults (cfg.film.mode) ← preset ← roll_defaults ← auto (crop, angle, base_region) ← user
  def settings_hash(s: FrameSettings) -> str     # sha1 of canonical JSON; used to know whether an export is current
  APPLY_FIELDS = ("film", "sharpen", "mirror", "rotate90")   # what "apply to" copies; crop/angle stay per frame
  def apply(edits: Edits, from_n: int, to: list[int], fields: tuple[str, ...] = APPLY_FIELDS) -> None
  # presets.py
  def load_presets(path: Path) -> dict[str, dict]            # user_data_dir()/"presets.yaml"; missing → {}
  def save_preset(path: Path, name: str, film: dict, sharpen: dict) -> None
  def match_preset(presets: dict[str, dict], stock: str) -> str | None   # case-insensitive, whitespace-collapsed exact match
  ```

- [ ] **Step 1: Failing tests:** `test_round_trip`; `test_save_is_atomic` (monkeypatch `os.replace` to raise → original file unchanged); `test_corrupt_sidecar_renamed_and_warned`; `test_needs_detection_on_changed_fingerprint`; `test_effective_precedence` (preset sets contrast 1.5, roll default 1.6, user 1.7 → 1.7; remove user → 1.6); `test_apply_copies_film_not_crop`; `test_settings_hash_stable_and_sensitive`; presets: `test_match_case_insensitive` ("kentmere  400" matches "Kentmere 400"), `test_missing_file_empty`, `test_save_then_load`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add edits sidecar and presets"`

### Task 8: Export, metadata, contact sheet, Open in Photos

**Files:**
- Create: `src/fsd/processing/metadata.py`, `src/fsd/darkroom/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `decode`, `render`, `effective`, `settings_hash`, `write_tiff`, `naming.unique`, `Roll`, `Edits`, `ExportConfig`.
- Produces:
  ```python
  # metadata.py
  def describe(roll: Roll, frame: int) -> str
      # "Roll014 frame 7 · Kentmere 400 · ID-11 · 2026-11-02" — omit empty parts
  SOFTWARE = f"Film Scan Darkroom {fsd.__version__}"
  # export.py
  @dataclass class ExportResult: frame: int; files: list[Path]; error: str | None
  def export_frame(roll: Roll, edits: Edits, n: int, cfg: Config, preset: dict | None) -> ExportResult
  def export_frames(roll, edits, frames: list[int], cfg, preset, *, progress: Callable[[int, int], None] | None = None,
                    cancelled: Callable[[], bool] = lambda: False) -> list[ExportResult]
      # skips rejected; one frame's failure is recorded and the loop continues; saves edits after each success
  def contact_sheet(roll: Roll, edits: Edits, thumbs: dict[int, Path], cfg: Config) -> Path
      # JPEG at positives/<roll>_contact.jpg; 6 columns, 400 px cells, frame number under each, rejected omitted
  def photos_available() -> bool          # sys.platform == "darwin"
  def open_in_photos(path: Path) -> None  # subprocess.run(["open", "-a", "Photos", str(path)], check=True); raise RuntimeError off macOS
  ```
- Output name: `positives/<source stem>.tif` / `.jpg`. If the target exists and is **not** the path recorded in that frame's `exported`, use `naming.unique()` (→ `-1`, `-2`…). If it is the recorded path, overwrite.
- TIFF: `write_tiff(..., compression=cfg.export.tiff_compression, description=describe(...), software=SOFTWARE)`. JPEG: 8-bit (`>> 8`), quality from config, EXIF 0x010E = description, 0x0131 = SOFTWARE.
- Record `exported = {"tif"/"jpg": relative path, "at": iso, "settings_hash": ...}`.

- [ ] **Step 1: Failing tests** (roll built in `tmp_path` from `synth_scan` saved as 16-bit TIFF sources):
  `test_export_writes_positive_and_records`; `test_export_never_touches_sources` (hash every non-`positives/`, non-`darkroom.json` file before/after → equal); `test_existing_foreign_file_not_overwritten` (Review Focus 5); `test_reexport_overwrites_own_file`; `test_rejected_skipped`; `test_failure_continues` (one source deleted, folder for another made read-only via monkeypatched `write_tiff` raising → those results have `error`, others have files; Review Focus 4); `test_tiff_metadata` (`tifffile.TiffFile` page `description` and `software` match); `test_jpeg_exif_description`; `test_contact_sheet_dims`; `test_open_in_photos_off_mac_raises` (monkeypatch `sys.platform = "linux"`); `test_open_in_photos_calls_open` (monkeypatch `subprocess.run`, assert args).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add export, metadata, contact sheet and Open in Photos"`

### Task 9: Session — open a roll, analyse in the background, cache previews

**Files:**
- Create: `src/fsd/app/__init__.py`, `src/fsd/app/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  ```python
  class Session:
      def __init__(self, cfg: Config, *, workers: int = max(1, os.cpu_count() // 2))
      def library(self) -> list[dict]          # [{path, name, stock, frames, exported, flagged}]
      def open(self, path: Path) -> dict       # loads roll + edits + preset match, starts analysis job, returns state()
      def refresh(self) -> dict                # re-scan roll dir; new/changed frames queued for detection (Review Focus 1)
      def state(self) -> dict                  # roll name, metadata, preset, warnings, analysing: {done, total},
                                               # frames: [{n, source, status: "pending"|"ready"|"error", confidence,
                                               #          flagged, rejected, exported_current: bool, settings: FrameSettings as dict}]
      def thumb(self, n: int) -> bytes         # JPEG, long side 320; disk cache cache_dir()/<sha1(source, fingerprint, settings_hash)>.jpg
      def preview(self, n: int, view: Literal["positive", "negative"]) -> bytes
          # JPEG, long side 1600. positive = render(half decode, effective, scale=0.5); negative = straightened-not-cropped
          # half decode with no inversion, gamma-encoded for display (the UI draws the crop box itself)
      def edit(self, n: int, changes: dict) -> dict      # merge into user overrides, save, return frame state
      def reset(self, n: int) -> dict
      def apply(self, from_n: int, to: Literal["all", "selected", "auto"], frames: list[int] | None) -> dict
      def reject(self, n: int, rejected: bool) -> dict
      def set_roll_defaults(self, changes: dict) -> dict
      def save_preset(self, name: str, from_n: int) -> dict
      def start_export(self, frames: list[int] | Literal["all"]) -> str   # job id
      def job(self, job_id: str) -> dict       # {kind, done, total, status: running|done|done_with_errors|cancelled, results}
      def cancel(self, job_id: str) -> None
      def contact_sheet(self) -> Path
      def open_in_photos(self, n: int) -> Path # exports first if not exported_current; prefers .tif
  ```
- Detection runs `decode(half=True)` → `detect(linear, aspect=cfg.film.aspect, margin=cfg.detect.margin)` in a `ThreadPoolExecutor`; results saved to edits as they land (lock around edits + save). `flagged = confidence < cfg.detect.min_confidence`.
- Half-size decodes cached in an LRU of 6 (`functools.lru_cache` keyed by `(path, fingerprint)`). `# lazy: in-memory LRU of 6 half decodes (~300 MB); move to a disk cache if memory is tight.`
- "auto" target in `apply` = frames with no user overrides for the applied fields.

- [ ] **Step 1: Failing tests** (synth roll in `tmp_path`): `test_open_analyses_all_frames` (wait on job ≤ 20 s; all `status == "ready"`); `test_blank_frame_flagged`; `test_refresh_picks_up_new_and_changed` (add a file, touch another with new content → both re-detected); `test_edit_persists_across_reopen`; `test_apply_to_all`; `test_export_job_progress_and_result`; `test_cancel_export`; `test_preview_is_jpeg_of_expected_size`; `test_corrupt_darkroom_json_warns` (state()["warnings"] non-empty).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add session with background analysis and caches"`

### Task 10: HTTP API and server entry

**Files:**
- Create: `src/fsd/app/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Session`, `load_config`, `paths`.
- Produces: `create_app(config: Config | None = None) -> FastAPI`, `prepare_user_folder() -> Path | None`, `open_browser_when_ready(url, server)`, `main(argv: list[str] | None = None) -> int` — the last three adapted from `cefs/app/server.py` (copy header). Routes (JSON unless noted):

  | Method & path | Body | Returns |
  |---|---|---|
  | GET `/` | | `index.html` |
  | GET `/api/status` | | `{app, version, platform, photos_available}` |
  | GET `/api/library` | | `Session.library()` |
  | POST `/api/roll/open` | `{path}` | `state()`; 404 if not a dir |
  | POST `/api/roll/refresh` | | `state()` |
  | GET `/api/roll` | | `state()`; 409 if none open |
  | POST `/api/roll/defaults` | partial dict | `state()` |
  | GET `/api/frames/{n}/thumb` | | `image/jpeg` |
  | GET `/api/frames/{n}/preview?view=positive\|negative` | | `image/jpeg` |
  | POST `/api/frames/{n}/edit` | partial overrides | frame state |
  | POST `/api/frames/{n}/reset` | | frame state |
  | POST `/api/frames/{n}/reject` | `{rejected}` | frame state |
  | POST `/api/apply` | `{from, to, frames?}` | `state()` |
  | GET `/api/presets` / POST `/api/presets` | — / `{name, from}` | presets |
  | POST `/api/export` | `{frames: "all" \| [n]}` | `{job}` |
  | GET `/api/jobs/{id}` / POST `/api/jobs/{id}/cancel` | | job dict |
  | POST `/api/contact-sheet` | | `{path}` |
  | POST `/api/frames/{n}/open-in-photos` | | `{path}`; 409 off macOS |

  Unknown frame → 404 with a message. Validation errors → 422 with the offending field named.

- [ ] **Step 1: Failing tests** with `fastapi.testclient.TestClient` on a synth roll: one test per row's happy path, plus `test_unknown_frame_404`, `test_no_roll_409`, `test_bad_edit_422` (`{"angle": "x"}`), `test_open_in_photos_409_off_mac`, `test_binds_loopback_by_default` (`load_config().server.host == "127.0.0.1"`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `git commit -m "Add HTTP API and server entry"`

### Task 11: Browser UI

**Files:**
- Create: `src/fsd/app/static/index.html`, `style.css`, `app.js`
- Test: `tests/test_static.py` (served, references resolve); manual checklist in `TESTING.md` (Task 12)

**Interfaces:**
- Consumes: the API table in Task 10 only.
- Look: reuse the EDSDK app's `style.css` tokens and component styles (copy header) — translucent toolbar over the image, 340 px inspector with `<details>` sections, `ⓘ` disclosures, light/dark via `prefers-color-scheme`. Layout per the spec sketch: header (roll name · stock · developer, Library, Export ▸), strip of thumbnails with ⚠ / ✕ rejected / ✓ exported-current badges, editor, inspector.
- Behaviour:
  - Library view on start (or `?roll=` query): list from `/api/library`, "Open folder…" takes a typed path (browsers cannot return real paths from a picker).
  - While analysing, a progress bar; thumbs fill in as frames become ready (poll `/api/roll` every 500 ms while `analysing.done < total`).
  - Editor: positive preview; "Show negative" toggle shows the negative view with the crop box as an SVG overlay with 4 corner handles and a rotate handle; dragging updates `crop`/`angle` locally and POSTs `edit` on release; aspect lock checkbox (uses `config` aspect, orientation from the current box).
  - Inspector sections: Crop (Auto on/off = clear user crop/angle, angle slider ±5° step 0.05, ↺/↻ quarter-turn, Mirror, Reset), Film (B&W | Colour, exposure, contrast, black/white point, "Pick base" = drag a rectangle on the negative view → `base_region`), Sharpen (amount, radius, threshold), Output (read-only from config, with `ⓘ` saying where to change it), Roll (preset name, "Save as preset…", roll defaults).
  - Apply to: all / selected (shift/cmd-click in strip) / frames still on auto.
  - Keys: ←/→ frame, R reject, E export frame, N negative toggle, ⌘/Ctrl-Z not required.
  - Export ▸ menu: roll, selected, this frame, contact sheet; progress with Cancel; result list shows per-frame errors.
  - Open in Photos button only when `photos_available`; its `ⓘ`: "Photos imports a copy. Edits made there stay in Photos."
  - Warnings from `state().warnings` shown in a dismissible banner.

- [ ] **Step 1: Failing test:** `test_index_served_and_assets_resolve` (GET `/` 200; every `src=`/`href=` to a local asset returns 200).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the three files.
- [ ] **Step 4:** Run → PASS. Then run the app (`python -m fsd.app.server` with `FSD_LIBRARY_ROOTS` pointing at a synth roll written by a helper `python -m fsd.mock.synth --write <dir>`; add that CLI to `synth.py`) and check every behaviour bullet in the browser preview, including dark mode and a 1280×800 window.
- [ ] **Step 5: Commit** `git commit -m "Add browser UI"`

### Task 12: Packaging, docs, real-roll check

**Files:**
- Create: `packaging/entry.py`, `packaging/build_app.py`, `packaging/fsd.spec` (adapted copies of the EDSDK app's, without EDSDK steps and without the main-thread note), `README.md`, `CONTEXT.md`, `TESTING.md`
- Modify: `src/fsd/app/server.py` (first-launch: create user folder, write commented `config.yaml` from `config.example.yaml`, write a mock roll `Example Roll/` from `synth` with 4 frames if the library is empty)
- Test: `tests/test_packaging.py` (spec lists `fsd/app/static` and `config.example.yaml` as datas; adapted from EDSDK's), `tests/test_real_roll.py`

**Interfaces:** none new.

- README follows the siblings' shape: title + tests badge (`RafatH0ssain/Film-Scan-Darkroom`), one-line pitch, screenshot placeholders, "The family" section linking both scanners and saying Darkroom opens their rolls, Install/Run, "What it never does" (touch originals), Photos Clean Up section, Config reference, Licence.
- `CONTEXT.md`: handoff for the next session — what exists, copied-files list with source commit `abf739d`, open questions, deferred items (auto dust removal).
- `TESTING.md`: manual checklist mirroring Task 11's behaviours plus "open a real roll from each scanner".

- [ ] **Step 1: Failing tests:** `test_spec_bundles_static_and_example_config`; `tests/test_real_roll.py::test_real_roll_detects` — skipped unless `FSD_REAL_ROLL` is set; opens it, waits for analysis, asserts every frame `ready` and ≥ 80% not flagged, and exports one frame to `tmp_path` copy of the roll.
- [ ] **Step 2:** Run → `test_packaging` FAILS, real-roll SKIPPED.
- [ ] **Step 3:** Implement packaging, first-launch, docs.
- [ ] **Step 4:** `pytest -q` → all pass. Then `FSD_REAL_ROLL=~/Projects/Canon-EDSDK-Film-Scanner/captures/Roll001 pytest tests/test_real_roll.py -v` → PASS (it works on a temp copy; the original roll is untouched). Report the per-frame confidences.
- [ ] **Step 5:** `python packaging/build_app.py` → `dist/Film Scan Darkroom.app` launches, opens the browser, shows Example Roll.
- [ ] **Step 6: Commit** `git commit -m "Add packaging, docs and real-roll check"`
