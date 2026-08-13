/* Shell: six tabs, shared polling, boot.
 *
 *   Input    what to make this time
 *   Run      the guided flow, with the rig editor inside it
 *   Result   what came out, per stage
 *   Styles   the looks, their context, and what has been done to them
 *   Queue    jobs on disk and the autopilot that drains them
 *   Settings how the machine behaves — global, or pinned per pipeline
 */

import { api } from './api.js';
import { renderInput } from './input.js';
import { renderResult } from './result.js';
import { renderRun } from './run.js';
import { renderSettings } from './settings.js';
import { renderStyles } from './styles.js';
import { renderQueue } from './queue.js';
import { renderEditor } from './editor.js';
import { renderOverview } from './overview.js';
import { configsFor, indexConfigModules, renderRail } from './rail.js';
import { $, $$, el } from './core/dom.js';
import { draft, loadConfig, state } from './store.js';

const TABS = ['overview', 'input', 'run', 'result', 'styles', 'editor', 'queue', 'settings'];

function setTab(name) {
  state.tab = name;
  $$('#nav li').forEach((li) => li.classList.toggle('active', li.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  render();
}

function render() {
  if (!state.schema) return;
  if (state.tab === 'overview') {
    renderOverview($('#view-overview'), { goTo: setTab });
  } else if (state.tab === 'input') {
    renderInput($('#view-input'), {
      onChange: (path, value) => { draft()[path] = value; render(); },
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
  } else if (state.tab === 'editor') {
    renderEditor($('#view-editor'));
  } else if (state.tab === 'queue') {
    renderQueue($('#view-queue'));
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
    renderResult(host, {
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
  await indexConfigModules();
  const pick = select || state.current || configs[0];
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
  sel.disabled = mine.length < 2;
}

function renderRailBar() {
  renderRail($('#railbar'), {
    onSwitch: () => { renderConfigPicker(); render(); },
  });
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
  renderRailBar();
  await refreshRuns();

  $$('#nav li').forEach((li) => { li.onclick = () => setTab(li.dataset.view); });
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
    refreshRuns();
  });

  setTab('overview');

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
