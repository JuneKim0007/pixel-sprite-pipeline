/* Structural primitives: headings, sections, and the (?) tip.
 *
 * These exist so a screen is assembled from named parts rather than from a
 * pile of divs with hand-written class strings. Two things follow from that:
 * the markup is consistent without anyone remembering to make it consistent,
 * and a change to how every section looks is one edit here.
 *
 * No build step and no framework — this is the same plain `el()` the rest of
 * the UI uses, just wrapped in names.
 */

import { el } from '../core/dom.js';

/* The (?) next to a label.
 *
 * Replaces a paragraph of help under every control. The reasoning in this
 * project is worth keeping — it is measured, and docs/DECISIONS.md exists because
 * of it — but printed under all 131 settings it makes the form unreadable and
 * people stop reading any of it. So: one glyph, click to reveal, and the full
 * text is one interaction away instead of always-on noise.
 *
 * The lead sentence is treated as the summary and shown in the tooltip title,
 * so hovering answers the common case without a click.
 */
export function HelpTip(text) {
  if (!text) return null;
  const split = text.search(/\.\s/);
  const lead = split < 0 ? text : text.slice(0, split + 1);

  const btn = el('button', {
    className: 'ui-tip', textContent: '?', type: 'button',
    title: lead, 'aria-label': `Explain: ${lead}`,
  });
  const body = el('p', { className: 'ui-tip-body hidden', textContent: text });
  btn.setAttribute('aria-expanded', 'false');
  btn.onclick = () => {
    const open = body.classList.toggle('hidden') === false;
    btn.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
  };
  return { btn, body };
}

/* A label with its (?) attached. Kept here rather than in BaseField so that a
 * one-off control outside the schema form gets the same affordance. */
export function LabelWithTip(text, help, { htmlFor = null } = {}) {
  const tip = HelpTip(help);
  const label = el('label', { className: 'ui-label', textContent: text });
  if (htmlFor) label.setAttribute('for', htmlFor);
  const row = el('div', { className: 'ui-label-row' }, label, tip ? tip.btn : null);
  return { row, body: tip ? tip.body : null };
}

export function Disclosure(title, opts = {}, ...children) {
  const { open = true, note = '', actions = null, onToggle } = opts;
  const box = el('details', { className: 'disclosure' });
  box.open = open;

  const head = el('summary', { className: 'disclosure-head' },
    el('span', { className: 'disclosure-title', textContent: title }));
  if (note) head.append(el('span', { className: 'headnote', textContent: note }));
  if (actions) {
    const bar = el('span', { className: 'disclosure-actions' }, ...[].concat(actions));
    bar.onclick = (e) => e.preventDefault();
    head.append(bar);
  }

  box.append(head, el('div', { className: 'disclosure-body' }, ...children));
  if (onToggle) box.ontoggle = () => onToggle(box.open);
  return box;
}
