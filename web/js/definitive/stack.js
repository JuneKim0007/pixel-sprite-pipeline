/* The layer stack: reorderable, and the form under it is generated.
 *
 * Nothing here knows what a layer does. It knows there is a list, that the
 * list is the order, and that each entry declares its own fields. Adding a
 * layer to the editor is adding one to `pipeline/definitive/`; this file does
 * not change.
 *
 * That is also why every control has its (?) without anyone attaching one.
 * The form is built by `BaseField`, which cannot render a label row without a
 * tip - a field with no explanation shows a disabled marker, so the gap is
 * visible rather than looking like a control that needs none. Handing the
 * declaration to a builder is the only way "every setting is explained" stops
 * being a thing to remember.
 */

import { el } from '../core/dom.js';
import { BaseField } from '../ui/index.js';

/* A control built from a Field declaration.
 *
 * BaseField owns the label row and the tip; a subclass only says what the
 * input element is. Everything below is one `control()` each.
 */
class Control extends BaseField {
  constructor(spec, value, onChange) {
    super({ field: { path: spec.key, label: spec.label, help: spec.help, type: spec.kind },
            value, on: { change: (_p, v) => onChange(spec.key, v) } });
    this.spec = spec;
  }

  control() {
    const s = this.spec;
    if (s.kind === 'bool') {
      const box = el('input', { type: 'checkbox', id: this.id, checked: !!this.value });
      box.onchange = () => this.commit(box.checked);
      return box;
    }
    if (s.kind === 'select') {
      const sel = el('select', { className: 'select', id: this.id });
      for (const [value, label] of s.options || []) {
        sel.append(el('option', { value, textContent: label, selected: value === this.value }));
      }
      sel.onchange = () => this.commit(sel.value);
      return sel;
    }
    if (s.kind === 'text') {
      const input = el('input', { type: 'text', className: 'input', id: this.id,
                                  value: this.value ?? '' });
      input.onchange = () => this.commit(input.value);
      return input;
    }
    // A bounded number is a slider with its value beside it: these are judged
    // against the preview, not typed.
    if (s.min !== null && s.max !== null && s.kind === 'float') {
      const range = el('input', { type: 'range', id: this.id,
                                  min: s.min, max: s.max, step: s.step ?? 0.05,
                                  value: this.value ?? s.default ?? 0 });
      this.out = el('span', { className: 'val',
                              textContent: Number(this.value ?? 0).toFixed(2) });
      range.oninput = () => { this.out.textContent = Number(range.value).toFixed(2); };
      range.onchange = () => this.commit(Number(range.value));
      return range;
    }
    const num = el('input', { type: 'number', className: 'num', id: this.id,
                              min: s.min ?? undefined, max: s.max ?? undefined,
                              step: s.step ?? 1, value: this.value ?? s.default ?? 0 });
    num.onchange = () => this.commit(Number(num.value));
    return num;
  }

  readout() { return this.out || null; }
}

function visible(spec, config) {
  return Object.entries(spec.when || {}).every(([k, v]) => config[k] === v);
}

/** The form for one layer, from its declaration. */
export function layerForm(spec, config, onChange) {
  const host = el('div', { className: 'layerform' });
  for (const f of spec.fields) {
    if (!visible(f, config)) continue;
    const value = config[f.key] ?? f.default;
    host.append(new Control(f, value, onChange).render());
  }
  return host;
}

/* -------------------------------------------------------------- the list */

export function stackList(stack, catalogue, { selected, onSelect, onReorder,
                                              onToggle, onRemove, onAdd }) {
  const byKey = Object.fromEntries(catalogue.map((s) => [s.key, s]));
  const list = el('div', { className: 'stacklist' });

  stack.forEach((entry, i) => {
    const spec = byKey[entry.layer] || { label: entry.layer, summary: '' };
    const row = el('div', {
      className: `stackrow ${entry.id === selected ? 'on' : ''}`
        + `${entry.enabled === false ? ' off' : ''}`,
      draggable: true,
    });
    row.dataset.index = String(i);

    const power = el('button', {
      className: 'stackpower', type: 'button',
      textContent: entry.enabled === false ? '○' : '◉',
      title: entry.enabled === false ? 'Disabled' : 'Enabled',
    });
    power.onclick = (e) => { e.stopPropagation(); onToggle(i); };

    const drop = el('button', { className: 'stackx', type: 'button', textContent: '✕',
                                title: 'Remove this layer' });
    drop.onclick = (e) => { e.stopPropagation(); onRemove(i); };

    row.append(
      el('span', { className: 'stackgrip', textContent: '⠿', title: 'Drag to reorder' }),
      el('span', { className: 'stackname', textContent: spec.label }),
      power, drop);
    row.onclick = () => onSelect(entry.id);

    // Reordering by drag, with no library. The dataTransfer payload is the
    // index; the drop target computes the move.
    row.ondragstart = (e) => {
      e.dataTransfer.setData('text/plain', String(i));
      row.classList.add('dragging');
    };
    row.ondragend = () => row.classList.remove('dragging');
    row.ondragover = (e) => { e.preventDefault(); row.classList.add('over'); };
    row.ondragleave = () => row.classList.remove('over');
    row.ondrop = (e) => {
      e.preventDefault();
      row.classList.remove('over');
      const from = Number(e.dataTransfer.getData('text/plain'));
      if (!Number.isNaN(from) && from !== i) onReorder(from, i);
    };

    list.append(row);
  });

  const add = el('select', { className: 'select stackadd' });
  add.append(el('option', { value: '', textContent: '+ add a layer' }));
  for (const spec of catalogue) {
    const already = stack.some((s) => s.layer === spec.key);
    if (already && !spec.repeatable) continue;
    add.append(el('option', { value: spec.key, textContent: spec.label }));
  }
  add.onchange = () => { if (add.value) onAdd(add.value); };

  return el('div', {}, list, add);
}
