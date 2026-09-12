// BasePanel — a block a view either shows or does not, plus the set a view shows.

import { el } from '../core/dom.js';

export class BasePanel {
  constructor({ data = {}, on = {} } = {}) {
    this.data = data;
    this.on = on;
    this.node = null;
  }

  // Hooks a subclass overrides. All optional.
  boxClass() { return 'ui-panel'; }
  shows() { return true; }
  note() { return null; }
  body() { return []; }
  /* What stands in when there is nothing to show. Null drops the panel. */
  empty() { return null; }

  render() {
    if (!this.shows()) {
      this.node = this.empty();
      return this.node;
    }
    const note = this.note();
    this.node = el('div', { className: this.boxClass() },
      note ? el('p', { className: 'mini', textContent: note }) : null,
      ...this.body().filter(Boolean));
    return this.node;
  }
}

/* Which panels a view shows and in what order, named once instead of at the append site. */
export class PanelSet {
  constructor(...panels) { this.panels = panels; }

  add(panel) {
    this.panels.push(panel);
    return this;
  }

  build(data, on = {}) {
    return this.panels.map((Panel) => new Panel({ data, on }).render()).filter(Boolean);
  }
}
