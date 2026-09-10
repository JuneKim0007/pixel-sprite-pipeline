// Settings tab — macOS-style category sidebar with a scope switcher.

import { api, delPath, getPath, setPath } from '../../api.js';
import { showError } from '../../core/errors.js';
import { Button } from '../../ui/index.js';
import { renderGroup } from '../../fields.js';
import { el } from '../../core/dom.js';
import { browseDialog } from '../../ui/dialog.js';
import { remember } from '../../core/history.js';
import { state, toast } from '../../store.js';

// An ordering hint, not a whitelist.
const ORDER = [
  'Asset', 'Proportions', 'Pipeline', 'Pose', 'Depth', 'Canonical', 'Frames',
  'Pose control', 'Identity', 'References', 'Props', 'Softbody', 'Palette',
  'Quality', 'Export', 'LLM', 'Models', 'Compute', 'Services', 'Paths',
];

/* Groups with no schema fields yet, which still need a home in the sidebar
 * because their editor is hand-written rather than generated. */
const ALWAYS = new Set(['Paths', 'Softbody', 'References']);

function sectionOrder(counts) {
  const known = new Set(ORDER);
  const extra = Object.keys(counts).filter((g) => g && !known.has(g)).sort();
  return [...ORDER, ...extra];
}

// Labelled 'usually set globally'; still editable in the pipeline scope.
const GLOBAL_ONLY = new Set(['Models', 'Compute', 'Services', 'Paths']);
// Hidden there outright, because nothing in it is per-pipeline at all.
const PIPELINE_HIDDEN = new Set(['Services']);

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
    btn.onclick = () => {
      remember();
      state.scope = value;
      renderSettings(host, { onSaved });
    };
    scopeBar.append(btn);
  }

  const hint = el('p', { className: 'scopehint', textContent: isGlobal
    ? 'Changing a default here affects every pipeline that has not pinned its own value.'
    : 'Values shown are inherited from global unless pinned. Changing one pins it to this pipeline.' });

  /* -- category sidebar */
  const nav = el('nav', { className: 'subnav' });
  const body = el('div', { className: 'subbody' });

  const shown = sectionOrder(counts).filter((s) => counts[s] || ALWAYS.has(s));

  for (const name of shown) {
    if (!isGlobal && PIPELINE_HIDDEN.has(name)) continue;
    const pinnedHere = state.overrides.filter((p) => fieldGroup(p) === name).length;
    const item = el('div', {
      className: `subnav-item ${state.settingsSection === name ? 'on' : ''}`,
    },
      el('span', { textContent: name }),
      el('span', { className: 'count', textContent: counts[name] ? String(counts[name]) : '·' }),
      (!isGlobal && pinnedHere) ? el('span', { className: 'dot' }) : null);
    item.onclick = () => {
      remember();
      state.settingsSection = name;
      renderSettings(host, { onSaved });
    };
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
      ? pathsSection(isGlobal, onChange, isGlobal ? null : onReset)
      : renderGroup(section, cfg, {
          onChange, onReset: isGlobal ? null : onReset,
          overrides: isGlobal ? [] : state.overrides,
        })));

  /* -- save bar */
  const save = Button('Save', { variant: 'primary', disabled: !state.dirty });
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
      showError(e);
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

const PATH_ROWS = [
  ['paths.input_dir', 'Input folder', false,
   'Where uploads land, and the root the image browser opens in. Machine-level: '
   + 'uploads arrive before a pipeline is chosen.'],
  ['paths.output_dir', 'Output folder', true,
   'Where runs are written. A pipeline may pin its own — references.from_run '
   + 'resolves against it.'],
  ['paths.download_dir', 'Export folder', false,
   'Default target when exporting results. Machine-level.'],
];

function pathsSection(isGlobal, onChange, onReset) {
  const host = el('div', { className: 'fields' });
  for (const [path, label, pinnable, help] of PATH_ROWS) {
    const editable = isGlobal || pinnable;
    const value = getPath(isGlobal ? state.global : state.effective, path) ?? '';
    const pinned = !isGlobal && state.overrides.includes(path);

    const input = el('input', {
      type: 'text', className: 'wide', value, disabled: !editable,
    });
    input.onchange = () => onChange(path, input.value);

    const browse = el('button', {
      className: 'btn ghost', textContent: 'Browse…', disabled: !editable,
    });
    browse.onclick = async () => {
      const chosen = await browseDialog(value, false);
      if (chosen) { input.value = chosen; onChange(path, chosen); }
    };

    const reset = (pinned && onReset)
      ? el('button', { className: 'btn ghost mini', textContent: 'Reset' })
      : null;
    if (reset) reset.onclick = () => onReset(path);

    host.append(el('div', { className: 'field' },
      el('div', { className: 'field-top stacked' },
        el('div', {},
          el('label', { textContent: label }),
          pinned ? el('span', { className: 'dot' }) : null,
          el('div', { className: 'path', textContent: path })),
        el('div', { className: 'control-wrap' }, input, browse, reset)),
      el('p', { className: 'help', textContent: editable ? help
        : `${help} Set it under Global defaults.` })));
  }
  return host;
}
