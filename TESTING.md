# Testing on real rolls

A manual walkthrough of the whole UI. Roughly 20–30 minutes.

The point is not to confirm it works. It is to find where it doesn't — the
synthetic rolls prove the code agrees with itself, and nothing more.

---

## Read this first

**Restart the server and hard-refresh the browser.** Python does not reload
modules and browsers cache `app.js` hard. If you skip either, you will be
testing old code and blaming new code.

```bash
# stop any running server first (Ctrl+C in its window)
film-scan-darkroom
```

Then **Cmd+Shift+R** (**Ctrl+Shift+R** on Windows) at <http://127.0.0.1:8001/>.

Use the `Example Roll` from a fresh packaged launch, or one of your own rolls,
for sections 1–13. Sections 14–15 need real scans from both scanners.

---

## 1. Library

- On launch, **Library** lists the rolls found under the default roots. An
  `Example Roll` should be present after a first packaged launch.
- The count under "Your scanned rolls" matches the list; newest roll first.
- Add a root to `config.yaml` under `library.roots` (a relative `rolls` resolves
  against the user folder) and restart. That roll appears without being opened
  by hand.

❌ **Report if:** a roll in a listed root is missing, or a relative root is looked
for in the wrong place.

## 2. Open a roll

- Paste a roll folder into the path box and press **Open folder…** — or click a
  roll in the library.
- The header shows the roll name, stock and developer from `roll.json`. A roll
  with no `roll.json` opens too, with empty metadata.
- **Library** again, then **Back to roll**: the same roll is still open.

❌ **Report if:** opening changes anything in the roll folder other than
`darkroom.json`. There should be no new or rewritten `roll.json`.

## 3. Analysis progress

- **Analysing…** appears with a meter; it advances once per frame and then
  disappears.
- Switch rolls mid-analysis: the old job stops updating the new roll's strip.

✅ **Good:** the meter completes and every strip tile settles into a state.

❌ **Report if:** the meter sticks at a number, or results from a previous roll
appear in the new one.

## 4. Strip badges

- Each tile shows its frame number and a status. Look for the three badges:
  **⚠ low confidence**, **✕ rejected**, **✓ export up to date**.
- Hover a flagged tile: its confidence is below the configured
  `detect.min_confidence`.
- Click a tile to select it; ←/→ move between frames.

❌ **Report if:** a badge stays after the condition is cleared, or a flagged
frame is exported without warning.

## 5. Negative view and the crop overlay

- Tick **Negative** to see the raw scan instead of the positive.
- The crop rectangle and its four corner handles sit on the frame; the rotate
  handle sits above it.
- Drag a corner: the crop follows, the aspect lock holds the ratio when ticked,
  and the new crop survives a reload (`darkroom.json`).
- Drag the rotate handle: the angle readout changes in degrees, counter-clockwise
  positive, and straightens the picture.
- **Reset crop & angle** restores the detected values; unticking **Auto crop &
  angle** leaves your manual values alone.

❌ **Report if:** the overlay is offset from the picture, a drag snaps back, or
a manual crop is lost on reopen.

## 6. Pick the film base

- On the negative, press **Pick base** and drag a rectangle over clear film base
  (the rebate along the long edge is best).
- The measured RGB appears and the positive visibly neutralises. On colour
  negative, R > G > B for the orange mask.

❌ **Report if:** the sample lands somewhere else, or a colour negative stays
cyan after sampling a real rebate.

## 7. Film sliders

- Switch **B&W** / **Colour**: B&W forces a neutral result; colour keeps the
  neutralised dye image.
- Drag **Exposure**, **Contrast**, **Black point** and **White point**. The
  preview follows smoothly and the saved positive uses the same values.

❌ **Report if:** raising contrast flattens the image, or a slider changes the
preview but not the export.

## 8. Sharpen sliders

- Raise **Amount** from 0: grain and edges gain definition. **Radius** controls
  the neighbourhood; **Threshold** keeps flat areas untouched.
- Back off to 0 and confirm the image returns to the unsharpened render.

❌ **Report if:** flat areas get noisy at a high threshold, or amount 0 still
sharpens.

## 9. Apply to all / selected / auto

- Edit frame 1, choose **All** under **Apply** and press **Apply**: every frame
  takes the same setting.
- Select a few tiles, choose **Selected**, apply: only those change.
- Choose **Auto**: only frames you have not touched change.

❌ **Report if:** "selected" includes unselected tiles, or "auto" overwrites a
manual edit.

## 10. Reject

- Press **Reject** on one frame: the ✕ badge appears and it drops out of a
  roll-wide export. Press again to restore it.

❌ **Report if:** a rejected frame is still exported with the whole roll.

## 11. Presets

- Type a stock name and press **Save as preset…**; it appears in the preset
  list and is matched automatically for a roll whose `roll.json` names that
  stock.
- Reopen that roll and confirm the preset is still selected.

❌ **Report if:** a preset saves but does not match, or the name is lost.

## 12. Export roll / selected / frame / contact sheet

- **Export ▸ whole roll**, **selected**, and **this frame** (the frame toolbar's
  export button): each writes into `positives/` next to the originals.
- With `format: both`, expect a `.tif` and a `.jpg`; contact sheet writes one
  page for the roll.
- Re-export the same frame with unchanged settings: the ✓ badge is already set
  and no `-1` duplicate appears.

❌ **Report if:** a file lands outside `positives/`, an original is touched, or
a repeat export writes a numbered duplicate.

## 13. Cancel

- Start a whole-roll export and press **Cancel** while the meter is moving.
  The job reports cancelled; frames already written stay, no half-written file
  is left.

❌ **Report if:** Cancel does nothing, or a cancelled export keeps writing.

## 14. Open in Photos (macOS)

- On macOS the **Open in Photos** button appears for an exported frame. Press
  it: Apple Photos opens with that positive ready for blemish cleanup.
- Edit in Photos, then check Darkroom's file and the original scan: neither
  changed. Photos kept its own copy.

❌ **Report if:** the button appears off macOS, the wrong image opens, or
Photos edits reach back into the roll.

## 15. Warnings banner

- Open a roll with an unreadable `roll.json` or a corrupt `darkroom.json`: a
  banner lists the warning, and **×** dismisses it for this view.

❌ **Report if:** a bad sidecar is silently overwritten, or a warning never
clears.

---

## 16. Open a real roll from each scanner

The important one. Point a library root at each scanner's capture folder and
open one roll from each:

- A **Canon EDSDK Film Scanner** roll (USB), with RAW+JPEG pairs sharing frame
  numbers and a `roll.json`.
- A **Canon Smart Film Scanner** roll (wireless, CCAPI), with its own
  `roll.json`.

For each roll:

- Every frame analyses; check the per-frame confidences reported by the real-roll
  test and the ⚠ badges in the strip. Trimmed/thin frames may be flagged.
- The crop follows the holder window or the clear gaps on a bare strip, at the
  right angle.
- A colour frame neutralises after sampling the rebate; a B&W frame is neutral.
- Export one frame from each and check the positive beside the originals.

❌ **Report if:** one scanner's naming groups two files as one frame, or a roll
from one rig needs settings that break the other.

---

## What to send back

Most useful, in order:

1. Anything in the ❌ lists above.
2. The per-frame confidences from a real roll, and which frames were flagged.
3. A screenshot of the negative with its crop overlay next to the positive, on
   colour film.
4. Any `WARNING` or `ERROR` lines from the server window.

Don't send actual scans — they can be large and are git-ignored for a reason.
