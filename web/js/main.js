/* Shell: four tabs, shared polling, boot.
 *
 *   Input    what to make this time
 *   Run      the guided flow, with the rig editor inside it
 *   Result   what came out, per stage
 *   Settings how the machine behaves — global, or pinned per pipeline
 */

import { api } from './api.js';
import { renderInput } from './input.js';
import { renderResult } from './result.js';
import { renderRun } from './run.js';
import { renderSettings } from './settings.js';
import { renderStyles } from './styles.js';
import { $, $$, el, loadConfig, state } from './store.js';

const TABS = ['input', 'run', 'result', 'styles', 'settings'];

function setTab(name) {
  state.tab = name;
  $$('#nav li').forEach((li) => li.classList.toggle('active', li.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  render();
}

function render() {
  if (!state.schema) return;
  if (state.tab === 'input') {
    renderInput($('#view-input'), {
      onChange: (path, value) => { state.draft[path] = value; render(); },
      onContinue: () => setTab('run'),
    });
  } else if (state.tab === 'run') {
    renderRun($('#view-run'), {
      onStarted: () => { setTab('result'); refreshRuns(); },
      goTo: setTab,
    });
  } else if (state.tab === 'result') {
    renderResultTab();
  } else if (state.tab === 'styles') {
    renderStyles($('#view-styles'), {
      onChanged: async () => { await loadConfig(state.current); },
    });
  } else if (state.tab === 'settings') {
    renderSettings($('#view-settings'), {
      onSaved: async () => { await loadConfig(state.current); },
    });
  }
  renderFlow();
}

async function renderResultTab() {
  const host = $('#view-result');
  if (!state.selectedRun) {
    host.replaceChildren(el('p', { className: 'empty', textContent: 'No runs yet.' }));
    return;
  }
  try {
    const detail = await api.run(state.selectedRun);
    renderResult(host, { runId: state.selectedRun, detail });
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

async function refreshRuns() {
  try {
    const { runs } = await api.runs();
    state.runs = runs;
    if (!state.selectedRun && runs.length) state.selectedRun = runs[0].id;
    renderRunPicker();
    renderFlow();
    if (state.tab === 'result') await renderResultTab();
  } catch { /* server restarting; the next tick retries */ }
}

async function refreshConfigs(select = null) {
  const { configs } = await api.configs();
  state.configs = configs;
  const sel = $('#configPicker');
  sel.replaceChildren(...configs.map((c) =>
    el('option', { value: c, textContent: c, selected: c === (select || state.current) })));
  const pick = select || state.current || configs[0];
  if (pick) await loadConfig(pick);
}

async function boot() {
  state.tab = 'input';
  state.schema = await api.schema(state.module);
  state.system = await api.system().catch(() => null);
  state.global = (await api.global().catch(() => ({ config: {} }))).config || {};

  // Joint names are only known to the schema consumer; the soft-body editor
  // needs them as a dropdown source.
  const { JOINTS } = await import('./views.js');
  state.schema.options.joints = JOINTS;

  renderServices();
  await refreshConfigs();
  await refreshRuns();

  $$('#nav li').forEach((li) => { li.onclick = () => setTab(li.dataset.view); });
  $('#configPicker').onchange = async (e) => {
    await loadConfig(e.target.value);
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
    refreshRuns();
  });

  setTab('input');

  // Poll only while something is live, or while the Result tab is open.
  setInterval(() => {
    const live = state.runs.some((r) => r.running);
    if (live || state.tab === 'result') refreshRuns();
  }, 4000);
}

boot().catch((e) => {
  document.body.replaceChildren(
    el('pre', { style: 'padding:24px;font:13px ui-monospace,monospace', textContent:
      `Failed to start the UI: ${e.message}` }));
});
