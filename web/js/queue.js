/* The queue, and the autopilot that drains it.
 *
 * Both were complete and reachable only from a shell, which is a strange place
 * to put the feature whose entire purpose is running unattended for hours: the
 * moment you most want to look at it is from somewhere other than the terminal
 * that started it.
 *
 * The view is built around one asymmetry. A pending job is a claim that
 * something will work, and preflight can check that claim in milliseconds
 * without touching the GPU — so it does, here, and shows the result. A job
 * that can never work is visible before the night is spent discovering it,
 * which is the difference between finding one broken config in the morning and
 * finding two hundred failures.
 *
 * Held is deliberately not an error state and is not coloured like one. A job
 * is held when its dependency has not been produced yet, which during a
 * chained overnight batch is the normal condition of most of the queue.
 */

import { api } from './api.js';
import { el, state, toast } from './store.js';

const STATES = [
  { key: 'running', label: 'Running', tone: 'run' },
  { key: 'pending', label: 'Pending', tone: 'idle' },
  { key: 'held', label: 'Held', tone: 'wait' },
  { key: 'done', label: 'Done', tone: 'ok' },
  { key: 'failed', label: 'Failed', tone: 'bad' },
];

let showing = 'pending';
let timer = null;

function jobRow(job, onAct) {
  const row = el('div', { className: `jobrow ${job.state}` });

  const facts = [job.config];
  if (job.module) facts.push(job.module);
  if (job.attempts) facts.push(`${job.attempts} attempt(s)`);
  if (job.run_id) facts.push(job.run_id);

  const cells = Object.entries(job.matrix_cell || job.overrides || {});
  const main = el('div', { className: 'jobmain' },
    el('div', { className: 'jobid', textContent: job.id }),
    el('div', { className: 'mini', textContent: facts.join(' · ') }));

  if (cells.length) {
    main.append(el('div', { className: 'joboverrides' },
      ...cells.map(([k, v]) =>
        el('span', { className: 'frag', textContent: `${k} = ${JSON.stringify(v)}` }))));
  }

  // Preflight is only computed for pending jobs; for the rest it is either
  // moot or already history.
  const pf = job.preflight;
  if (pf && pf.problems.length) {
    for (const p of pf.problems) {
      main.append(el('p', { className: 'warnline', textContent: `✗ ${p}` }));
    }
  } else if (pf && pf.waiting_on.length) {
    main.append(el('p', { className: 'mini', textContent:
      `⏸ waiting on ${pf.waiting_on.join(', ')} — this is normal in a chained batch` }));
  } else if (pf) {
    main.append(el('p', { className: 'ok', textContent: '✓ preflight passes' }));
  }

  if (job.error) {
    main.append(el('pre', { className: 'joberror', textContent: job.error }));
  }
  if (job.needs?.length) {
    main.append(el('div', { className: 'mini', textContent: `needs ${job.needs.join(', ')}` }));
  }

  const actions = el('div', { className: 'jobactions' });
  if (job.state !== 'running') {
    const add = (action, label, title) => {
      const b = el('button', { className: 'pill', textContent: label, title });
      b.onclick = () => onAct(job.id, action);
      actions.append(b);
    };
    if (job.state !== 'pending') add('retry', 'retry', 'Move back to pending and reset attempts');
    if (job.state === 'pending') add('hold', 'hold', 'Postpone for an hour');
    add('drop', 'drop', 'Delete this job');
  }

  row.append(main, actions);
  return row;
}

function autopilotBar(data, refresh) {
  const { running, started } = data.autopilot;
  const bar = el('div', { className: `pilotbar ${running ? 'on' : ''}` });

  const act = async (action, extra = {}) => {
    try {
      const r = await api.autopilot({ action, ...extra });
      toast(r.note || (r.running ? 'Autopilot started' : 'Autopilot stopping'));
      refresh();
    } catch (e) { toast(e.message, 'error'); }
  };

  const start = el('button', { className: 'btn primary', textContent: 'Start autopilot',
                               disabled: running });
  start.onclick = () => act('start');
  const drain = el('button', { className: 'btn', textContent: 'Start and drain',
                               disabled: running,
                               title: 'Exit when the queue empties instead of idling' });
  drain.onclick = () => act('start', { drain: true });
  const stop = el('button', { className: 'btn', textContent: 'Stop', disabled: !running,
                              title: 'Finishes the job it is on, then exits' });
  stop.onclick = () => act('stop');

  const services = data.services;
  bar.append(
    el('div', {},
      el('div', { className: 'pilotstate' },
        el('span', { className: `dot ${running ? 'up' : ''}` }),
        el('b', { textContent: running ? 'Autopilot running' : 'Autopilot stopped' })),
      el('div', { className: 'mini', textContent: running
        ? `since ${started}`
        : services.ok
          ? 'services are up — the queue can be drained'
          : `services down: ${services.why}` })),
    el('div', { className: 'row' }, start, drain, stop));
  return bar;
}

function submitForm(refresh) {
  const box = el('details', { className: 'submitbox' });
  box.append(el('summary', { textContent: 'Queue a job' }));

  const config = el('select', { className: 'select' });
  for (const name of state.configs || []) {
    config.append(el('option', { value: name, textContent: name,
                                 selected: name === state.current }));
  }
  const priority = el('input', { type: 'number', value: 50, className: 'num',
                                 min: 0, max: 9999 });
  const matrix = el('textarea', { rows: 4, placeholder:
    '{"canonical.seed": [1, 2, 3], "pose.view": ["front", "side"]}' });

  const go = el('button', { className: 'btn primary', textContent: 'Submit' });
  go.onclick = async () => {
    let parsed = {};
    const raw = matrix.value.trim();
    if (raw) {
      try { parsed = JSON.parse(raw); }
      catch (e) { return toast(`matrix is not valid JSON: ${e.message}`, 'error'); }
    }
    try {
      const r = await api.queueSubmit(
        { config: config.value, ...(Object.keys(parsed).length ? { matrix: parsed } : {}) },
        Number(priority.value));
      toast(`Queued ${r.count} job(s)`);
      matrix.value = '';
      refresh();
    } catch (e) { toast(e.message, 'error'); }
  };

  box.append(el('div', { className: 'fields' },
    el('div', { className: 'row' },
      el('span', { className: 'mini', textContent: 'Config' }), config,
      el('span', { className: 'mini', textContent: 'Priority' }), priority),
    matrix,
    el('div', { className: 'row' }, go)));
  return box;
}

export function renderQueue(host) {
  const refresh = () => renderQueue(host);
  host.replaceChildren();

  const body = el('div', {});
  host.append(
    el('header', { className: 'head' },
      el('div', {},
        el('h1', { textContent: 'Queue' }),
))),
    body);

  (async () => {
    let data;
    try {
      data = await api.queue();
    } catch (e) {
      body.append(el('p', { className: 'empty', textContent: e.message }));
      return;
    }

    const onAct = async (id, action) => {
      try {
        await api.queueJob(id, action);
        toast(`${action} ${id}`);
        refresh();
      } catch (e) { toast(e.message, 'error'); }
    };

    const tabs = el('div', { className: 'segmented' });
    for (const s of STATES) {
      const n = data.counts[s.key] || 0;
      const b = el('button', {
        className: `seg ${showing === s.key ? 'on' : ''}`,
        textContent: n ? `${s.label} ${n}` : s.label,
      });
      b.onclick = () => { showing = s.key; refresh(); };
      tabs.append(b);
    }

    const jobs = data.states[showing] || [];
    const list = el('div', { className: 'joblist' });
    if (!jobs.length) {
      list.append(el('p', { className: 'empty', textContent: `Nothing ${showing}.` }));
    } else {
      for (const job of jobs) list.append(jobRow(job, onAct));
    }

    body.replaceChildren(
      autopilotBar(data, refresh),
      submitForm(refresh),
      tabs,
      list,
      el('p', { className: 'mini', textContent: data.dir }));

    // Poll only while there is something in motion. A queue at rest does not
    // need a request every three seconds, and this view stays open for hours.
    clearTimeout(timer);
    if (data.autopilot.running || data.counts.running) {
      timer = setTimeout(() => { if (state.tab === 'queue') refresh(); }, 4000);
    }
  })();
}
