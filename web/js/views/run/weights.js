import { el } from '../../core/dom.js';
import { undoController, undoKeys } from '../../core/undo.js';
import { Button, Range, Segmented } from '../../ui/index.js';

export const EDGE = 128;
export const NEUTRAL = 0.8;

export const BRUSH = { min: 0.015, max: 0.4, step: 0.005, value: 0.12 };
export const STRENGTH = { min: 0.02, max: 0.3, step: 0.02, value: 0.06 };
export const UNDO_DEPTH = 40;

export const TOOLS = [
  ['paint', 'Paint'],
  ['erase', 'Erase'],
  ['lasso', 'Lasso'],
];

export function flat(value = NEUTRAL, edge = EDGE) {
  return new Float32Array(edge * edge).fill(value);
}

export function radial(centre = 0.9, rim = NEUTRAL, falloff = 1, edge = EDGE) {
  const out = new Float32Array(edge * edge);
  for (let y = 0; y < edge; y++) {
    for (let x = 0; x < edge; x++) {
      const ny = (y / (edge - 1)) * 2 - 1;
      const nx = (x / (edge - 1)) * 2 - 1;
      const reach = Math.min(1, Math.hypot(nx, ny) / Math.max(falloff, 1e-6));
      out[y * edge + x] = centre + (rim - centre) * reach;
    }
  }
  return out;
}

// `amount` is signed, so erasing is the same stroke with the sign flipped.
export function paint(values, { x, y, radius, amount, edge = EDGE, mask = null }) {
  const cx = x * (edge - 1);
  const cy = y * (edge - 1);
  const r = Math.max(radius * edge, 0.5);
  const lo = Math.max(0, Math.floor(cy - r));
  const hi = Math.min(edge - 1, Math.ceil(cy + r));
  const left = Math.max(0, Math.floor(cx - r));
  const right = Math.min(edge - 1, Math.ceil(cx + r));

  for (let py = lo; py <= hi; py++) {
    for (let px = left; px <= right; px++) {
      const d = Math.hypot(px - cx, py - cy) / r;
      if (d >= 1) continue;
      const i = py * edge + px;
      if (mask && !mask[i]) continue;
      values[i] = Math.min(1, Math.max(0, values[i] + amount * (1 - d * d)));
    }
  }
  return values;
}

export function fill(values, value, mask = null) {
  for (let i = 0; i < values.length; i++) {
    if (!mask || mask[i]) values[i] = value;
  }
  return values;
}

// Scanline even-odd: crossings solved once per row, not once per cell.
export function lassoMask(path, edge = EDGE) {
  const mask = new Uint8Array(edge * edge);
  if (!path || path.length < 3) return mask;

  const crossings = [];
  for (let py = 0; py < edge; py++) {
    const y = (py + 0.5) / edge;
    crossings.length = 0;
    for (let i = 0, j = path.length - 1; i < path.length; j = i++) {
      const [x1, y1] = path[j];
      const [x2, y2] = path[i];
      if ((y1 > y) !== (y2 > y)) {
        crossings.push(x1 + ((y - y1) / (y2 - y1)) * (x2 - x1));
      }
    }
    crossings.sort((a, b) => a - b);
    for (let k = 0; k + 1 < crossings.length; k += 2) {
      const from = Math.max(0, Math.ceil(crossings[k] * edge - 0.5));
      const to = Math.min(edge - 1, Math.floor(crossings[k + 1] * edge - 0.5));
      for (let px = from; px <= to; px++) mask[py * edge + px] = 1;
    }
  }
  return mask;
}

export function invertMask(mask) {
  const out = new Uint8Array(mask.length);
  for (let i = 0; i < mask.length; i++) out[i] = mask[i] ? 0 : 1;
  return out;
}

export function maskCount(mask) {
  let n = 0;
  for (let i = 0; i < mask.length; i++) if (mask[i]) n++;
  return n;
}

export function stats(values) {
  let min = 1, max = 0, sum = 0;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
    sum += v;
  }
  return { min, max, mean: sum / values.length };
}

// Blue where the model is told to attend least, warm where most.
export function toPixels(values, edge = EDGE) {
  const data = new Uint8ClampedArray(edge * edge * 4);
  for (let i = 0; i < values.length; i++) {
    const t = Math.min(1, Math.max(0, (values[i] - 0.5) / 0.5));
    data[i * 4] = Math.round(40 + 215 * t);
    data[i * 4 + 1] = Math.round(70 + 80 * t);
    data[i * 4 + 2] = Math.round(200 - 140 * t);
    data[i * 4 + 3] = 255;
  }
  return data;
}

export function toImageData(values, edge = EDGE) {
  return new ImageData(toPixels(values, edge), edge, edge);
}

export function weightPainter({ imagePath, onChange, initial = null } = {}) {
  let values = initial ? Float32Array.from(initial) : flat();
  let selection = null;
  let lassoPath = null;
  let tool = 'paint';
  let radius = BRUSH.value;
  let amount = STRENGTH.value;
  let painting = false;

  const canvas = el('canvas', { width: 512, height: 512, className: 'weightcanvas',
                                tabIndex: 0 });
  const readout = el('span', { className: 'mini' });
  const scratch = el('canvas', { width: EDGE, height: EDGE });
  const photo = new Image();
  photo.onload = draw;
  if (imagePath) photo.src = `/api/file?path=${encodeURIComponent(imagePath)}`;

  const history = undoController({
    entries: UNDO_DEPTH,
    sizeOf: (v) => v.byteLength,
    read: () => Float32Array.from(values),
    write: (v) => { values = Float32Array.from(v); draw(); onChange?.(values); },
    onChange: () => refreshBar(),
  });

  function draw() {
    // The pure half runs headless; only the painting does not.
    const ctx = canvas.getContext?.('2d');
    if (!ctx || typeof ImageData === 'undefined') { report(); return; }
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    scratch.getContext('2d').putImageData(toImageData(values), 0, 0);
    ctx.drawImage(scratch, 0, 0, canvas.width, canvas.height);

    if (photo.naturalWidth) {
      const scale = Math.min(canvas.width / photo.naturalWidth,
                             canvas.height / photo.naturalHeight);
      const w = photo.naturalWidth * scale;
      const h = photo.naturalHeight * scale;
      ctx.globalAlpha = 0.55;
      ctx.drawImage(photo, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
      ctx.globalAlpha = 1;
    }

    if (lassoPath) outline(ctx, lassoPath);
    else if (selection) edges(ctx, selection);
    report();
  }

  function outline(ctx, path) {
    if (path.length < 2) return;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(path[0][0] * canvas.width, path[0][1] * canvas.height);
    for (const [x, y] of path.slice(1)) {
      ctx.lineTo(x * canvas.width, y * canvas.height);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  // A cell on the selection's boundary, drawn at canvas scale.
  function edges(ctx, mask) {
    const cell = canvas.width / EDGE;
    ctx.save();
    ctx.fillStyle = 'rgba(255,255,255,.85)';
    for (let y = 0; y < EDGE; y++) {
      for (let x = 0; x < EDGE; x++) {
        if (!mask[y * EDGE + x]) continue;
        const open = !x || !mask[y * EDGE + x - 1]
          || x === EDGE - 1 || !mask[y * EDGE + x + 1]
          || !y || !mask[(y - 1) * EDGE + x]
          || y === EDGE - 1 || !mask[(y + 1) * EDGE + x];
        if (open) ctx.fillRect(x * cell, y * cell, cell, cell);
      }
    }
    ctx.restore();
  }

  function report() {
    const s = stats(values);
    const picked = selection ? `, ${maskCount(selection)} cells selected` : '';
    readout.textContent =
      `${s.min.toFixed(2)} to ${s.max.toFixed(2)}, average ${s.mean.toFixed(2)}${picked}`;
  }

  function commit() {
    draw();
    onChange?.(values);
  }

  function at(e) {
    const r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  }

  const signFor = (e) =>
    (tool === 'erase' || e.button === 2 || e.buttons === 2 || e.shiftKey) ? -1 : 1;

  canvas.onpointerdown = (e) => {
    painting = true;
    canvas.focus?.();
    canvas.setPointerCapture?.(e.pointerId);
    const p = at(e);
    if (tool === 'lasso') { lassoPath = [[p.x, p.y]]; draw(); return; }
    history.begin();
    paint(values, { ...p, radius, amount: amount * signFor(e), mask: selection });
    commit();
  };

  canvas.onpointermove = (e) => {
    if (!painting) return;
    const p = at(e);
    if (tool === 'lasso') { lassoPath.push([p.x, p.y]); draw(); return; }
    paint(values, { ...p, radius, amount: amount * signFor(e), mask: selection });
    commit();
  };

  const stop = (e) => {
    if (!painting) return;
    painting = false;
    try { canvas.releasePointerCapture?.(e.pointerId); } catch { /* gone */ }
    if (tool === 'lasso') {
      selection = lassoPath && lassoPath.length > 2 ? lassoMask(lassoPath) : null;
      lassoPath = null;
      refreshBar();
      draw();
      return;
    }
    history.commit();
  };
  canvas.onpointerup = stop;
  canvas.onpointercancel = stop;
  canvas.oncontextmenu = (e) => e.preventDefault();

  const bar = el('div', { className: 'weightbar' });

  const act = (label, title, run) => {
    const b = Button(label, { variant: 'ghost', title });
    b.onclick = () => { run(); commit(); refreshBar(); };
    return b;
  };

  const brushRow = (label, spec, value, set) =>
    el('label', { className: 'chk' }, `${label} `,
      Range(value, {
        min: spec.min, max: spec.max, step: spec.step,
        onInput: set, format: (v) => String(v),
      }));

  function refreshBar() {
    const undo = Button('Undo', { variant: 'ghost', title: 'Ctrl+Z',
                                  disabled: !history.canUndo() });
    undo.onclick = () => history.undo();
    const redo = Button('Redo', { variant: 'ghost', title: 'Ctrl+Y',
                                  disabled: !history.canRedo() });
    redo.onclick = () => history.redo();

    const picked = selection ? [
      act('Fill', 'Set the selected cells to the current strength',
          () => history.record(() => fill(values, Math.min(1, 0.5 + amount * 5), selection))),
      act('Invert', 'Select everything outside instead',
          () => { selection = invertMask(selection); }),
      act('Deselect', 'Paint the whole map again', () => { selection = null; }),
    ] : [];

    bar.replaceChildren(
      Segmented(TOOLS, { value: tool, onPick: (v) => { tool = v; refreshBar(); } }),
      el('span', { className: 'sep' }),
      undo, redo,
      el('span', { className: 'sep' }),
      act('Centre', 'Strong in the middle, easing outward',
          () => history.record(() => { values = radial(0.9, NEUTRAL); })),
      act('Level', 'Every cell to one value; the map still applies',
          () => history.record(() => fill(values, NEUTRAL, selection))),
      act('Remove map', 'Drop the map; the run uses one strength again',
          () => { history.record(() => { values = flat(); }); onChange?.(null); }),
      el('span', { className: 'sep' }),
      brushRow('Brush', BRUSH, radius, (v) => { radius = v; }),
      brushRow('Strength', STRENGTH, amount, (v) => { amount = v; }),
      ...picked,
      readout);
  }

  refreshBar();
  draw();

  // Scoped to the panel, not the document: the map lives inside a disclosure on
  // a tab, and a global Ctrl+Z would rewind it from anywhere in the app.
  const node = el('div', { className: 'weightpanel', tabIndex: -1 }, bar, canvas);
  const releaseKeys = undoKeys(history, { target: node });

  return {
    node,
    values: () => values,
    selection: () => selection,
    history,
    destroy: releaseKeys,
    set(next) {
      values = next ? Float32Array.from(next) : flat();
      history.clear();
      draw();
    },
  };
}
