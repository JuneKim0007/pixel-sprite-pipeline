/* The widgets every view builds, built once.
 *
 * Counted before writing this: `btn` appears in 12 files, `mini` in 11,
 * `empty` in 10, `warnline` in 7, `select` in 8. Each of those is one widget
 * assembled by hand in every view that wants it, which is why the same control
 * looks slightly different in three tabs and why changing one means finding
 * all of them.
 *
 * Grouping is the point. A UI is consistent when there is one place a variant
 * can be added, so these take a variant name rather than a class string: a
 * caller says what a button IS, not what it looks like. `Button.primary` and
 * `Button.ghost` exist; `className: 'btn primary'` does not need to be spelt
 * anywhere, and a new variant is one entry here rather than a search.
 *
 * Everything returns a DOM node and nothing knows what a rig or a palette is.
 * That is the rule for this folder - a primitive that understands the domain
 * has stopped being one.
 */

import { el } from '../core/dom.js';

/* ------------------------------------------------------------------ text */

export const Mini = (text, extra = '') =>
  el('span', { className: `mini ${extra}`.trim(), textContent: text });

export const Mono = (text) => el('span', { className: 'mono', textContent: text });

export const Empty = (text) => el('p', { className: 'empty', textContent: text });

export const Warn = (text) => el('p', { className: 'warnline', textContent: `! ${text}` });

export const Ok = (text) => el('p', { className: 'ok', textContent: `✓ ${text}` });

/* Prose that belongs beside a control rather than behind a (?). Rare on
 * purpose: a paragraph under every field is what the (?) exists to replace. */
export const Note = (text) => el('p', { className: 'headnote', textContent: text });

/* --------------------------------------------------------------- buttons */

const BUTTON_VARIANTS = ['primary', 'ghost', 'danger', 'pill'];

/** A button. `variant` names what it is; the class string is this file's business. */
export function Button(label, { variant = '', onClick, title = '', disabled = false } = {}) {
  if (variant && !BUTTON_VARIANTS.includes(variant)) {
    // A typo in a variant name silently produces an unstyled button, which is
    // the kind of thing nobody notices until a screenshot.
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

/* A slider with its value beside it. Bounded numbers are judged against
 * something on screen, not typed. */
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

/** A view's title bar: heading, optional subtitle, optional actions. */
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

/* One choice from a few, which is a radio group that reads as a control.
 * Written once here because five views build it by hand. */
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

/* A key and its value, for the small measurement grids. `tone` marks a number
 * the machine measured rather than one someone typed. */
export const Fact = (label, value, tone = '') =>
  el('div', { className: `fact ${tone}`.trim() }, Mini(label), el('b', { textContent: value }));

export const FactGrid = (...facts) =>
  el('div', { className: 'factsgrid' }, ...facts.filter(Boolean));
