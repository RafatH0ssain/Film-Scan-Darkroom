/* Film Scan Darkroom browser UI. No framework and no build step: the server
 * does the image work, this only holds state, draws the overlay and calls the
 * routes in Task 10.
 *
 * Everything it knows about a roll comes from GET /api/roll and the single
 * frame dicts returned by the edit endpoints. Image URLs carry a cache buster
 * so a fresh render is never served from the browser's stale cache.
 */

const $ = (id) => document.getElementById(id);

const els = {
  rollMeta: $("roll-meta"),
  metaRoll: $("meta-roll"),
  metaStock: $("meta-stock"),
  metaDeveloper: $("meta-developer"),
  chip: $("status-chip"),
  libraryBtn: $("library-btn"),
  backBtn: $("back-btn"),
  exportBtn: $("export-btn"),
  exportMenu: $("export-menu"),
  warnings: $("warnings"),
  warningsList: $("warnings-list"),
  warningsDismiss: $("warnings-dismiss"),
  libraryView: $("library-view"),
  libraryCount: $("library-count"),
  openPath: $("open-path"),
  openBtn: $("open-btn"),
  rollGrid: $("roll-grid"),
  libraryEmpty: $("library-empty"),
  workspace: $("workspace"),
  strip: $("strip"),
  analyseBar: $("analyse-bar"),
  analyseLabel: $("analyse-label"),
  analyseFill: $("analyse-fill"),
  viewer: $("viewer"),
  stage: $("preview-stage"),
  preview: $("preview"),
  overlay: $("crop-overlay"),
  cropShade: $("crop-shade"),
  cropRect: $("crop-rect"),
  handles: { nw: $("h-nw"), ne: $("h-ne"), sw: $("h-sw"), se: $("h-se") },
  rotateLine: $("rotate-line"),
  rotateHandle: $("rotate-handle"),
  baseRect: $("base-rect"),
  placeholder: $("placeholder"),
  toolbar: $("toolbar"),
  prev: $("prev-frame"),
  next: $("next-frame"),
  frameCount: $("frame-count"),
  negativeToggle: $("negative-toggle"),
  reject: $("reject-btn"),
  exportFrame: $("export-frame"),
  exportHud: $("export-hud"),
  exportStatus: $("export-status"),
  exportFill: $("export-fill"),
  exportCancel: $("export-cancel"),
  exportResults: $("export-results"),
  cropAuto: $("crop-auto"),
  angle: $("angle"),
  angleValue: $("angle-value"),
  rotCcw: $("rot-ccw"),
  rotCw: $("rot-cw"),
  mirror: $("mirror"),
  aspectLock: $("aspect-lock"),
  cropReset: $("crop-reset"),
  filmMode: $("film-mode"),
  exposure: $("exposure"),
  exposureValue: $("exposure-value"),
  contrast: $("contrast"),
  contrastValue: $("contrast-value"),
  blackPoint: $("black-point"),
  blackValue: $("black-value"),
  whitePoint: $("white-point"),
  whiteValue: $("white-value"),
  pickBase: $("pick-base"),
  baseHint: $("base-hint"),
  baseNote: $("base-note"),
  sharpenAmount: $("sharpen-amount"),
  sharpenAmountValue: $("sharpen-amount-value"),
  sharpenRadius: $("sharpen-radius"),
  sharpenRadiusValue: $("sharpen-radius-value"),
  sharpenThreshold: $("sharpen-threshold"),
  sharpenThresholdValue: $("sharpen-threshold-value"),
  outFormat: $("out-format"),
  outTiff: $("out-tiff"),
  outJpeg: $("out-jpeg"),
  outFolder: $("out-folder"),
  outConfidence: $("out-confidence"),
  outAspect: $("out-aspect"),
  presetName: $("preset-name"),
  rollPath: $("roll-path"),
  presetInput: $("preset-input"),
  savePreset: $("save-preset"),
  rollMirror: $("roll-mirror"),
  rollRotate: $("roll-rotate"),
  applyTarget: $("apply-target"),
  applyBtn: $("apply-btn"),
  photosBtn: $("photos-btn"),
  photosNote: $("photos-note"),
  toast: $("toast"),
};

let appStatus = {};
let state = null;
let current = null;
let selected = new Set();
let versions = new Map();
let autoMap = new Map();
let negative = false;
let pickBase = false;
let viewCrop = null;
let viewAngle = null;
let gesture = null;
let aspectRatio = null;
let pollTimer = null;
let jobTimer = null;
let activeJob = null;
let toastTimer = null;
let warningsDismissed = false;
let applyTo = "all";
let rollMirror = false;
let rollRotate = 0;
const editTimers = new Map();

// --- small helpers -----------------------------------------------------------

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const round4 = (v) => Math.round(v * 10000) / 10000;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));
const idle = (el) => document.activeElement !== el;

function toast(message, bad = false) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  els.toast.classList.toggle("bad", bad);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* a non-JSON error body: the status line will do */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function setSegmented(container, value) {
  container.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === String(value));
  });
}

const ranges = [...document.querySelectorAll('input[type="range"]')];
function paintRange(el) {
  const min = Number(el.min || 0);
  const max = Number(el.max || 100);
  const span = max - min;
  const pct = span > 0 ? ((Number(el.value) - min) / span) * 100 : 0;
  el.style.setProperty("--range-fill", `${clamp(pct, 0, 100)}%`);
}
function paintRanges() { ranges.forEach(paintRange); }
ranges.forEach((el) => el.addEventListener("input", () => paintRange(el)));

const frame = () => (state?.frames || []).find((f) => f.n === current) || null;
const version = (n) => versions.get(n) || 0;

function invalidate(n) {
  versions.set(n, Date.now());
}

// --- library ----------------------------------------------------------------

async function loadLibrary() {
  try {
    const rolls = await api("/api/library");
    els.libraryCount.textContent = `${rolls.length} roll${rolls.length === 1 ? "" : "s"}`;
    els.libraryEmpty.hidden = rolls.length > 0;
    els.rollGrid.innerHTML = rolls.map((roll) => {
      const tags = [];
      if (roll.flagged) tags.push(`<span class="tag flag">${roll.flagged} to review</span>`);
      if (roll.exported) tags.push(`<span class="tag done">${roll.exported} exported</span>`);
      return `<button class="roll-card" data-path="${esc(roll.path)}">
        <h3>${esc(roll.name)}</h3>
        <div class="meta">${roll.frames} frame${roll.frames === 1 ? "" : "s"}${roll.stock ? " &middot; " + esc(roll.stock) : ""}</div>
        <span class="path">${esc(roll.path)}</span>
        ${tags.length ? `<span class="tags">${tags.join("")}</span>` : ""}
      </button>`;
    }).join("");
  } catch (err) {
    toast(err.message, true);
  }
}

async function openRoll(path) {
  if (!path) return;
  try {
    state = await api("/api/roll/open", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  } catch (err) {
    toast(err.message, true);
    return;
  }
  current = state.frames[0]?.n ?? null;
  selected.clear();
  versions.clear();
  autoMap.clear();
  viewCrop = viewAngle = null;
  warningsDismissed = false;
  const url = new URL(location.href);
  url.searchParams.set("roll", path);
  history.replaceState(null, "", url);
  render();
  watchAnalysis();
}

function showLibrary() {
  els.libraryView.hidden = false;
  els.workspace.hidden = true;
  els.exportMenu.hidden = true;
  loadLibrary();
}

// --- rendering --------------------------------------------------------------

function renderHeader() {
  const open = !!(state && state.path);
  const meta = open ? state.metadata || {} : {};
  els.metaRoll.textContent = (state?.name) || "—";
  els.metaStock.textContent = meta.stock || meta.film_stock || "—";
  els.metaDeveloper.textContent = meta.developer || "—";
  els.rollMeta.dataset.idle = open ? "no" : "yes";
  els.exportBtn.disabled = !open;
  els.backBtn.hidden = !open;

  const analysing = open && state.analysing && state.analysing.done < state.analysing.total;
  if (!open) {
    els.chip.textContent = "Library";
    els.chip.dataset.state = "off";
  } else if (analysing) {
    els.chip.textContent = `Analysing ${state.analysing.done}/${state.analysing.total}`;
    els.chip.dataset.state = "off";
  } else {
    els.chip.textContent = "Ready";
    els.chip.dataset.state = "on";
  }
}

function renderWarnings() {
  const warnings = state?.warnings || [];
  els.warnings.hidden = !warnings.length || warningsDismissed;
  if (!els.warnings.hidden) {
    els.warningsList.innerHTML = warnings.map((w) => `<span>${esc(w)}</span>`).join("");
  }
}

function renderStrip() {
  const scroll = els.strip.scrollLeft;
  const thumbs = (state?.frames || []).map((f) => {
    const badges = [];
    if (f.flagged) badges.push('<span class="badge flag" title="Low confidence">&#9888;</span>');
    if (f.rejected) badges.push('<span class="badge reject" title="Rejected">&#10006;</span>');
    if (f.exported_current) badges.push('<span class="badge done" title="Export up to date">&#10003;</span>');
    const classes = [
      "frame-thumb",
      f.n === current ? "active" : "",
      selected.has(f.n) ? "selected" : "",
    ].filter(Boolean).join(" ");
    return `<button class="${classes}" data-n="${f.n}" data-status="${esc(f.status)}" title="Frame ${f.n}">
      <img src="/api/frames/${f.n}/thumb?v=${version(f.n)}" alt="Frame ${f.n}">
      <span class="frame-n">${f.n}</span>
      ${badges.length ? `<span class="badges">${badges.join("")}</span>` : ""}
    </button>`;
  });
  els.strip.replaceChildren(els.analyseBar, ...thumbs.map((html) => {
    const holder = document.createElement("div");
    holder.innerHTML = html;
    return holder.firstElementChild;
  }));
  els.strip.scrollLeft = scroll;

  const analysing = state.analysing && state.analysing.done < state.analysing.total;
  els.analyseBar.hidden = !analysing;
  if (analysing) {
    els.analyseLabel.textContent = `Analysing ${state.analysing.done} / ${state.analysing.total}`;
    els.analyseFill.style.width = `${state.analysing.total ? state.analysing.done / state.analysing.total * 100 : 0}%`;
  }
}

function renderEditor() {
  const f = frame();
  els.stage.hidden = !f;
  els.placeholder.hidden = !!f;
  els.toolbar.hidden = !f;
  if (!f) {
    els.preview.removeAttribute("src");
    return;
  }

  const view = negative || pickBase ? "negative" : "positive";
  const url = `/api/frames/${f.n}/preview?view=${view}&v=${version(f.n)}`;
  if (els.preview.getAttribute("src") !== url) els.preview.src = url;

  const index = state.frames.indexOf(f);
  els.frameCount.textContent = `${index + 1} / ${state.frames.length}  ·  ${f.n}`;
  els.prev.disabled = index <= 0;
  els.next.disabled = index >= state.frames.length - 1;
  els.negativeToggle.checked = negative;
  els.reject.dataset.on = f.rejected ? "yes" : "no";
  els.exportFrame.disabled = f.status !== "ready";

  fitStage();
  els.overlay.toggleAttribute("hidden", !(negative || pickBase));
  els.overlay.classList.toggle("base-mode", pickBase);
  drawOverlay();
}

function renderInspector() {
  const f = frame();
  if (!f) return;
  const s = f.settings || {};
  const film = s.film || {};
  const sharpen = s.sharpen || {};

  els.cropAuto.checked = autoMap.get(f.n) !== false;
  if (idle(els.angle)) els.angle.value = s.angle ?? 0;
  els.angleValue.textContent = `${Number(s.angle ?? 0).toFixed(2)}°`;
  if (idle(els.mirror)) els.mirror.checked = !!s.mirror;

  setSegmented(els.filmMode, film.mode);
  const sliders = [
    [els.exposure, els.exposureValue, film.exposure, (v) => `${v.toFixed(2)}×`],
    [els.contrast, els.contrastValue, film.contrast, (v) => v.toFixed(2)],
    [els.blackPoint, els.blackValue, film.black_point, (v) => v.toFixed(2)],
    [els.whitePoint, els.whiteValue, film.white_point, (v) => v.toFixed(2)],
    [els.sharpenAmount, els.sharpenAmountValue, sharpen.amount, (v) => v.toFixed(2)],
    [els.sharpenRadius, els.sharpenRadiusValue, sharpen.radius, (v) => `${v.toFixed(1)} px`],
    [els.sharpenThreshold, els.sharpenThresholdValue, sharpen.threshold, (v) => v.toFixed(2)],
  ];
  sliders.forEach(([el, out, value, format]) => {
    if (value !== undefined && value !== null && idle(el)) el.value = value;
    out.textContent = format(Number(el.value));
  });

  els.baseNote.dataset.measured = s.base_region ? "yes" : "no";
  els.pickBase.classList.toggle("primary", pickBase);
  els.baseHint.hidden = !pickBase;

  els.presetName.textContent = state.preset || "—";
  els.rollPath.textContent = state.path || "—";
  if (idle(els.rollMirror)) els.rollMirror.checked = rollMirror;
  els.photosBtn.hidden = !appStatus.photos_available;
  els.photosNote.hidden = !appStatus.photos_available;
  setSegmented(els.applyTarget, applyTo);
  setSegmented(els.rollRotate, rollRotate);
  paintRanges();
}

function render() {
  const open = !!(state && state.path);
  renderHeader();
  renderWarnings();
  els.libraryView.hidden = open;
  els.workspace.hidden = !open;
  if (open) {
    renderStrip();
    renderEditor();
    renderInspector();
  } else {
    loadLibrary();
  }
}

// --- overlay ----------------------------------------------------------------

function fitStage() {
  if (!els.preview.naturalWidth || !els.preview.naturalHeight) return;
  const width = Math.max(1, els.viewer.clientWidth - 24);
  const height = Math.max(1, els.viewer.clientHeight - 76);
  const scale = Math.min(width / els.preview.naturalWidth, height / els.preview.naturalHeight);
  els.stage.style.width = `${Math.max(1, Math.floor(els.preview.naturalWidth * scale))}px`;
  els.stage.style.height = `${Math.max(1, Math.floor(els.preview.naturalHeight * scale))}px`;
  const crop = frame()?.settings?.crop;
  if (crop) {
    const aspect = (crop[2] - crop[0]) * els.preview.naturalWidth /
      ((crop[3] - crop[1]) * els.preview.naturalHeight);
    if (Number.isFinite(aspect) && aspect > 0) els.outAspect.textContent = aspect.toFixed(2);
  }
  drawOverlay();
}

function currentCrop() {
  const s = frame()?.settings || {};
  const crop = viewCrop || s.crop || [0, 0, 1, 1];
  return crop.map(Number);
}

function currentAngle() {
  const s = frame()?.settings || {};
  return viewAngle === null || viewAngle === undefined ? Number(s.angle ?? 0) : viewAngle;
}

function setRect(el, x, y, w, h) {
  el.setAttribute("x", x);
  el.setAttribute("y", y);
  el.setAttribute("width", Math.max(0, w));
  el.setAttribute("height", Math.max(0, h));
}

function drawOverlay() {
  if (els.overlay.hasAttribute("hidden")) return;
  const [x0, y0, x1, y1] = currentCrop();
  const angle = currentAngle();
  const w = Math.max(1, els.stage.clientWidth);
  const h = Math.max(1, els.stage.clientHeight);
  const hw = 5 / w;
  const hh = 5 / h;

  els.cropShade.setAttribute(
    "d",
    `M0 0H1V1H0Z M${x0} ${y0}H${x1}V${y1}H${x0}Z`
  );
  setRect(els.cropRect, x0, y0, x1 - x0, y1 - y0);

  const corners = { nw: [x0, y0], ne: [x1, y0], sw: [x0, y1], se: [x1, y1] };
  for (const [name, [cx, cy]] of Object.entries(corners)) {
    setRect(els.handles[name], cx - hw, cy - hh, hw * 2, hh * 2);
  }

  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  let rx = cx;
  let ry = y0 > 0.09 ? y0 - 0.07 : y1 + 0.07;
  const rad = -angle * Math.PI / 180;
  const dx = (rx - cx) * w;
  const dy = (ry - cy) * h;
  rx = cx + (dx * Math.cos(rad) - dy * Math.sin(rad)) / w;
  ry = cy + (dx * Math.sin(rad) + dy * Math.cos(rad)) / h;
  els.rotateLine.setAttribute("x1", cx);
  els.rotateLine.setAttribute("y1", y0 > 0.09 ? y0 : y1);
  els.rotateLine.setAttribute("x2", rx);
  els.rotateLine.setAttribute("y2", ry);
  setRect(els.rotateHandle, rx - hw * 1.3, ry - hh * 1.3, hw * 2.6, hh * 2.6);

  const region = gesture?.mode === "base"
    ? gesture.region
    : (frame()?.settings?.base_region || null);
  if (region) {
    const [bx, by, bw, bh] = region.map(Number);
    els.baseRect.toggleAttribute("hidden", false);
    setRect(els.baseRect, bx, by, bw, bh);
  } else {
    els.baseRect.toggleAttribute("hidden", true);
  }
}

function point(e, bounded = true) {
  const rect = els.stage.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  return bounded ? { x: clamp(x, 0, 1), y: clamp(y, 0, 1) } : { x, y };
}

function constrain(crop, corner, pt) {
  const MIN = 0.015;
  const natural = els.preview;
  const nw = natural.naturalWidth || 1;
  const nh = natural.naturalHeight || 1;
  let [x0, y0, x1, y1] = crop;
  const fixedX = corner.includes("w") ? x1 : x0;
  const fixedY = corner.includes("n") ? y1 : y0;
  const signX = corner.includes("w") ? -1 : 1;
  const signY = corner.includes("n") ? -1 : 1;

  if (!aspectRatio) {
    if (corner.includes("w")) x0 = clamp(pt.x, 0, x1 - MIN);
    else x1 = clamp(pt.x, x0 + MIN, 1);
    if (corner.includes("n")) y0 = clamp(pt.y, 0, y1 - MIN);
    else y1 = clamp(pt.y, y0 + MIN, 1);
    return [x0, y0, x1, y1];
  }

  let dx = Math.abs((pt.x - fixedX) * nw);
  let dy = Math.abs((pt.y - fixedY) * nh);
  if (dx / aspectRatio > dy) dy = dx / aspectRatio;
  else dx = dy * aspectRatio;
  let width = clamp(dx, MIN * nw, Math.max(MIN * nw, Math.abs((signX > 0 ? 1 : 0) - fixedX) * nw));
  let height = width / aspectRatio;
  if (height > Math.abs((signY > 0 ? 1 : 0) - fixedY) * nh) {
    height = Math.abs((signY > 0 ? 1 : 0) - fixedY) * nh;
    width = height * aspectRatio;
  }
  x0 = clamp(signX > 0 ? fixedX : fixedX - width / nw, 0, 1);
  x1 = clamp(signX > 0 ? fixedX + width / nw : fixedX, 0, 1);
  y0 = clamp(signY > 0 ? fixedY : fixedY - height / nh, 0, 1);
  y1 = clamp(signY > 0 ? fixedY + height / nh : fixedY, 0, 1);
  let cw = Math.abs(x1 - x0) * nw;
  let ch = Math.abs(y1 - y0) * nh;
  if (cw / aspectRatio > ch) {
    cw = ch * aspectRatio;
    if (signX > 0) x1 = x0 + cw / nw;
    else x0 = x1 - cw / nw;
  } else {
    ch = cw / aspectRatio;
    if (signY > 0) y1 = y0 + ch / nh;
    else y0 = y1 - ch / nh;
  }
  return [x0, y0, x1, y1];
}

function handleFrom(target) {
  const map = { "h-nw": "nw", "h-ne": "ne", "h-sw": "sw", "h-se": "se" };
  return map[target.id] || null;
}

els.overlay.addEventListener("pointerdown", (event) => {
  if (!frame()) return;
  let mode;
  let corner = null;
  if (pickBase) {
    mode = "base";
  } else if (event.target.id === "rotate-handle" || event.target.id === "rotate-line") {
    mode = "rotate";
  } else if (handleFrom(event.target)) {
    mode = "corner";
    corner = handleFrom(event.target);
  } else if (event.target.id === "crop-rect") {
    mode = "move";
  } else {
    return;
  }
  const start = point(event, mode !== "rotate");
  gesture = {
    mode,
    corner,
    start,
    crop: [...currentCrop()],
    angle: currentAngle(),
    region: mode === "base" ? [start.x, start.y, 0, 0] : null,
  };
  try { els.overlay.setPointerCapture(event.pointerId); } catch { /* capture unavailable */ }
  event.preventDefault();
});

els.overlay.addEventListener("pointermove", (event) => {
  if (!gesture) return;
  const pt = point(event, gesture.mode !== "rotate");
  const { mode } = gesture;

  if (mode === "corner") {
    viewCrop = constrain(gesture.crop, gesture.corner, pt);
  } else if (mode === "move") {
    const [x0, y0, x1, y1] = gesture.crop;
    const dx = clamp(pt.x - gesture.start.x, -x0, 1 - x1);
    const dy = clamp(pt.y - gesture.start.y, -y0, 1 - y1);
    viewCrop = [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  } else if (mode === "rotate") {
    const w = Math.max(1, els.stage.clientWidth);
    const h = Math.max(1, els.stage.clientHeight);
    const [x0, , x1, y1] = gesture.crop;
    const cx = (x0 + x1) / 2 * w;
    const cy = (gesture.crop[1] + y1) / 2 * h;
    const a0 = Math.atan2(gesture.start.y * h - cy, gesture.start.x * w - cx);
    const a1 = Math.atan2(pt.y * h - cy, pt.x * w - cx);
    let delta = (a1 - a0) * 180 / Math.PI;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    viewAngle = clamp(gesture.angle - delta, -45, 45);
    els.angle.value = viewAngle;
    els.angleValue.textContent = `${viewAngle.toFixed(2)}°`;
  } else if (mode === "base") {
    const x = Math.min(gesture.start.x, pt.x);
    const y = Math.min(gesture.start.y, pt.y);
    gesture.region = [x, y, Math.abs(pt.x - gesture.start.x), Math.abs(pt.y - gesture.start.y)];
  }
  drawOverlay();
});

els.overlay.addEventListener("pointerup", async (event) => {
  if (!gesture) return;
  const done = gesture;
  gesture = null;
  try { els.overlay.releasePointerCapture(event.pointerId); } catch { /* already released */ }

  if (done.mode === "base") {
    const [x, y, w, h] = done.region.map(round4);
    if (w > 0.005 && h > 0.005) {
      await sendEdit({ base_region: [x, y, w, h] });
      toast("Film base measured");
    }
    pickBase = false;
    els.overlay.classList.remove("base-mode");
  } else if (done.mode === "rotate") {
    autoMap.set(current, false);
    await sendEdit({ angle: round4(viewAngle ?? done.angle) });
  } else {
    autoMap.set(current, false);
    await sendEdit({ crop: (viewCrop || done.crop).map(round4) });
  }
  viewCrop = null;
  viewAngle = null;
  drawOverlay();
  renderInspector();
});

els.overlay.addEventListener("pointercancel", () => {
  gesture = null;
  viewCrop = viewAngle = null;
  drawOverlay();
});

// --- edits ------------------------------------------------------------------

async function sendEdit(changes) {
  if (!current) return;
  try {
    const result = await api(`/api/frames/${current}/edit`, {
      method: "POST",
      body: JSON.stringify(changes),
    });
    applyFrame(result);
  } catch (err) {
    toast(err.message, true);
    await reloadState();
  }
}

function applyFrame(result) {
  if (!state || !result) return;
  const index = state.frames.findIndex((f) => f.n === result.n);
  if (index >= 0) state.frames[index] = result;
  invalidate(result.n);
  render();
}

function queueEdit(key, changes) {
  clearTimeout(editTimers.get(key));
  editTimers.set(key, setTimeout(() => {
    editTimers.delete(key);
    sendEdit(changes);
  }, 200));
}

function flushEdit(key, changes) {
  clearTimeout(editTimers.get(key));
  editTimers.delete(key);
  sendEdit(changes);
}

function bindSlider(el, output, format, changes) {
  el.addEventListener("input", () => {
    output.textContent = format(Number(el.value));
    queueEdit(el.id, changes());
  });
  el.addEventListener("change", () => flushEdit(el.id, changes()));
}

bindSlider(els.angle, els.angleValue, (v) => `${v.toFixed(2)}°`, () => {
  autoMap.set(current, false);
  return { angle: Number(els.angle.value) };
});
bindSlider(els.exposure, els.exposureValue, (v) => `${v.toFixed(2)}×`,
  () => ({ film: { exposure: Number(els.exposure.value) } }));
bindSlider(els.contrast, els.contrastValue, (v) => v.toFixed(2),
  () => ({ film: { contrast: Number(els.contrast.value) } }));
bindSlider(els.blackPoint, els.blackValue, (v) => v.toFixed(2), () => {
  let v = Number(els.blackPoint.value);
  if (v >= Number(els.whitePoint.value)) {
    v = Math.max(0, Number(els.whitePoint.value) - 0.01);
    els.blackPoint.value = v;
    els.blackValue.textContent = v.toFixed(2);
  }
  return { film: { black_point: v } };
});
bindSlider(els.whitePoint, els.whiteValue, (v) => v.toFixed(2), () => {
  let v = Number(els.whitePoint.value);
  if (v <= Number(els.blackPoint.value)) {
    v = Math.min(1, Number(els.blackPoint.value) + 0.01);
    els.whitePoint.value = v;
    els.whiteValue.textContent = v.toFixed(2);
  }
  return { film: { white_point: v } };
});
bindSlider(els.sharpenAmount, els.sharpenAmountValue, (v) => v.toFixed(2),
  () => ({ sharpen: { amount: Number(els.sharpenAmount.value) } }));
bindSlider(els.sharpenRadius, els.sharpenRadiusValue, (v) => `${v.toFixed(1)} px`,
  () => ({ sharpen: { radius: Number(els.sharpenRadius.value) } }));
bindSlider(els.sharpenThreshold, els.sharpenThresholdValue, (v) => v.toFixed(2),
  () => ({ sharpen: { threshold: Number(els.sharpenThreshold.value) } }));

els.filmMode.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => {
    setSegmented(els.filmMode, button.dataset.value);
    sendEdit({ film: { mode: button.dataset.value } });
  });
});

els.cropAuto.addEventListener("change", () => {
  if (!current) return;
  autoMap.set(current, els.cropAuto.checked);
  if (els.cropAuto.checked) {
    viewCrop = viewAngle = null;
    sendEdit({ crop: null, angle: null });
  }
});

async function quarterTurn(delta) {
  const s = frame()?.settings || {};
  await sendEdit({ rotate90: ((Number(s.rotate90 ?? 0) + delta) % 4 + 4) % 4 });
}
els.rotCcw.addEventListener("click", () => quarterTurn(3));
els.rotCw.addEventListener("click", () => quarterTurn(1));
els.mirror.addEventListener("change", () => sendEdit({ mirror: els.mirror.checked }));
els.cropReset.addEventListener("click", () => {
  autoMap.set(current, true);
  viewCrop = viewAngle = null;
  sendEdit({ crop: null, angle: null, rotate90: null, mirror: null });
  toast("Crop and angle reset");
});

els.aspectLock.addEventListener("change", () => {
  if (!els.aspectLock.checked) { aspectRatio = null; return; }
  const crop = currentCrop();
  const nw = els.preview.naturalWidth || 1;
  const nh = els.preview.naturalHeight || 1;
  const width = Math.max(1e-6, (crop[2] - crop[0]) * nw);
  const height = Math.max(1e-6, (crop[3] - crop[1]) * nh);
  aspectRatio = width / height;
  toast(`Aspect locked at ${aspectRatio.toFixed(2)}`);
});

els.pickBase.addEventListener("click", () => {
  if (!current) return;
  pickBase = !pickBase;
  if (pickBase) {
    negative = true;
    els.negativeToggle.checked = true;
  }
  renderEditor();
  renderInspector();
});

// --- apply, presets, defaults, reject ---------------------------------------

els.applyTarget.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => {
    applyTo = button.dataset.value;
    setSegmented(els.applyTarget, applyTo);
  });
});

els.applyBtn.addEventListener("click", async () => {
  if (!current) return;
  const body = { from: current, to: applyTo };
  if (applyTo === "selected") body.frames = [...selected];
  if (applyTo === "selected" && !body.frames.length) {
    toast("Select at least one thumbnail first", true);
    return;
  }
  try {
    state = await api("/api/apply", { method: "POST", body: JSON.stringify(body) });
    state.frames.forEach((f) => invalidate(f.n));
    render();
    toast(`Applied to ${applyTo} frames`);
  } catch (err) {
    toast(err.message, true);
  }
});

els.savePreset.addEventListener("click", async () => {
  const name = els.presetInput.value.trim();
  if (!name || !current) {
    toast("Type a preset name first", true);
    return;
  }
  try {
    state = await api("/api/presets", {
      method: "POST",
      body: JSON.stringify({ name, from: current }),
    });
    render();
    toast(`Saved preset “${name}”`);
  } catch (err) {
    toast(err.message, true);
  }
});

els.rollMirror.addEventListener("change", async () => {
  rollMirror = els.rollMirror.checked;
  try {
    state = await api("/api/roll/defaults", {
      method: "POST",
      body: JSON.stringify({ mirror: rollMirror }),
    });
    render();
  } catch (err) {
    toast(err.message, true);
  }
});

els.rollRotate.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", async () => {
    rollRotate = Number(button.dataset.value);
    setSegmented(els.rollRotate, rollRotate);
    try {
      state = await api("/api/roll/defaults", {
        method: "POST",
        body: JSON.stringify({ rotate90: rollRotate }),
      });
      render();
    } catch (err) {
      toast(err.message, true);
    }
  });
});

async function toggleReject() {
  const f = frame();
  if (!f) return;
  try {
    applyFrame(await api(`/api/frames/${f.n}/reject`, {
      method: "POST",
      body: JSON.stringify({ rejected: !f.rejected }),
    }));
  } catch (err) {
    toast(err.message, true);
  }
}
els.reject.addEventListener("click", toggleReject);

// --- export -----------------------------------------------------------------

function renderResults(results) {
  els.exportResults.hidden = !results.length;
  els.exportResults.innerHTML = results.map((r) => {
    const detail = r.error
      ? esc(r.error)
      : (r.files || []).join(", ");
    return `<li class="${r.error ? "bad" : ""}">Frame ${r.frame}: ${detail}</li>`;
  }).join("");
}

function showExportHud() {
  els.exportHud.hidden = false;
  els.exportStatus.textContent = "Starting…";
  els.exportFill.style.width = "0%";
  els.exportResults.hidden = true;
  els.exportCancel.textContent = "Cancel";
  els.exportCancel.dataset.done = "no";
}

async function pollJob(jobId) {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      els.exportStatus.textContent = `${job.done} / ${job.total} · ${job.status.replace(/_/g, " ")}`;
      els.exportFill.style.width = `${job.total ? job.done / job.total * 100 : 100}%`;
      if (job.status !== "running") {
        clearInterval(jobTimer);
        renderResults(job.results || []);
        els.exportCancel.textContent = "Close";
        els.exportCancel.dataset.done = "yes";
        activeJob = null;
        await reloadState();
      }
    } catch (err) {
      clearInterval(jobTimer);
      els.exportStatus.textContent = err.message;
    }
  }, 350);
}

async function beginExport(kind) {
  if (!state?.path) return;
  els.exportMenu.hidden = true;
  if (kind === "sheet") {
    try {
      const body = await api("/api/contact-sheet", { method: "POST" });
      toast(`Contact sheet: ${body.path}`);
    } catch (err) {
      toast(err.message, true);
    }
    return;
  }
  let frames;
  if (kind === "all") frames = "all";
  else if (kind === "frame") frames = current ? [current] : [];
  else frames = [...selected];
  if (kind !== "all" && !frames.length) {
    toast("No frames to export", true);
    return;
  }
  try {
    const body = await api("/api/export", {
      method: "POST",
      body: JSON.stringify({ frames }),
    });
    activeJob = body.job;
    showExportHud();
    pollJob(activeJob);
  } catch (err) {
    toast(err.message, true);
  }
}

els.exportBtn.addEventListener("click", () => {
  els.exportMenu.hidden = !els.exportMenu.hidden;
});
els.exportMenu.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => beginExport(button.dataset.export));
});
els.exportFrame.addEventListener("click", () => beginExport("frame"));
els.exportCancel.addEventListener("click", async () => {
  if (els.exportCancel.dataset.done === "yes") {
    els.exportHud.hidden = true;
    return;
  }
  if (activeJob) {
    try {
      await api(`/api/jobs/${activeJob}/cancel`, { method: "POST" });
    } catch (err) {
      toast(err.message, true);
    }
  }
});

els.photosBtn.addEventListener("click", async () => {
  if (!current) return;
  try {
    const body = await api(`/api/frames/${current}/open-in-photos`, { method: "POST" });
    toast(`Opened ${body.path} in Photos`);
  } catch (err) {
    toast(err.message, true);
  }
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".menu-wrap")) els.exportMenu.hidden = true;
});

// --- navigation, polling, keyboard ------------------------------------------

function navigate(step) {
  if (!state || !current) return;
  const index = state.frames.findIndex((f) => f.n === current);
  const next = state.frames[index + step];
  if (!next) return;
  current = next.n;
  viewCrop = viewAngle = null;
  renderStrip();
  renderEditor();
  renderInspector();
}

function selectFrame(n, additive) {
  current = n;
  viewCrop = viewAngle = null;
  if (additive) {
    if (selected.has(n)) selected.delete(n);
    else selected.add(n);
  } else {
    selected.clear();
  }
  renderStrip();
  renderEditor();
  renderInspector();
}

els.strip.addEventListener("click", (event) => {
  const thumb = event.target.closest(".frame-thumb");
  if (thumb) selectFrame(Number(thumb.dataset.n), event.shiftKey || event.metaKey || event.ctrlKey);
});
els.prev.addEventListener("click", () => navigate(-1));
els.next.addEventListener("click", () => navigate(1));

els.negativeToggle.addEventListener("change", () => {
  negative = els.negativeToggle.checked;
  if (!negative) pickBase = false;
  renderEditor();
  renderInspector();
});

async function reloadState() {
  try {
    state = await api("/api/roll");
    render();
  } catch {
    showLibrary();
  }
}

function watchAnalysis() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!state?.path) return;
    try {
      const fresh = await api("/api/roll");
      const analysing = fresh.analysing && fresh.analysing.done < fresh.analysing.total;
      state = fresh;
      render();
      if (!analysing) clearInterval(pollTimer);
    } catch {
      clearInterval(pollTimer);
    }
  }, 500);
}

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea, select")) return;
  if (event.key === "ArrowLeft") { event.preventDefault(); navigate(-1); return; }
  if (event.key === "ArrowRight") { event.preventDefault(); navigate(1); return; }
  const key = event.key.toLowerCase();
  if (key === "r") { event.preventDefault(); toggleReject(); }
  if (key === "e") { event.preventDefault(); beginExport("frame"); }
  if (key === "n") {
    event.preventDefault();
    negative = !negative;
    if (!negative) pickBase = false;
    renderEditor();
    renderInspector();
  }
});

els.libraryBtn.addEventListener("click", showLibrary);
els.backBtn.addEventListener("click", () => {
  if (state?.path) {
    els.libraryView.hidden = true;
    els.workspace.hidden = false;
    render();
  }
});
els.openBtn.addEventListener("click", () => openRoll(els.openPath.value.trim()));
els.openPath.addEventListener("keydown", (event) => {
  if (event.key === "Enter") openRoll(els.openPath.value.trim());
});
els.rollGrid.addEventListener("click", (event) => {
  const card = event.target.closest(".roll-card");
  if (card) openRoll(card.dataset.path);
});
els.warningsDismiss.addEventListener("click", () => {
  warningsDismissed = true;
  els.warnings.hidden = true;
});
els.preview.addEventListener("load", fitStage);
new ResizeObserver(() => fitStage()).observe(els.viewer);

// --- boot -------------------------------------------------------------------

function renderOutput() {
  const out = appStatus.export || {};
  const formats = { tiff: "TIFF", jpeg: "JPEG", both: "TIFF + JPEG" };
  const compressions = { none: "None", deflate: "Deflate" };
  els.outFormat.textContent = formats[out.format] || out.format || "—";
  els.outTiff.textContent = compressions[out.tiff_compression] || out.tiff_compression || "—";
  els.outJpeg.textContent = out.jpeg_quality ?? "—";
  els.outFolder.textContent = out.folder || "—";
  const confidence = appStatus.detect?.min_confidence;
  els.outConfidence.textContent = confidence === undefined
    ? "—" : Number(confidence).toFixed(2);
}

async function boot() {
  try { appStatus = await api("/api/status"); } catch { appStatus = {}; }
  renderOutput();

  const roll = new URLSearchParams(location.search).get("roll");
  if (roll) {
    await openRoll(roll);
    return;
  }
  try {
    state = await api("/api/roll");
    current = state.frames[0]?.n ?? null;
    render();
    watchAnalysis();
  } catch {
    state = null;
    showLibrary();
  }
}

boot();
