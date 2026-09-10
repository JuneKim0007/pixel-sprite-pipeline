import { el } from '../../core/dom.js';

const STORE_KEY = 'pixel.editor.panes';

export const MIN_FRACTION = 0.15;
export const MAX_FRACTION = 0.85;

export function clampFraction(value, min = MIN_FRACTION, max = MAX_FRACTION) {
  if (!Number.isFinite(value)) return (min + max) / 2;
  return Math.min(max, Math.max(min, value));
}

export function fractionAt(position, size, min = MIN_FRACTION, max = MAX_FRACTION) {
  if (!Number.isFinite(size) || size <= 0) return (min + max) / 2;
  return clampFraction(position / size, min, max);
}

export function loadRatios(fallback) {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    const out = { ...fallback };
    for (const key of Object.keys(fallback)) {
      if (Number.isFinite(saved[key])) out[key] = clampFraction(saved[key]);
    }
    return out;
  } catch {
    return { ...fallback };
  }
}

export function saveRatios(ratios) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(ratios));
  } catch { /* ignored */ }
}

export function splitter(axis, { onMove, onDrop, label }) {
  const bar = el('div', {
    className: `pane-split pane-split-${axis}`,
    role: 'separator',
    tabIndex: 0,
    title: label || 'Drag to resize',
  });
  bar.setAttribute('aria-orientation', axis === 'x' ? 'vertical' : 'horizontal');

  let dragging = false;

  bar.onpointerdown = (e) => {
    dragging = true;
    bar.setPointerCapture(e.pointerId);
    bar.classList.add('dragging');
    e.preventDefault();
  };

  bar.onpointermove = (e) => {
    if (!dragging) return;
    onMove(e);
  };

  const finish = (e) => {
    if (!dragging) return;
    dragging = false;
    try { bar.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
    bar.classList.remove('dragging');
    if (onDrop) onDrop();
  };
  bar.onpointerup = finish;
  bar.onpointercancel = finish;

  bar.onkeydown = (e) => {
    const step = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -0.02
      : e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 0.02 : 0;
    if (!step) return;
    e.preventDefault();
    onMove({ nudge: step });
    if (onDrop) onDrop();
  };

  return bar;
}
