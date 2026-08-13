/* Form controls generated from the server's schema.
 *
 * Nothing here hardcodes a knob: add a field to pipeline/schema.py and it
 * appears with the right control, range and help text. The list editors are
 * the exception — reference images, pose sets and soft-body nodes are lists of
 * objects, which a flat field table cannot express, so they get bespoke
 * editors keyed by config path.
 */

import { getPath } from './api.js';
import { el } from './core/dom.js';
import { state } from './store.js';
import { autoOrder as orderOf, orderProblems as problemsOf } from './features/stages.js';
import { HelpTip } from './ui/index.js';
import { VIEW_OPTIONS } from './features/pose.js';

/* ------------------------------------------------------------- primitives */

function optionsFor(field) {
  if (field.options) return field.options;
  if (field.options_from) return state.schema.options[field.options_from] || [];
  return [];
}

/* A `when` condition compares against the CONTROLLING field's own default, not
 * a single hardcoded one. The literal 'library' here was pose.source's default,
 * copied from the pre-module version and then applied to every condition — so
 * `palette.file`, gated on `palette.source == 'file'` whose real default is
 * 'extract', was being judged against 'library'. It hid correctly by accident,
 * and would have shown the wrong control the moment a `when` awaited the value
 * that happened to be hardcoded. */
function whenDefault(path) {
  const field = (state.schema?.fields || []).find((f) => f.path === path);
  return field && 'default' in field ? field.default : undefined;
}

function visible(field, cfg) {
  if (!field.when) return true;
  return Object.entries(field.when).every(([path, want]) => {
    const actual = getPath(cfg, path) ?? whenDefault(path);
    return actual === want;
  });
}

/** Build one control. `onChange(value)` receives null when cleared. */
export function control(field, value, onChange) {
  const wrap = el('div', { className: 'control' });

  if (field.type === 'bool') {
    const box = el('input', { type: 'checkbox', checked: !!value });
    box.onchange = () => onChange(box.checked);
    wrap.append(box);
    return wrap;
  }

  if (field.type === 'select') {
    const opts = optionsFor(field);
    const sel = el('select', { className: 'select' });
    sel.append(el('option', { value: '', textContent: '(inherit)' }));
    for (const o of opts) sel.append(el('option', { value: o, textContent: o }));
    if (field.free_numeric && value != null && !opts.includes(value)) {
      sel.append(el('option', { value: String(value), textContent: String(value) }));
    }
    sel.value = value == null ? '' : String(value);
    sel.onchange = () => onChange(sel.value === '' ? null : sel.value);
    wrap.append(sel);

    if (field.free_numeric) {
      const num = el('input', { type: 'number', className: 'num', placeholder: '°', step: 5 });
      num.onchange = () => { if (num.value !== '') onChange(Number(num.value)); };
      wrap.append(num);
    }
    return wrap;
  }

  if (field.type === 'int' || field.type === 'float') {
    const isFloat = field.type === 'float';
    const step = field.step ?? (isFloat ? 0.05 : 1);
    const num = el('input', {
      type: 'number', className: 'num', step,
      value: value ?? '', placeholder: 'auto',
    });
    if (field.min != null) num.min = field.min;
    if (field.max != null) num.max = field.max;

    const spanOk = field.min != null && field.max != null && field.max - field.min <= 5000;
    if (spanOk) {
      const range = el('input', {
        type: 'range', min: field.min, max: field.max, step,
        value: value ?? field.min,
      });
      range.oninput = () => { num.value = range.value; };
      range.onchange = () => onChange(Number(range.value));
      num.oninput = () => { range.value = num.value; };
      wrap.append(range);
    }
    num.onchange = () =>
      onChange(num.value === '' ? null : (isFloat ? parseFloat(num.value) : parseInt(num.value, 10)));
    wrap.append(num);
    return wrap;
  }

  if (field.type === 'textarea') {
    const area = el('textarea', { rows: 2, value: value ?? '' });
    area.onchange = () => onChange(area.value);
    wrap.append(area);
    return wrap;
  }

  if (field.type === 'stages') {
    wrap.style.flex = '1';
    wrap.append(stagePicker(value || [], onChange));
    return wrap;
  }

  const input = el('input', { type: 'text', value: value ?? '' });
  input.onchange = () => onChange(input.value);
  wrap.append(input);
  return wrap;
}

/* ------------------------------------------------------------ stage picker */

/* Mirrors the server's dependency check so an unrunnable order shows up as you
 * build it, not when you press Save. */
export const orderProblems = (active) => problemsOf(active, state.schema.stages);
export const autoOrder = (active) => orderOf(active, state.schema.stages);


function stagePicker(active, onChange) {
  const box = el('div', { className: 'stagepicker' });
  const all = state.schema.stages.map((s) => s.name);

  for (const name of [...active, ...all.filter((s) => !active.includes(s))]) {
    const meta = state.schema.stages.find((s) => s.name === name);
    const on = active.includes(name);
    const chip = el('div', {
      className: `st ${on ? '' : 'off'} ${meta?.resource === 'gpu' ? 'gpu' : ''}`,
      draggable: on,
      title: meta ? `${meta.resource.toUpperCase()} · needs ${meta.requires.join(', ') || '—'} · gives ${meta.produces.join(', ') || '—'}` : '',
    },
      el('span', { className: 'num', textContent: on ? String(active.indexOf(name) + 1) : '–' }),
      name);

    chip.onclick = () =>
      onChange(on ? active.filter((s) => s !== name) : [...active, name]);
    chip.ondragstart = (e) => e.dataTransfer.setData('text/plain', name);
    chip.ondragover = (e) => e.preventDefault();
    chip.ondrop = (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData('text/plain');
      if (from === name) return;
      const next = active.filter((s) => s !== from);
      const at = next.indexOf(name);
      next.splice(at < 0 ? next.length : at, 0, from);
      onChange(next);
    };
    box.append(chip);
  }

  const wrap = el('div', { style: 'flex:1' }, box);
  const problems = orderProblems(active);
  if (problems.length) {
    const fix = el('button', { className: 'btn', textContent: 'Auto-order' });
    fix.onclick = () => onChange(autoOrder(active));
    wrap.append(el('div', { className: 'orderwarn' },
      el('b', { textContent: 'This order cannot run.' }),
      el('ul', {}, ...problems.map((p) => el('li', { textContent: p }))),
      fix));
  }
  return wrap;
}

/* ------------------------------------------------------------ list editors */

const LIST_EDITORS = {
  'references.identity': {
    label: 'Identity references',
    help: 'Who the character is. Illustrations, concept art or photos — these '
        + 'do NOT have to be pixel art; the sprite look comes from generation. '
        + 'Each is labelled with the view it shows, and frames are matched to '
        + 'the closest one.',
    blank: () => ({ path: '', view: 'front', weight: 1.0 }),
    fields: [
      { key: 'path', label: 'Image', type: 'image' },
      { key: 'view', label: 'Shows view', type: 'select', options: VIEW_OPTIONS },
      { key: 'weight', label: 'Weight ×', type: 'float', min: 0, max: 1.5, step: 0.05 },
    ],
  },
  'references.style': {
    label: 'Style references',
    help: 'What the art should look like — good sprites in the target idiom. '
        + 'Kept weak on purpose: at identity strength a style exemplar replaces '
        + 'your character with the exemplar.',
    blank: () => ({ path: '', weight: 1.0 }),
    fields: [
      { key: 'path', label: 'Image', type: 'image' },
      { key: 'weight', label: 'Weight ×', type: 'float', min: 0, max: 1.5, step: 0.05 },
    ],
  },
  'references.pose': {
    label: 'Pose references',
    help: 'A composition to reproduce. Annotate one in the Run tab and its '
        + 'marked pose drives the frame.',
    blank: () => ({ path: '', view: 'front', annotation: 'auto' }),
    fields: [
      { key: 'path', label: 'Image', type: 'image' },
      { key: 'view', label: 'Shows view', type: 'select', options: VIEW_OPTIONS },
      { key: 'annotation', label: 'Use annotation', type: 'select',
        options: ['auto', 'ignore'] },
    ],
  },
  'references.palette': {
    label: 'Palette references',
    help: 'Colours to lock to. Read once and imposed on every frame, so the '
        + 'result cannot drift.',
    blank: () => ({ path: '' }),
    fields: [{ key: 'path', label: 'Image or .hex', type: 'image' }],
  },
  'pose.set': {
    label: 'Pose set',
    help: 'Independent poses instead of one animation. Each entry may use its '
        + 'own view — a turnaround is one pose at several angles.',
    blank: () => ({ name: 'idle', view: 'front' }),
    fields: [
      { key: 'name', label: 'Pose', type: 'select', optionsFrom: 'poses' },
      { key: 'view', label: 'View', type: 'select', options: VIEW_OPTIONS },
      { key: 'frame', label: 'Frame #', type: 'int', min: 0, max: 64 },
    ],
  },
  'props': {
    label: 'Held objects',
    help: 'Attached to a joint, drawn into the depth map, and named in the '
        + 'prompt. Long thin objects work best; broad cloth is better served '
        + 'by soft-body nodes.',
    blank: () => ({
      name: 'sword', socket: 'l_wrist', offset: [0.02, 0.04, -0.01],
      aim: [0.25, 0.6, -0.8], length: 0.3, width: 0.022,
      second_socket: '', prompt: 'longsword', flex: 0, influence: 1, shade: 1,
    }),
    fields: [
      { key: 'name', label: 'Name', type: 'text' },
      { key: 'socket', label: 'Held at', type: 'select', optionsFrom: 'joints' },
      { key: 'second_socket', label: 'Second hand', type: 'select', optionsFrom: 'joints' },
      { key: 'prompt', label: 'Called', type: 'text' },
      { key: 'offset', label: 'Offset from joint', type: 'vec3' },
      { key: 'aim', label: 'Points toward', type: 'vec3' },
      { key: 'length', label: 'Length', type: 'float', min: 0.05, max: 0.8, step: 0.01 },
      { key: 'width', label: 'Width', type: 'float', min: 0.005, max: 0.2, step: 0.005 },
      { key: 'flex', label: 'Flex', type: 'float', min: 0, max: 1, step: 0.05 },
      { key: 'shade', label: 'Depth brightness', type: 'float', min: 0.2, max: 1.5, step: 0.05 },
      { key: 'influence', label: 'Influence', type: 'float', min: 0, max: 1, step: 0.05 },
    ],
  },
  'softbody.nodes': {
    label: 'Soft-body nodes',
    help: 'Loose mass that trails the skeleton. You set where it hangs and what '
        + "it's made of; the motion itself is simulated, never posed.",
    blank: () => ({
      name: 'belly', anchor: 'neck', offset: [0, 0.055, 0.19], radius: 0.16,
      stiffness: 70, damping: 4.5, mass: 1, max_displacement: 0.03,
      influence: 1, axis: [1, 1],
    }),
    fields: [
      { key: 'name', label: 'Name', type: 'text' },
      { key: 'anchor', label: 'Anchor joint', type: 'select', optionsFrom: 'joints' },
      { key: 'offset', label: 'Offset (lat, depth, height)', type: 'vec3' },
      { key: 'radius', label: 'Radius', type: 'float', min: 0.02, max: 0.5, step: 0.01 },
      { key: 'stiffness', label: 'Stiffness', type: 'float', min: 5, max: 400, step: 5 },
      { key: 'damping', label: 'Damping', type: 'float', min: 0.5, max: 30, step: 0.5 },
      { key: 'mass', label: 'Mass', type: 'float', min: 0.1, max: 5, step: 0.1 },
      { key: 'max_displacement', label: 'Max travel', type: 'float', min: 0, max: 0.12, step: 0.005 },
      { key: 'influence', label: 'Influence', type: 'float', min: 0, max: 1, step: 0.05 },
      { key: 'axis', label: 'Axis scale (x, y)', type: 'vec2' },
    ],
  },
};


function listEditor(path, items, onChange) {
  const spec = LIST_EDITORS[path];
  const list = Array.isArray(items) ? items : [];
  const box = el('div', { className: 'listeditor' });

  list.forEach((item, index) => {
    const card = el('div', { className: 'listcard' });
    const remove = el('button', { className: 'iconbtn', title: 'Remove', textContent: '✕' });
    remove.onclick = () => onChange(list.filter((_, i) => i !== index));
    card.append(el('div', { className: 'listcard-head' },
      el('span', { className: 'listcard-title', textContent: item.name || item.path || `#${index + 1}` }),
      remove));

    const grid = el('div', { className: 'listcard-grid' });
    for (const field of spec.fields) {
      grid.append(el('label', { className: 'mini', textContent: field.label }));
      grid.append(subControl(field, item[field.key], (value) => {
        const next = structuredClone(list);
        next[index][field.key] = value;
        onChange(next);
      }));
    }
    card.append(grid);
    box.append(card);
  });

  const add = el('button', { className: 'btn', textContent: `+ Add ${spec.label.replace(/s$/, '').toLowerCase()}` });
  add.onclick = () => onChange([...list, spec.blank()]);
  box.append(add);
  return box;
}

/* ---------------------------------------------------------------- section */

/** Render one settings group. `onChange(path, value)`, `onReset(path)`. */
export function renderGroup(group, cfg, { onChange, onReset, overrides = [] }) {
  const fields = state.schema.fields.filter((f) => f.group === group);
  const host = el('div', { className: 'fields' });

  for (const field of fields) {
    if (!visible(field, cfg)) continue;

    const pinned = overrides.includes(field.path);
    const row = el('div', { className: `field ${pinned ? 'pinned' : ''}` });
    // The explanation goes behind a (?) rather than under the control.
    //
    // The reasoning is worth keeping - it is measured, and docs/DECISIONS.md exists
    // because of it - but 119 fields each carrying a paragraph is a page
    // nobody reads any of. The lead sentence is the tooltip, so hovering
    // answers the common case; clicking reveals the rest.
    const tip = HelpTip(field.help);
    const label = el('div', {},
      el('div', { className: 'ui-label-row' },
        el('label', {}, field.label,
           pinned ? el('span', { className: 'dot', title: 'Set by this pipeline' }) : null),
        tip ? tip.btn : null),
      el('div', { className: 'path', textContent: field.path }));

    const right = el('div', { className: 'control-wrap' },
      control(field, getPath(cfg, field.path), (value) => onChange(field.path, value)));

    if (pinned && onReset) {
      const reset = el('button', { className: 'linkbtn', textContent: 'reset' });
      reset.title = 'Remove this override and inherit the global value';
      reset.onclick = () => onReset(field.path);
      right.append(reset);
    }

    row.append(el('div', { className: 'field-top' }, label, right));
    if (tip) row.append(tip.body);
    host.append(row);
  }

  // List editors attach to the group that owns their config path. References
  // owns four of them, one per role, because the roles are consumed
  // differently and carry weights an order of magnitude apart.
  for (const [path, spec] of Object.entries(LIST_EDITORS)) {
    if (spec.group !== group && !groupOwnsPath(group, path)) continue;
    const row = el('div', { className: 'field' },
      el('div', { className: 'field-top' },
        el('div', {},
          el('label', { textContent: spec.label }),
          el('div', { className: 'path', textContent: path }))),
      spec.help ? el('p', { className: 'help', textContent: spec.help }) : null,
      listEditor(path, getPath(cfg, path), (value) => onChange(path, value)));
    host.append(row);
  }

  return host;
}

const GROUP_PATHS = {
  Props: 'props',
  References: 'references.identity',
  Pose: 'pose.set',
  Softbody: 'softbody.nodes',
};

function groupOwnsPath(group, path) {
  if (group === 'References') return path.startsWith('references.');
  return GROUP_PATHS[group] === path;
}
