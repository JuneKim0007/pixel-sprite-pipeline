import { el } from '../../core/dom.js';
import { Range } from '../../ui/index.js';
import { Button } from '../../ui/index.js';

export const EDGE = 128;
export const NEUTRAL = 0.8;

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

/* A round brush that eases to nothing at its rim, so overlapping strokes build
 * up instead of leaving a disc edge. `amount` is signed: painting and erasing
 * are the same stroke with the sign flipped. */
export function paint(values, { x, y, radius, amount, edge = EDGE }) {
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
      const falloff = 1 - d * d;
      const i = py * edge + px;
      values[i] = Math.min(1, Math.max(0, values[i] + amount * falloff));
    }
  }
  return values;
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

/* Blue where the model is told to attend least, warm where most. Drawn under
 * the reference at low opacity, so what is painted is read against the picture
 * rather than beside it. */
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
  let radius = 0.12;
  let amount = 0.06;
  let painting = false;

  const canvas = el('canvas', { width: 512, height: 512, className: 'weightcanvas' });
  const readout = el('span', { className: 'mini' });
  const scratch = el('canvas', { width: EDGE, height: EDGE });
  const photo = new Image();
  photo.onload = draw;
  if (imagePath) photo.src = `/api/file?path=${encodeURIComponent(imagePath)}`;

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

    report();
  }

  function report() {
    const s = stats(values);
    readout.textContent =
      `${s.min.toFixed(2)} to ${s.max.toFixed(2)}, average ${s.mean.toFixed(2)}`;
  }

  function commit() {
    draw();
    onChange?.(values);
  }

  function at(e) {
    const r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  }

  canvas.onpointerdown = (e) => {
    painting = true;
    canvas.setPointerCapture?.(e.pointerId);
    const p = at(e);
    // Right button and shift both erase, so leaving a stroke does not mean
    // reaching for a different tool.
    const sign = e.button === 2 || e.shiftKey ? -1 : 1;
    paint(values, { ...p, radius, amount: amount * sign });
    commit();
  };
  canvas.onpointermove = (e) => {
    if (!painting) return;
    const sign = e.buttons === 2 || e.shiftKey ? -1 : 1;
    paint(values, { ...at(e), radius, amount: amount * sign });
    commit();
  };
  const stop = (e) => {
    if (!painting) return;
    painting = false;
    try { canvas.releasePointerCapture?.(e.pointerId); } catch { /* gone */ }
  };
  canvas.onpointerup = stop;
  canvas.onpointercancel = stop;
  canvas.oncontextmenu = (e) => e.preventDefault();

  const button = (label, title, run) => {
    const b = Button(label, { variant: 'ghost', title });
    b.onclick = () => { run(); commit(); };
    return b;
  };

  // A brush size is judged against the stroke, so this reacts while dragging
  // rather than on release.
  const slider = (label, min, max, step, value, set) =>
    el('label', { className: 'chk' }, `${label} `,
      Range(value, { min, max, step, onInput: set, format: (v) => String(v) }));

  const bar = el('div', { className: 'weightbar' },
    button('Centre', 'Strong in the middle, easing outward',
           () => { values = radial(0.9, NEUTRAL); }),
    button('Flat', 'One weight everywhere', () => { values = flat(); }),
    button('Clear', 'Remove the map; the run uses one strength again',
           () => { values = flat(); onChange?.(null); }),
    el('span', { className: 'sep' }),
    slider('Brush', 0.04, 0.4, 0.02, radius, (v) => { radius = v; }),
    slider('Strength', 0.02, 0.3, 0.02, amount, (v) => { amount = v; }),
    readout);

  draw();
  return {
    node: el('div', { className: 'weightpanel' }, bar, canvas,
      el('p', { className: 'help', textContent:
        'Paint where the model should attend. Warm is more, blue is less. '
        + 'Shift or right-drag lowers. The map is stored beside the image and '
        + 'resized to the sampler’s grid.' })),
    values: () => values,
    set(next) { values = next ? Float32Array.from(next) : flat(); draw(); },
  };
}
