/* Shell: six tabs, shared polling, boot.
 *
 *   Input    what to make this time
 *   New run  the guided flow, with the rig editor inside it
 *   Result   what came out, per stage
 *   Styles   the looks, their context, and what has been done to them
 *   Queue    jobs on disk and the autopilot that drains them
 *   Settings how the machine behaves — global, or pinned per pipeline
 */

import { api } from './api.js';
import { renderInput } from './views/input/input.js';
import { renderResult } from './views/result/result.js';
import { renderRun } from './views/run/run.js';
import { renderSettings } from './views/settings/settings.js';
import { renderStyles } from './views/styles/styles.js';
import { renderQueue } from './views/queue/queue.js';
import { renderEditor } from './views/editor/editor.js';
import { renderOverview } from './views/overview/overview.js';
import { configsFor, renderRail } from './rail.js';
import { newPipelineDialog, newTypeDialog } from './library.js';
import { $, $$, el } from './core/dom.js';
import { mount } from './listeners/lifecycle.js';
import { poll } from './listeners/poll.js';
import * as history from './core/history.js';
import { draft, loadConfig, state, toast } from './store.js';

const TABS = ['overview', 'input', 'run', 'result', 'styles', 'editor', 'queue', 'settings'];

function setTab(name, { record = true } = {}) {
  if (record) history.remember();
  state.tab = name;
  showTab(name);
  render();
}

function showTab(name) {
  $$('#nav li').forEach((li) => li.classList.toggle('active', li.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
}

history.install({
  read: () => history.snapshot(state),
  restore: (where) => { Object.assign(state, where); showTab(state.tab); render(); },
  onChange: (can) => { const b = $('#goback'); if (b) b.disabled = !can; },
});

/* One table, and the mounter calls it.
 *
 * This was an if-chain of eight branches, which is the same shape the server's
 * routing had before it became data - and it had the same problem: nothing
 * could enumerate the views, and nobody could tear one down because there was
 * no moment that meant "leaving".
 *
 * `mount` calls the previous view's teardown before the next one renders, so a
 * poll or a subscription cannot outlive the tab that started it.
 */
const VIEWS = {
  overview: (host) => renderOverview(host, { goTo: setTab }),
  input: (host) => renderInput(host, {
    onChange: (path, value) => { draft()[path] = value; render(); },
    onContinue: () => setTab('run'),
  }),
  run: (host) => renderRun(host, {
    onStarted: () => { setTab('result'); refreshRuns({ force: true }); watchRuns(); },
    goTo: setTab,
  }),
  result: () => { renderResultTab(); return () => { if (stopResult) stopResult(); }; },
  styles: (host) => renderStyles(host, {
    onChanged: async () => { await loadConfig(state.current); },
  }),
  editor: (host) => renderEditor(host),
  queue: (host) => renderQueue(host),
  settings: (host) => renderSettings(host, {
    onSaved: async () => { await loadConfig(state.current); },
  }),
};

function render() {
  if (!state.schema) return;
  const view = VIEWS[state.tab];
  if (view) mount(state.tab, $(`#view-${state.tab}`), view);
  renderFlow();
}

// The result tab is redrawn by the run poll as well as by a tab switch, so it
// owns its own teardown rather than handing one to mount().
let stopResult = null;

async function renderResultTab() {
  const host = $('#view-result');
  if (stopResult) { stopResult(); stopResult = null; }
  if (!state.selectedRun) {
    host.replaceChildren(el('p', { className: 'empty', textContent: 'No runs yet.' }));
    return;
  }
  try {
    const detail = await api.run(state.selectedRun);
    stopResult = renderResult(host, {
      runId: state.selectedRun, detail,
      onPick: (id) => { state.selectedRun = id; renderResultTab(); },
    });
  } catch (e) {
    host.replaceChildren(el('p', { className: 'empty', textContent: e.message }));
  }
}

/* Sidebar: stage flow with live completion state. */
function renderFlow() {
  const cfg = state.effective || {};
  const stages = cfg.pipeline?.stages || [];
  const run = state.runs.find((r) => r.id === state.selectedRun);
  const done = new Set(run?.completed || []);

  $('#stageflow').replaceChildren(...stages.map((name) => {
    const meta = state.schema.stages.find((s) => s.name === name);
    return el('li', { className: `${meta?.resource || ''} ${done.has(name) ? 'done' : ''}` },
      el('span', { textContent: name }),
      el('span', { className: 'res', textContent: done.has(name) ? '✓' : (meta?.resource || '') }));
  }));
}

function renderServices() {
  const s = state.system?.services || {};
  const row = (name, info) => el('div', { className: 'svc' },
    el('span', { className: `dot ${info?.up ? 'up' : ''}` }),
    name,
    info?.models?.length ? el('span', { className: 'dim', textContent: ` (${info.models.length})` }) : null);
  $('#services').replaceChildren(row('ComfyUI', s.comfyui), row('Ollama', s.ollama));
}

function renderRunPicker() {
  const sel = $('#runPicker');
  const previous = state.selectedRun;
  sel.replaceChildren();
  for (const run of state.runs) {
    sel.append(el('option', {
      value: run.id,
      textContent: run.id + (run.running ? '  ● live' : run.stopped_at ? '  ⏸ paused' : ''),
      selected: run.id === previous,
    }));
  }
  if (!state.runs.length) sel.append(el('option', { value: '', textContent: 'no runs yet' }));
}

/* What a refresh would change on screen, as one string.
 *
 * `renderResult` opens with `host.replaceChildren()`, which is right when a
 * view mounts and wrong every four seconds: it takes scroll position, focus and
 * image decode with it. Comparing first means an unchanged payload costs one
 * request and no DOM at all. */
function runsSignature(runs, selected) {
  return runs.map((r) => [
    r.id, r.modified, r.running ? 1 : 0, r.stopped_at || '',
    (r.completed || []).join('.'),
    (r.stages || []).map((s) => `${s.dir}:${s.images.length}`).join(','),
  ].join('|')).join('\n') + `#${selected || ''}`;
}

let lastSignature = null;
let stopWatching = null;

/* Runs only while a run is running, and again when one starts.
 *
 * It used to run whenever the Result tab was merely open, which is a finished
 * run being asked about every four seconds for as long as the tab stays open.
 * Stopping when idle is only safe if starting is covered, so every path that
 * begins or resumes a run calls this. */
function watchRuns() {
  if (stopWatching) return;
  stopWatching = poll(async () => {
    await refreshRuns();
    if (state.runs.some((r) => r.running)) return false;
    stopWatching = null;
    return true;
  }, { every: 4000, immediate: false });
}

async function refreshRuns({ force = false } = {}) {
  try {
    const { runs } = await api.runs();
    if (!state.selectedRun && runs.length) state.selectedRun = runs[0].id;

    const signature = runsSignature(runs, state.selectedRun);
    if (!force && signature === lastSignature) return;
    lastSignature = signature;

    state.runs = runs;
    renderRunPicker();
    renderFlow();
    if (state.tab === 'result') await renderResultTab();
  } catch { /* server restarting; the next tick retries */ }
}

async function refreshConfigs(select = null) {
  const { configs } = await api.configs();
  state.configs = configs;
  const pick = select || state.current || configs[0]?.name;
  if (pick) await loadConfig(pick);
  renderConfigPicker();
}

/* Only this workspace's pipelines. A character-sheet config in an animation
 * list is a config that changes the schema under you when picked. */
function renderConfigPicker() {
  const mine = configsFor(state.module);
  const sel = $('#configPicker');
  sel.replaceChildren(...mine.map((c) =>
    el('option', { value: c, textContent: c, selected: c === state.current })));
  // One pipeline is still a choice now that a second can be made from here.
  sel.disabled = !mine.length;

  const add = $('#configNew');
  add.onclick = async () => {
    const meta = state.schema?.modules?.[state.module];
    const made = await newPipelineDialog(state.module, meta);
    if (made) await refreshConfigs(made);
  };
}

function renderRailBar() {
  renderRail($('#railbar'), {
    onSwitch: () => { renderConfigPicker(); render(); },

    onEmpty: async (key, meta) => {
      const made = await newPipelineDialog(key, meta);
      if (made) await refreshConfigs(made);
      return made;
    },

    // A new type has no pipeline, and the rail cannot open a workspace without
    // one, so making the type offers to make its first pipeline too.
    onNewType: async () => {
      const key = await newTypeDialog();
      if (!key) return;
      state.schema = await api.schema(state.module);
      renderRailBar();
      const meta = state.schema.modules?.[key];
      if (meta?.available) {
        const made = await newPipelineDialog(key, meta);
        if (made) { await refreshConfigs(made); renderRailBar(); }
      }
      render();
    },
  });
}

async function boot() {
  state.tab = 'input';
  state.schema = await api.schema(state.module);
  state.system = await api.system().catch(() => null);
  state.global = (await api.global().catch(() => ({ config: {} }))).config || {};

  // Joint names are only known to the schema consumer; the soft-body editor
  // needs them as a dropdown source.
  const { JOINTS } = await import('./features/pose.js');
  state.schema.options.joints = JOINTS;

  renderServices();
  await refreshConfigs();
  renderRailBar();
  await refreshRuns();

  $$('#nav li').forEach((li) => { li.onclick = () => setTab(li.dataset.view); });

  const back = $('#goback');
  if (back) back.onclick = () => history.goBack();

  $('#configPicker').onchange = async (e) => {
    await loadConfig(e.target.value);
    renderRailBar();
    render();
  };
  $('#runPicker').onchange = (e) => {
    state.selectedRun = e.target.value;
    render();
  };

  // The Result tab's gate banner acts in place rather than sending you to
  // another tab to find the right control.
  window.addEventListener('pipeline:edit', (e) => {
    state.selectedRun = e.detail.runId;
    state.wizardStep = e.detail.step ?? 0;
    setTab(e.detail.tab || 'run');
  });
  window.addEventListener('pipeline:resumed', (e) => {
    state.selectedRun = e.detail.runId;
    state.activeRun = e.detail.runId;
    refreshRuns({ force: true });
    watchRuns();
  });

  setTab('overview');
  if (state.runs.some((r) => r.running)) watchRuns();
}

// mount() catches a view that throws while rendering and poll() catches its own
// tick, but an async click handler rejects into nowhere - which is how a run that
// started looked like a button that did nothing.
window.addEventListener('unhandledrejection', (e) => {
  toast(e.reason?.message || String(e.reason ?? 'Something went wrong'), 'error');
});
window.addEventListener('error', (e) => {
  toast(e.message || 'Something went wrong', 'error');
});

boot().catch((e) => {
  document.body.replaceChildren(
    el('pre', { style: 'padding:24px;font:13px ui-monospace,monospace', textContent:
      `Failed to start the UI: ${e.message}` }));
});
