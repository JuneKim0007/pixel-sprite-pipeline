// BaseField — a labelled control that CANNOT be built without its (?).

import { el } from '../core/dom.js';
import { HelpTip } from './primitives.js';

let seq = 0;

export class BaseField {
  /* `field` is the schema entry: { path, label, help, type, min, max, step }. */
  constructor({ field = {}, value = null, on = {} } = {}) {
    this.field = field;
    this.value = value;
    this.on = on;
    this.id = `f${++seq}`;
    this.node = null;
  }

  /* The only hook most subclasses need: return the input element. */
  control() {
    return el('input', { type: 'text', id: this.id, className: 'input',
                         value: this.value ?? '' });
  }

  /* Subclasses that show a live value (a range's number) override this. */
  readout() { return null; }

  /** Whether this field is holding something other than its declared default. */
  changed() {
    const fallback = this.field.default;
    if (fallback === undefined || fallback === null) return false;
    return String(this.value) !== String(fallback);
  }

  commit(value) {
    this.value = value;
    if (this.on.change) this.on.change(this.field.path, value);
  }

  labelRow() {
    const help = this.field.help;
    const tip = HelpTip(help);
    const label = el('label', { className: 'ui-label', textContent: this.field.label || this.field.path });
    label.setAttribute('for', this.id);

    // No help is a gap in the schema, not a field that needs none. Show it.
    const marker = tip ? tip.btn : el('button', {
      className: 'ui-tip ui-tip-missing', textContent: '?', type: 'button',
      title: 'No explanation recorded for this setting', disabled: true,
    });

    const row = el('div', { className: 'ui-label-row' }, label, marker);
    if (this.on.reset && this.changed()) {
      const back = el('button', {
        className: 'linkbtn', type: 'button', textContent: 'reset',
        title: 'Back to this layer\u2019s default',
      });
      back.onclick = () => this.on.reset(this.field.path);
      row.append(back);
    }
    return { row, body: tip ? tip.body : null };
  }

  render() {
    const { row, body } = this.labelRow();
    this.node = el('div', { className: `ui-field ui-field-${this.field.type || 'text'}` },
      row,
      body,
      el('div', { className: 'ui-field-control' }, this.control(), this.readout()));
    return this.node;
  }
}
