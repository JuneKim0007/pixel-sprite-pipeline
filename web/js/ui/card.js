/* BaseCard — one level of inheritance, on purpose.
 *
 * Every card in this UI is the same shape: a media area, a title, some rows of
 * metadata, and a footer of controls. The parts that differ between a
 * reference card, a style card and a queue card are WHICH of those are filled
 * in, not how a card is assembled.
 *
 * So the base owns assembly and lifecycle, and a subclass overrides the few
 * hooks that vary. Deliberately ONE level deep: the moment a subclass needs
 * half of another subclass, that is composition asking to be used instead, and
 * a second level of inheritance would answer it with a flag.
 *
 *   class RefCard extends BaseCard {
 *     media() { return el('img', { src: this.data.url }); }
 *     footer() { return [this.roleSelect(), this.removeButton()]; }
 *   }
 *   grid.append(new RefCard({ data, onChange }).render());
 */

import { el } from '../store.js';
import { HelpTip } from './primitives.js';

export class BaseCard {
  /* `data` is the thing being shown; `on` is a bag of callbacks. Subclasses
   * read both and should not reach for module state — a card that can only be
   * built from globals cannot be tested or reused. */
  constructor({ data = {}, on = {}, className = '' } = {}) {
    this.data = data;
    this.on = on;
    this.extraClass = className;
    this.node = null;
  }

  /* ---- hooks a subclass overrides. All optional; a card with none of them
   * still renders, which keeps a new subclass to the parts it cares about. */
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

  /* Re-render in place. Cards live in grids that are rebuilt wholesale today;
   * this is here so a subclass CAN update without its parent knowing, which is
   * what a card with its own controls needs.
   *
   * Uses replaceWith rather than indexing the parent: `children` is an
   * HTMLCollection in a browser and has no indexOf, so splicing it is a
   * TypeError at runtime and a silent pass under any test double backed by an
   * array. Stick to APIs the real DOM actually has. */
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
