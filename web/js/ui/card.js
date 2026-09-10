// BaseCard — one level of inheritance, on purpose.

import { el } from '../core/dom.js';
import { HelpTip } from './primitives.js';

export class BaseCard {
  // `data` is the thing being shown; `on` is a bag of callbacks.
  constructor({ data = {}, on = {}, className = '' } = {}) {
    this.data = data;
    this.on = on;
    this.extraClass = className;
    this.node = null;
  }

  // Hooks a subclass overrides. All optional.
  media() { return null; }
  title() { return this.data.title ?? null; }
  subtitle() { return this.data.subtitle ?? null; }
  help() { return this.data.help ?? null; }
  rows() { return []; }
  footer() { return []; }

  /* ---- assembly, owned by the base so every card lands the same way. */
  render() {
    const tip = HelpTip(this.help());
    const heading = this.title() == null ? null : el('div', { className: 'ui-card-head' },
      el('span', { className: 'ui-card-title', textContent: String(this.title()) }),
      tip ? tip.btn : null);

    const sub = this.subtitle();
    const rows = this.rows().filter(Boolean);
    const foot = this.footer().filter(Boolean);

    this.node = el('div', { className: `ui-card ${this.extraClass}`.trim() },
      this.media(),
      heading,
      tip ? tip.body : null,
      sub ? el('p', { className: 'ui-card-sub', textContent: String(sub) }) : null,
      rows.length ? el('div', { className: 'ui-card-rows' }, ...rows) : null,
      foot.length ? el('div', { className: 'ui-card-foot' }, ...foot) : null);
    return this.node;
  }

  // Re-render in place.
  update(data) {
    this.data = { ...this.data, ...data };
    const old = this.node;
    const next = this.render();
    if (old && old.parentNode) old.replaceWith(next);
    this.node = next;
    return next;
  }

  /* A labelled metadata line, since every subclass wants them. */
  row(label, value) {
    return el('div', { className: 'ui-card-row' },
      el('span', { className: 'ui-card-key mini', textContent: label }),
      el('span', { className: 'ui-card-val mono', textContent: String(value ?? '') }));
  }
}
