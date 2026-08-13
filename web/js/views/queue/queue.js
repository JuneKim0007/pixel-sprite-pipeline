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

import { api } from '../../api.js';
import { el } from '../../core/dom.js';
import { state, toast } from '../../store.js';
import { Button, Empty, Fields, Head, Mini, Num, Ok, Row, Segmented, Select, Warn } from '../../ui/index.js';
import { poll } from '../../listeners/poll.js';

const STATES = [
  { key: 'running', label: 'Running', tone: 'run' },
  { key: 'pending', label: 'Pending', tone: 'idle' },
  { key: 'held', label: 'Held', tone: 'wait' },
  { key: 'done', label: 'Done', tone: 'ok' },
  { key: 'failed', label: 'Failed', tone: 'bad' },
];

let showing = 'pending';

function jobRow(job, onAct) {
  const row = el('div', { className: `jobrow ${job.state}` });

  const facts = [job.config];
  if (job.module) facts.push(job.module);
  if (job.attempts) facts.push(`${job.attempts} attempt(s)`);
  if (job.run_id) facts.push(job.run_id);

  const cells = Object.entries(job.matrix_cell || job.overrides || {});
  const main = el('div', { className: 'jobmain' },
    el('div', { className: 'jobid', textContent: job.id }),
    Mini(facts.join(' · ')));

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
      main.append(Warn(p));
    }
  } else if (pf && pf.waiting_on.length) {
    main.append(el('p', { className: 'mini', textContent:
      `⏸ waiting on ${pf.waiting_on.join(', ')} — this is normal in a chained batch` }));
  } else if (pf) {
    main.append(Ok('preflight passes'));
  }

  if (job.error) {
    main.append(el('pre', { className: 'joberror', textContent: job.error }));
  }
  if (job.needs?.length) {
    main.append(Mini(`needs ${job.needs.join(', ')}`));
  }

  const actions = el('div', { className: 'jobactions' });
  if (job.state !== 'running') {
    const add = (action, label, title) =>
      actions.append(Button.pill(label, { title, onClick: () => onAct(job.id, action) }));
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

  const start = Button.primary('Start autopilot',
    { disabled: running, onClick: () => act('start') });
  const drain = Button('Start and drain',
    { disabled: running, onClick: () => act('start', { drain: true }),
      title: 'Exit when the queue empties instead of idling' });
  const stop = Button('Stop', { disabled: !running, onClick: () => act('stop'),
                                title: 'Finishes the job it is on, then exits' });

  const services = data.services;
  bar.append(
    el('div', {},
      el('div', { className: 'pilotstate' },
        el('span', { className: `dot ${running ? 'up' : ''}` }),
        el('b', { textContent: running ? 'Autopilot running' : 'Autopilot stopped' })),
      Mini(running ? `since ${started}`
        : services.ok ? 'services are up — the queue can be drained'
          : `services down: ${services.why}`)),
    Row(start, drain, stop));
  return bar;
}

function submitForm(refresh) {
  const box = el('details', { className: 'submitbox' });
  box.append(el('summary', { textContent: 'Queue a job' }));

  const config = Select(state.configs || [], { value: state.current });
  const priority = Num(50, { min: 0, max: 9999 });
  const matrix = el('textarea', { rows: 4, placeholder:
    '{"canonical.seed": [1, 2, 3], "pose.view": ["front", "side"]}' });

  const go = Button.primary('Submit');
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

  box.append(Fields(
    Row(Mini('Config'), config, Mini('Priority'), priority),
    matrix,
    Row(go)));
  return box;
}

export function renderQueue(host) {
  // The panel that shows the queue, kept as a node so a refresh replaces its
  // children rather than the whole view. Rebuilding the view would take the
  // header, the poll and the segmented control with it - and the segmented
  // control is what you just clicked.
  const body = el('div', {});
  host.replaceChildren(Head('Queue'), body);

  let data = null;

  const onAct = async (id, action) => {
    try {
      await api.queueJob(id, action);
      toast(`${action} ${id}`);
      await load();
    } catch (e) { toast(e.message, 'error'); }
  };

  function draw() {
    if (!data) return;
    const jobs = data.states[showing] || [];
    const list = el('div', { className: 'joblist' });
    if (!jobs.length) list.append(Empty(`Nothing ${showing}.`));
    else for (const job of jobs) list.append(jobRow(job, onAct));

    body.replaceChildren(
      autopilotBar(data, load),
      submitForm(load),
      Segmented(STATES.map((s) => [s.key, s.label, data.counts[s.key] || null]),
                { value: showing, onPick: (k) => { showing = k; draw(); } }),
      list,
      Mini(data.dir));
  }

  async function load() {
    try {
      data = await api.queue();
      draw();
    } catch (e) {
      body.replaceChildren(Empty(e.message));
    }
  }

  // Polling only while something is in motion. A queue at rest does not need a
  // request every four seconds, and this view stays open for hours. `poll`
  // skips a hidden tab and will not stack a tick on a slow one.
  const stopPolling = poll(async () => {
    await load();
    return data && !(data.autopilot.running || data.counts.running);
  }, { every: 4000 });

  // The teardown the lifecycle calls before the next view mounts. Without it
  // this interval outlived the tab, which is what the old setTimeout did.
  return stopPolling;
}
