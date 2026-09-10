/* Widgets the views used to rebuild by hand: btn in 12 files, mini in 11,
 * empty in 10. A caller names what a thing is, never a class string. */
import { el } from '../core/dom.js';
import { normaliseColour } from '../core/colour.js';

/* ------------------------------------------------------------------ text */

export const Mini = (text, extra = '') =>
  el('span', { className: `mini ${extra}`.trim(), textContent: text });

export const Mono = (text) => el('span', { className: 'mono', textContent: text });

export const Empty = (text) => el('p', { className: 'empty', textContent: text });

export const Warn = (text) => el('p', { className: 'warnline', textContent: `! ${text}` });

export const Ok = (text) => el('p', { className: 'ok', textContent: `✓ ${text}` });

/* Rare on purpose: a paragraph under every field is what the (?) replaces. */
export const Note = (text) => el('p', { className: 'headnote', textContent: text });

/* --------------------------------------------------------------- buttons */

const BUTTON_VARIANTS = ['primary', 'ghost', 'danger', 'pill'];
// Size is not a variant: a large ghost button is both.
const BUTTON_SIZES = ['lg'];

/** variant names what it is; the class string is this file's business. */
export function Button(label, { variant = '', size = '', onClick, title = '', disabled = false } = {}) {
  if (variant && !BUTTON_VARIANTS.includes(variant)) {
    // A typo would silently render unstyled, which nobody sees until a screenshot.
    throw new Error(`no button variant '${variant}'`);
  }
  if (size && !BUTTON_SIZES.includes(size)) {
    throw new Error(`no button size '${size}'`);
  }
  const node = el('button', {
    className: `btn ${variant} ${size}`.replace(/\s+/g, ' ').trim(),
    textContent: label, type: 'button', title, disabled,
  });
  if (onClick) node.onclick = onClick;
  return node;
}

Button.primary = (label, opts = {}) => Button(label, { ...opts, variant: 'primary' });
Button.ghost = (label, opts = {}) => Button(label, { ...opts, variant: 'ghost' });
Button.danger = (label, opts = {}) => Button(label, { ...opts, variant: 'danger' });
Button.pill = (label, opts = {}) => Button(label, { ...opts, variant: 'pill' });

/* ---------------------------------------------------------------- inputs */

export function Select(options, { value = '', onChange, className = '', disabled = false } = {}) {
  const node = el('select', { className: `select ${className}`.trim(), disabled });
  for (const opt of options) {
    const [v, label] = Array.isArray(opt) ? opt : [opt, opt];
    node.append(el('option', { value: v, textContent: label, selected: v === value }));
  }
  if (onChange) node.onchange = () => onChange(node.value);
  return node;
}

export function Num(value, { min, max, step = 1, onChange, className = '' } = {}) {
  const node = el('input', { type: 'number', className: `num ${className}`.trim(),
                             value, min, max, step });
  if (onChange) node.onchange = () => onChange(Number(node.value));
  return node;
}

export function Check(label, { checked = false, onChange } = {}) {
  const box = el('input', { type: 'checkbox', checked });
  if (onChange) box.onchange = () => onChange(box.checked);
  return el('label', { className: 'chk' }, box, el('span', { textContent: label }));
}

/* Bounded numbers are judged against something on screen, not typed. */
// A bounded number, as a slider with the value beside it.
export function Range(value, {
  min, max, step = 0.05, onChange, onInput, format,
  readout = 'text', placeholder = '',
} = {}) {
  const show = format || ((v) => Number(v).toFixed(2));
  const node = el('input', { type: 'range', min, max, step, value });

  if (readout === 'box') {
    const box = el('input', {
      type: 'number', className: 'num', step, value, placeholder,
    });
    if (min != null) box.min = min;
    if (max != null) box.max = max;

    const clamp = (raw) => {
      const n = parseFloat(raw);
      if (Number.isNaN(n)) return null;
      return Math.min(max ?? n, Math.max(min ?? n, n));
    };
    node.oninput = () => { box.value = node.value; onInput?.(Number(node.value)); };
    node.onchange = () => onChange?.(Number(node.value));
    box.oninput = () => { const n = clamp(box.value); if (n != null) node.value = n; };
    box.onchange = () => {
      const n = clamp(box.value);
      box.value = n ?? '';
      node.value = n ?? min ?? 0;
      onChange?.(n);
    };
    return el('div', { className: 'control' }, node, box);
  }

  const out = el('span', { className: 'val', textContent: show(value) });
  node.oninput = () => { out.textContent = show(node.value); onInput?.(Number(node.value)); };
  if (onChange) node.onchange = () => onChange(Number(node.value));
  return el('div', { className: 'control' }, node, out);
}

/* --------------------------------------------------------------- layout */

export const Row = (...children) => el('div', { className: 'row' }, ...children);

export const Fields = (...children) => el('div', { className: 'fields' }, ...children);

/** A view's title bar. */
export function Head(title, { sub = '', actions = [] } = {}) {
  return el('header', { className: 'head' },
    el('div', {},
      el('h1', { textContent: title }),
      sub ? el('p', { className: 'sub', textContent: sub }) : null),
    actions.length ? el('div', { className: 'head-actions' }, ...actions) : null);
}

/** A panel heading inside a view. */
export function PanelHead(title, { note = '', action = null } = {}) {
  return el('div', { className: 'ovhead' },
    el('h2', { textContent: title }),
    note ? Mini(note) : null,
    action);
}

/* Five views built their own, and .seg had two definitions in the CSS. */
export function Segmented(options, { value, onPick } = {}) {
  const host = el('div', { className: 'segmented' });
  for (const opt of options) {
    const [v, label, count] = Array.isArray(opt) ? opt : [opt, opt, null];
    const b = el('button', { type: 'button',
                             className: `seg ${v === value ? 'on' : ''}`.trim(),
                             textContent: count == null ? label : `${label} ${count}` });
    b.onclick = () => onPick(v);
    host.append(b);
  }
  return host;
}

/* tone marks a number the machine measured, not one someone typed. */
export const Fact = (label, value, tone = '') =>
  el('div', { className: `fact ${tone}`.trim() }, Mini(label), el('b', { textContent: value }));

export const FactGrid = (...facts) =>
  el('div', { className: 'factsgrid' }, ...facts.filter(Boolean));

export function ColourPicker(value, { presets = [], fallback = '#FF00FF', onChange } = {}) {
  const wrap = el('div', { className: 'colourctl' });
  let current = normaliseColour(value) || fallback;

  const swatches = el('div', { className: 'swatchrow' });
  const picker = el('input', { type: 'color', className: 'swatchpick', value: current });
  const text = el('input', { type: 'text', className: 'num mono', value: value ?? current });

  const set = (next, from) => {
    const hex = normaliseColour(next);
    if (!hex) return false;
    current = hex;
    picker.value = hex;
    if (from !== 'text') text.value = hex;
    for (const chip of swatches.children) {
      chip.classList.toggle('on', chip.dataset.hex.toLowerCase() === hex.toLowerCase());
    }
    if (onChange) onChange(hex);
    return true;
  };

  for (const opt of presets) {
    const [hex, label] = Array.isArray(opt) ? opt : [opt, opt];
    const chip = el('button', {
      type: 'button', className: 'swatch', title: label, style: `background:${hex}`,
    });
    chip.dataset.hex = hex;
    chip.onclick = () => set(hex);
    swatches.append(chip);
  }

  picker.oninput = () => set(picker.value);
  text.onchange = () => { if (!set(text.value, 'text')) text.value = current; };

  wrap.append(swatches, picker, text);
  set(current);
  return wrap;
}

export function Meter(shape) {
  const { label = '', total = 0, done = 0, detail = '' } = shape || {};
  const share = total ? Math.min(1, done / total) : 0;
  const fill = el('span', { className: 'meterfill' });
  fill.style.width = `${(share * 100).toFixed(1)}%`;
  return el('div', { className: 'meter' },
    el('div', { className: 'meterhead' },
      el('span', { textContent: label }),
      Mini(total ? `${done}/${total}` : '-')),
    el('div', { className: 'metertrack' }, fill),
    detail ? Mini(detail) : null);
}
