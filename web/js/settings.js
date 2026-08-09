/* Settings tab — macOS-style category sidebar with a scope switcher.
 *
 * Two scopes share one form. Global holds machine-level answers (compute,
 * models, paths) that are true for every pipeline; the pipeline scope shows
 * the same fields with inherited values, and pins one only when you change it.
 * A pinned field gets a dot and a reset link, because "inherited 7.0" and
 * "pinned to 7.0" behave differently the moment the global changes.
 */

import { api, delPath, getPath, setPath } from './api.js';
import { renderGroup } from './fields.js';
import { el, state, toast } from './store.js';

/* Ordered so the common edits sit near the top. */
const SECTIONS = [
  'Asset', 'Proportions', 'Pipeline', 'Pose', 'Depth', 'Canonical', 'Frames',
  'Pose control', 'Identity', 'References', 'Props', 'Softbody', 'Palette',
  'LLM', 'Models', 'Compute', 'Services', 'Paths',
];

const GLOBAL_ONLY = new Set(['Models', 'Compute', 'Services', 'Paths']);

function sectionCounts() {
  const counts = {};
  for (const field of state.schema.fields) {
    counts[field.group] = (counts[field.group] || 0) + 1;
  }
  return counts;
}

export function renderSettings(host, { onSaved }) {
  host.replaceChildren();
  const counts = sectionCounts();
  const isGlobal = state.scope === 'global';

  /* -- scope switcher */
  const scopeBar = el('div', { className: 'scopebar' });
  for (const [value, label] of [['global', 'Global defaults'], ['pipeline', state.current || 'pipeline']]) {
    const btn = el('button', {
      className: `scopebtn ${state.scope === value ? 'on' : ''}`,
      textContent: label,
    });
    btn.onclick = () => { state.scope = value; renderSettings(host, { onSaved }); };
    scopeBar.append(btn);
  }

  const hint = el('p', { className: 'scopehint', textContent: isGlobal
    ? 'Changing a default here affects every pipeline that has not pinned its own value.'
    : 'Values shown are inherited from global unless pinned. Changing one pins it to this pipeline.' });

  /* -- category sidebar */
  const nav = el('nav', { className: 'subnav' });
  const body = el('div', { className: 'subbody' });

  const shown = SECTIONS.filter((s) =>
    counts[s] || s === 'Paths' || s === 'Softbody' || s === 'References');

  for (const name of shown) {
    if (!isGlobal && GLOBAL_ONLY.has(name) && name !== 'Models' && name !== 'Compute') continue;
    const pinnedHere = state.overrides.filter((p) => fieldGroup(p) === name).length;
    const item = el('div', {
      className: `subnav-item ${state.settingsSection === name ? 'on' : ''}`,
    },
      el('span', { textContent: name }),
      el('span', { className: 'count', textContent: counts[name] ? String(counts[name]) : '·' }),
      (!isGlobal && pinnedHere) ? el('span', { className: 'dot' }) : null);
    item.onclick = () => { state.settingsSection = name; renderSettings(host, { onSaved }); };
    nav.append(item);
  }

  /* -- the form itself */
  const cfg = isGlobal ? state.global : state.effective;

  const onChange = (path, value) => {
    if (isGlobal) {
      setPath(state.global, path, value);
    } else {
      setPath(state.own, path, value);
      setPath(state.effective, path, value);
      if (!state.overrides.includes(path)) state.overrides.push(path);
      state.unset = state.unset.filter((p) => p !== path);
    }
    state.dirty = true;
    renderSettings(host, { onSaved });
  };

  const onReset = (path) => {
    delPath(state.own, path);
    state.overrides = state.overrides.filter((p) => p !== path);
    if (!state.unset.includes(path)) state.unset.push(path);
    state.dirty = true;
    // The displayed value falls back on the next load; refetch to show it.
    api.config(state.current).then((data) => {
      state.effective = data.effective || {};
      renderSettings(host, { onSaved });
    });
  };

  const section = state.settingsSection;
  body.append(el('div', { className: 'group' },
    el('h2', {}, section,
      GLOBAL_ONLY.has(section) && !isGlobal
        ? el('span', { className: 'headnote', textContent: 'usually set globally' })
        : null),
    section === 'Paths'
      ? pathsSection(isGlobal, onChange)
      : renderGroup(section, cfg, {
          onChange, onReset: isGlobal ? null : onReset,
          overrides: isGlobal ? [] : state.overrides,
        })));

  /* -- save bar */
  const save = el('button', { className: 'btn primary', textContent: 'Save', disabled: !state.dirty });
  save.onclick = async () => {
    try {
      if (isGlobal) {
        await api.saveGlobal(state.global);
      } else {
        await api.saveConfig(state.current, { config: state.own, unset: state.unset });
      }
      state.dirty = false;
      state.unset = [];
      toast('Saved');
      await onSaved?.();
      renderSettings(host, { onSaved });
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  host.append(
    el('div', { className: 'settings-head' }, scopeBar, save),
    hint,
    el('div', { className: 'settings-body' }, nav, body));
}

function fieldGroup(path) {
  return state.schema.fields.find((f) => f.path === path)?.group;
}

/* Directories are global-only: a pipeline that redefined where runs are
 * written would scatter output unpredictably. */
function pathsSection(isGlobal, onChange) {
  const host = el('div', { className: 'fields' });
  if (!isGlobal) {
    host.append(el('p', { className: 'empty', textContent: 'Folders are configured globally.' }));
    return host;
  }
  const rows = [
    ['paths.input_dir', 'Input folder', 'Where uploads land and the image browser opens.'],
    ['paths.output_dir', 'Output folder', 'Where runs are written.'],
    ['paths.download_dir', 'Download folder', 'Default target when exporting results.'],
  ];
  for (const [path, label, help] of rows) {
    const input = el('input', { type: 'text', style: 'width:340px', value: getPath(state.global, path) ?? '' });
    input.onchange = () => onChange(path, input.value);
    host.append(el('div', { className: 'field' },
      el('div', { className: 'field-top' },
        el('div', {},
          el('label', { textContent: label }),
          el('div', { className: 'path', textContent: path })),
        el('div', { className: 'control-wrap' }, input)),
      el('p', { className: 'help', textContent: help })));
  }
  return host;
}
