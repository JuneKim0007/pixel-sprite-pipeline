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

/* Heading levels are semantic, not visual. `level` picks the tag so the
 * document outline is real; the class carries the look. A page has one h1,
 * a section an h2, a subsection an h3 — and CSS keys off .ui-h{n}, never off
 * the tag, so restyling one level cannot silently restyle another. */
export function Heading(text, { level = 2, sub = null, actions = null } = {}) {
  const tag = `h${Math.min(Math.max(level, 1), 6)}`;
  const head = el('div', { className: `ui-heading ui-h${level}` },
    el('div', { className: 'ui-heading-text' },
      el(tag, { className: 'ui-title', textContent: text }),
      sub ? el('p', { className: 'ui-sub', textContent: sub }) : null),
    actions ? el('div', { className: 'ui-heading-actions' }, actions) : null);
  return head;
}

/* A titled block. `Section` is the top level of a tab; `Subsection` nests
 * inside one. They differ only in heading level and class, which is exactly
 * the kind of variance that should not be two components. */
function block(kind, level, title, opts, children) {
  const { sub = null, actions = null, id = null } = opts;
  const node = el('section', { className: `ui-${kind}` });
  if (id) node.id = id;
  if (title) node.append(Heading(title, { level, sub, actions }));
  node.append(el('div', { className: `ui-${kind}-body` }, ...children));
  return node;
}

export const Section = (title, opts = {}, ...children) =>
  block('section', 2, title, opts, children);

export const Subsection = (title, opts = {}, ...children) =>
  block('subsection', 3, title, opts, children);

/* The (?) next to a label.
 *
 * Replaces a paragraph of help under every control. The reasoning in this
 * project is worth keeping — it is measured, and DECISIONS.md exists because
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
