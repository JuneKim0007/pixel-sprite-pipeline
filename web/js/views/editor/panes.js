/* Splitters, and the ratios they remember.
 *
 * The editor was four bordered cards with four different gaps between them.
 * Every border is two lines where one would do, every gap is space the picture
 * does not get, and the split between the work area and the controls was
 * `grid-template-columns: 1fr 340px` - a decision taken once, in a stylesheet,
 * for every screen and every image.
 *
 * So the cards become one shell whose dividers ARE the separators, and the
 * dividers are draggable. A grid track is driven by a custom property; the
 * splitter writes that property and nothing else, which is why this file knows
 * about ratios and not about what is on either side of one.
 */

import { el } from '../../core/dom.js';

const STORE_KEY = 'pixel.editor.panes';

/* Where a split may sit, as a fraction of the container.
 *
 * A splitter that can reach 0 hides a pane with no way back, because the
 * handle goes with it. Both ends therefore stop short of the edge. */
export const MIN_FRACTION = 0.15;
export const MAX_FRACTION = 0.85;

export function clampFraction(value, min = MIN_FRACTION, max = MAX_FRACTION) {
  if (!Number.isFinite(value)) return (min + max) / 2;
  return Math.min(max, Math.max(min, value));
}

/** Where a pointer at `position` sits within `size`, clamped to the usable band. */
export function fractionAt(position, size, min = MIN_FRACTION, max = MAX_FRACTION) {
  if (!Number.isFinite(size) || size <= 0) return (min + max) / 2;
  return clampFraction(position / size, min, max);
}

/* Per-viewer convenience, so it is allowed to be missing.
 *
 * Every read and write is guarded: a private window, cleared site data, or a
 * browser set to block storage all throw on access rather than returning
 * nothing, and a layout that cannot render without its saved ratios is worse
 * than one that opens at its defaults. */
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
  } catch {
    /* not worth telling anyone about; the layout still works */
  }
}

/* A divider that drags.
 *
 * Pointer capture rather than a document-level listener: the pointer leaves
 * the 6px handle on the first frame of any real drag, and without capture the
 * move events go to whatever is underneath instead.
 */
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

  // A pane you can only size with a mouse is one a keyboard cannot recover
  // from once a drag has left it too narrow to see.
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
