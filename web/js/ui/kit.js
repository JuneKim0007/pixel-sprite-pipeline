/* Widgets the views used to rebuild by hand: btn in 12 files, mini in 11,
 * empty in 10. A caller names what a thing is, never a class string. */
import { el } from '../core/dom.js';

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

/** variant names what it is; the class string is this file's business. */
export function Button(label, { variant = '', onClick, title = '', disabled = false } = {}) {
  if (variant && !BUTTON_VARIANTS.includes(variant)) {
    // A typo would silently render unstyled, which nobody sees until a screenshot.
    throw new Error(`no button variant '${variant}'`);
  }
  const node = el('button', {
    className: `btn ${variant}`.trim(), textContent: label, type: 'button',
    title, disabled,
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
export function Range(value, { min, max, step = 0.05, onChange, format } = {}) {
  const show = format || ((v) => Number(v).toFixed(2));
  const out = el('span', { className: 'val', textContent: show(value) });
  const node = el('input', { type: 'range', min, max, step, value });
  node.oninput = () => { out.textContent = show(node.value); };
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
