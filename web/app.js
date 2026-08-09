/* Sprite Pipeline UI.
 *
 * Forms are generated from /api/schema so a setting is defined in exactly one
 * place (pipeline/schema.py). Adding a knob there makes it appear here with
 * the right control, range and help text — the UI never hardcodes a field.
 */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const SETTINGS_GROUPS = new Set(['Models', 'Compute', 'Services']);
const STAGE_LABEL = {
  pose: 'Skeletons', depth: 'Depth maps', canonical: 'Reference sprite',
  frames: 'Generated frames', palette: 'Pixelized', export: 'Sprite sheet',
};

const state = {
  schema: null, system: null, configs: [],
  current: null, config: {}, raw: '',
  runs: [], selectedRun: null, activeRun: null,
  stageFilter: 'all', outcomeRun: 'all', poll: null,
};

/* ------------------------------------------------------------------ api */

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `${r.status} ${r.statusText}`);
  return body;
}

const getPath = (obj, path) =>
  path.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);

function setPath(obj, path, value) {
  const parts = path.split('.');
  let node = obj;
  for (const p of parts.slice(0, -1)) {
    if (typeof node[p] !== 'object' || node[p] === null) node[p] = {};
    node = node[p];
  }
  node[parts.at(-1)] = value;
}

/* ------------------------------------------------------------ rendering */

function optionsFor(field) {
  if (field.options) return field.options;
  if (field.options_from) return state.schema.options[field.options_from] || [];
  return [];
}

function visible(field) {
  if (!field.when) return true;
  return Object.entries(field.when)
    .every(([p, v]) => (getPath(state.config, p) ?? 'library') === v);
}

function control(field) {
  const val = getPath(state.config, field.path);
  const wrap = document.createElement('div');
  wrap.className = 'control';

  const commit = (v) => { setPath(state.config, field.path, v); refreshConditionals(); };

  if (field.type === 'bool') {
    const cb = Object.assign(document.createElement('input'),
      { type: 'checkbox', checked: !!val });
    cb.onchange = () => commit(cb.checked);
    wrap.append(cb);
    return wrap;
  }

  if (field.type === 'select') {
    const sel = document.createElement('select');
    sel.className = 'select';
    const opts = optionsFor(field);
    const blank = new Option('(default)', '');
    sel.append(blank);
    for (const o of opts) sel.append(new Option(o, o));
    if (field.free_numeric && val != null && !opts.includes(val)) {
      sel.append(new Option(String(val), String(val)));
    }
    sel.value = val == null ? '' : String(val);
    sel.onchange = () => commit(sel.value === '' ? null : sel.value);
    wrap.append(sel);

    if (field.free_numeric) {
      const num = Object.assign(document.createElement('input'),
        { type: 'number', className: 'num', placeholder: 'deg', step: 5 });
      num.onchange = () => { if (num.value !== '') commit(Number(num.value)); render(); };
      wrap.append(num);
    }
    return wrap;
  }

  if (field.type === 'int' || field.type === 'float') {
    const isFloat = field.type === 'float';
    const step = field.step ?? (isFloat ? 0.05 : 1);
    const hasRange = field.min != null && field.max != null && (field.max - field.min) <= 5000;

    const num = Object.assign(document.createElement('input'), {
      type: 'number', className: 'num', step,
      value: val ?? '', placeholder: 'auto',
    });
    if (field.min != null) num.min = field.min;
    if (field.max != null) num.max = field.max;

    if (hasRange) {
      const rng = Object.assign(document.createElement('input'), {
        type: 'range', min: field.min, max: field.max, step,
        value: val ?? field.min,
      });
      rng.oninput = () => { num.value = rng.value; commit(Number(rng.value)); };
      wrap.append(rng);
      num.oninput = () => { rng.value = num.value; };
    }
    num.onchange = () =>
      commit(num.value === '' ? null : (isFloat ? parseFloat(num.value) : parseInt(num.value, 10)));
    wrap.append(num);
    return wrap;
  }

  if (field.type === 'textarea') {
    const ta = document.createElement('textarea');
    ta.rows = 2; ta.style.width = '360px'; ta.value = val ?? '';
    ta.onchange = () => commit(ta.value);
    wrap.append(ta);
    return wrap;
  }

  if (field.type === 'stages') {
    wrap.style.flex = '1';
    wrap.append(stagePicker(val || []));
    return wrap;
  }

  const inp = Object.assign(document.createElement('input'),
    { type: 'text', value: val ?? '' });
  inp.style.width = '280px';
  inp.onchange = () => commit(inp.value);
  wrap.append(inp);
  return wrap;
}

/* Mirror of the server's dependency check, run on every edit so an
 * unrunnable order is visible immediately rather than at save or run time. */
function orderProblems(active) {
  const meta = Object.fromEntries(state.schema.stages.map((s) => [s.name, s]));
  const producers = {};
  for (const name of active) for (const p of meta[name]?.produces || []) producers[p] = name;

  const have = new Set();
  const problems = [];
  for (const name of active) {
    for (const need of meta[name]?.requires || []) {
      if (!have.has(need)) {
        const owner = producers[need];
        problems.push(owner
          ? `${name} needs "${need}" from ${owner}, which runs later`
          : `${name} needs "${need}", which no enabled stage produces`);
      }
    }
    for (const p of meta[name]?.produces || []) have.add(p);
  }
  return problems;
}

/* Topological sort by the same contracts, used by the Auto-order button. */
function autoOrder(active) {
  const meta = Object.fromEntries(state.schema.stages.map((s) => [s.name, s]));
  const producers = {};
  for (const name of active) for (const p of meta[name]?.produces || []) producers[p] = name;

  const out = [], placed = new Set();
  let guard = active.length + 1;
  while (out.length < active.length && guard-- > 0) {
    for (const name of active) {
      if (placed.has(name)) continue;
      const deps = (meta[name]?.requires || [])
        .map((r) => producers[r]).filter((d) => d && d !== name);
      if (deps.every((d) => placed.has(d))) { out.push(name); placed.add(name); }
    }
  }
  // Anything left is in a cycle; append so nothing silently disappears.
  return [...out, ...active.filter((s) => !placed.has(s))];
}

function stagePicker(active) {
  const box = document.createElement('div');
  box.className = 'stagepicker';
  const all = state.schema.stages.map((s) => s.name);
  const ordered = [...active, ...all.filter((s) => !active.includes(s))];

  ordered.forEach((name) => {
    const meta = state.schema.stages.find((s) => s.name === name);
    const on = active.includes(name);
    const el = document.createElement('div');
    el.className = 'st' + (on ? '' : ' off');
    el.draggable = on;
    el.dataset.name = name;
    el.title = meta
      ? `${meta.resource.toUpperCase()} · needs: ${meta.requires.join(', ') || '-'} · gives: ${meta.produces.join(', ') || '-'}`
      : '';
    el.innerHTML = `<span class="num">${on ? active.indexOf(name) + 1 : '–'}</span>${name}`;
    el.onclick = () => {
      const next = on ? active.filter((s) => s !== name) : [...active, name];
      setPath(state.config, 'pipeline.stages', next);
      render();
    };
    el.ondragstart = (e) => e.dataTransfer.setData('text/plain', name);
    el.ondragover = (e) => e.preventDefault();
    el.ondrop = (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData('text/plain');
      if (from === name) return;
      const next = active.filter((s) => s !== from);
      const at = next.indexOf(name);
      next.splice(at < 0 ? next.length : at, 0, from);
      setPath(state.config, 'pipeline.stages', next);
      render();
    };
    box.append(el);
  });

  const wrap = document.createElement('div');
  wrap.style.flex = '1';
  wrap.append(box);

  const problems = orderProblems(active);
  if (problems.length) {
    const warn = document.createElement('div');
    warn.className = 'orderwarn';
    warn.innerHTML =
      `<b>This order cannot run.</b><ul>${problems.map((p) => `<li>${p}</li>`).join('')}</ul>`;
    const fix = document.createElement('button');
    fix.className = 'btn';
    fix.textContent = 'Auto-order';
    fix.onclick = () => {
      setPath(state.config, 'pipeline.stages', autoOrder(active));
      render();
    };
    warn.append(fix);
    wrap.append(warn);
  }
  return wrap;
}

function buildForm(container, groups) {
  container.innerHTML = '';
  const byGroup = new Map();
  for (const f of state.schema.fields) {
    if (!groups.has(f.group)) continue;
    if (!byGroup.has(f.group)) byGroup.set(f.group, []);
    byGroup.get(f.group).push(f);
  }

  for (const [group, fields] of byGroup) {
    const sec = document.createElement('section');
    sec.className = 'group';
    sec.innerHTML = `<h2>${group}</h2>`;
    const body = document.createElement('div');
    body.className = 'fields';

    for (const f of fields) {
      const row = document.createElement('div');
      row.className = 'field';
      row.dataset.path = f.path;
      if (!visible(f)) row.classList.add('hidden');

      const top = document.createElement('div');
      top.className = 'field-top';
      const left = document.createElement('div');
      left.innerHTML = `<label>${f.label}</label><div class="path">${f.path}</div>`;
      top.append(left, control(f));
      row.append(top);

      if (f.help) {
        const p = document.createElement('p');
        p.className = 'help';
        p.textContent = f.help;
        row.append(p);
      }
      body.append(row);
    }
    sec.append(body);
    container.append(sec);
  }
}

function refreshConditionals() {
  for (const f of state.schema.fields) {
    for (const row of $$(`.field[data-path="${CSS.escape(f.path)}"]`)) {
      row.classList.toggle('hidden', !visible(f));
    }
  }
}

function render() {
  buildForm($('#form'), new Set(
    state.schema.fields.map((f) => f.group).filter((g) => !SETTINGS_GROUPS.has(g))));
  buildForm($('#settingsForm'), SETTINGS_GROUPS);
  $('#rawEditor').value = state.raw;
  renderStageFlow();
}

function renderStageFlow() {
  const active = getPath(state.config, 'pipeline.stages') || [];
  $('#stageflow').innerHTML = active.map((name) => {
    const m = state.schema.stages.find((s) => s.name === name);
    const res = m ? m.resource : '?';
    return `<li class="${res}">${name}<span class="res">${res}</span></li>`;
  }).join('');
}

/* --------------------------------------------------------------- config */

async function loadConfigs(select = null) {
  const { configs } = await api('/api/configs');
  state.configs = configs;
  for (const el of [$('#configSelect'), $('#settingsConfig')]) {
    el.innerHTML = configs.map((c) => `<option value="${c}">${c}</option>`).join('');
  }
  const pick = select || configs[0];
  if (pick) {
    $('#configSelect').value = pick;
    $('#settingsConfig').value = pick;
    await loadConfig(pick);
  }
}

async function loadConfig(name) {
  const data = await api(`/api/config?name=${encodeURIComponent(name)}`);
  state.current = name;
  state.config = data.config || {};
  state.raw = data.raw || '';
  render();
}

async function saveConfig() {
  const usingRaw = !$('#rawWrap').classList.contains('hidden');
  const payload = usingRaw
    ? { raw: $('#rawEditor').value }
    : { config: state.config };
  await api(`/api/config?name=${encodeURIComponent(state.current)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload) });
  await loadConfig(state.current);
  banner(`Saved ${state.current}.yaml`);
}

function banner(msg, isError = false) {
  const el = $('#runBanner');
  el.textContent = msg;
  el.className = 'banner' + (isError ? ' err' : '');
  setTimeout(() => el.classList.add('hidden'), 6000);
}

/* ----------------------------------------------------------------- runs */

async function startRun() {
  try {
    await saveConfig();
    const { run_id } = await api('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: state.current }),
    });
    state.activeRun = run_id;
    state.selectedRun = run_id;
    banner(`Started ${run_id} — see Runs for live progress.`);
    switchView('runs');
    refreshRuns();
  } catch (e) {
    banner(e.message, true);
  }
}

async function refreshRuns() {
  try {
    const { runs } = await api('/api/runs');
    state.runs = runs;
    if (!state.selectedRun && runs.length) state.selectedRun = runs[0].id;
    renderRunList();
    renderOutcomeFilter();
    if (state.selectedRun) await renderRunDetail(state.selectedRun);
  } catch { /* server restarting; next tick will retry */ }
}

function renderRunList() {
  $('#runList').innerHTML = state.runs.map((r) => `
    <div class="runitem ${r.id === state.selectedRun ? 'sel' : ''}" data-id="${r.id}">
      <div class="rid">${r.id}</div>
      <div class="meta">
        <span>${r.stages.length} stages</span>
        ${r.running ? '<span class="live">● live</span>' : ''}
      </div>
    </div>`).join('') || '<p class="empty">No runs yet.</p>';

  $$('#runList .runitem').forEach((el) => {
    el.onclick = () => { state.selectedRun = el.dataset.id; refreshRuns(); };
  });
}

async function renderRunDetail(id) {
  let d;
  try { d = await api(`/api/run?id=${encodeURIComponent(id)}`); }
  catch { return; }

  const stages = d.stages.map((s) => `
    <div class="stageblock">
      <h3>${STAGE_LABEL[s.name] || s.name}
        <span class="count">${s.dir} · ${s.images.length} file(s)</span></h3>
      <div class="thumbs">
        ${s.images.map((img) => {
          const p = `out/runs/${id}/${s.dir}/${img}`;
          return `<div class="thumb" data-src="/api/file?path=${encodeURIComponent(p)}"
                       data-cap="${img}">
                    <img loading="lazy" src="/api/file?path=${encodeURIComponent(p)}">
                    <div class="cap">${img}</div>
                  </div>`;
        }).join('')}
      </div>
    </div>`).join('');

  $('#runDetail').innerHTML = `
    ${d.running ? '<div class="banner">Running…</div>' : ''}
    ${stages || '<p class="empty">No stage output yet.</p>'}
    <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-dim)">Log</h3>
    <pre class="log">${escapeHtml(d.log || '(empty)')}</pre>`;

  const log = $('#runDetail .log');
  if (log && d.running) log.scrollTop = log.scrollHeight;
  wireThumbs();
}

const escapeHtml = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/* ------------------------------------------------------------- outcomes */

function renderOutcomeFilter() {
  const names = ['all', ...new Set(state.runs.flatMap((r) => r.stages.map((s) => s.name)))];
  $('#stageFilter').innerHTML = names.map((n) =>
    `<div class="chip ${state.stageFilter === n ? 'on' : ''}" data-stage="${n}">
       ${n === 'all' ? 'All stages' : (STAGE_LABEL[n] || n)}</div>`).join('');
  $$('#stageFilter .chip').forEach((c) => {
    c.onclick = () => { state.stageFilter = c.dataset.stage; renderOutcomeFilter(); renderGallery(); };
  });

  const sel = $('#outcomeRun');
  const prev = state.outcomeRun;
  sel.innerHTML = ['all', ...state.runs.map((r) => r.id)]
    .map((r) => `<option value="${r}">${r === 'all' ? 'All runs' : r}</option>`).join('');
  sel.value = prev;
  sel.onchange = () => { state.outcomeRun = sel.value; renderGallery(); };
  renderGallery();
}

function renderGallery() {
  const cards = [];
  for (const run of state.runs) {
    if (state.outcomeRun !== 'all' && run.id !== state.outcomeRun) continue;
    for (const st of run.stages) {
      if (state.stageFilter !== 'all' && st.name !== state.stageFilter) continue;
      for (const img of st.images) {
        const p = `out/runs/${run.id}/${st.dir}/${img}`;
        const src = `/api/file?path=${encodeURIComponent(p)}`;
        cards.push(`<div class="card">
            <img loading="lazy" src="${src}" data-src="${src}" data-cap="${run.id} / ${img}">
            <div class="cap"><b>${st.name}</b> · ${img}<br>${run.id}</div>
          </div>`);
      }
    }
  }
  $('#gallery').innerHTML = cards.join('') ||
    '<p class="empty">Nothing matches. Run a pipeline, or widen the filter.</p>';
  wireThumbs();
}

/* --------------------------------------------------------------- about */

function renderAbout() {
  const s = state.system;
  if (!s) return;
  const rows = s.weights.map((w) => `
    <tr><td class="mono">${w.name}</td><td>${w.group}</td>
        <td>${w.purpose}</td><td class="mono">${w.size}</td></tr>`).join('');

  $('#aboutBody').innerHTML = `
    <div class="note"><b>On GPU control:</b> ${s.compute_note}</div>
    <section class="group">
      <h2>Host</h2>
      <div class="fields"><table class="tbl">
        <tr><th>Platform</th><td class="mono">${s.host.platform}</td>
            <th>CPU cores</th><td class="mono">${s.host.cpu_count}</td></tr>
        <tr><th>Python</th><td class="mono">${s.host.python}</td>
            <th>Disk free</th><td class="mono">${s.host.disk_free}</td></tr>
        <tr><th>Weights on disk</th><td class="mono">${s.host.models_size}</td>
            <th></th><td></td></tr>
      </table></div>
    </section>
    <section class="group" style="margin-top:16px">
      <h2>Open weights installed</h2>
      <div class="fields"><table class="tbl">
        <tr><th>File</th><th>Kind</th><th>Purpose</th><th>Size</th></tr>
        ${rows || '<tr><td colspan="4">None found.</td></tr>'}
      </table></div>
    </section>`;
}

function renderServices() {
  const s = state.system?.services || {};
  const row = (name, info) => `
    <div class="svc"><span class="dot ${info?.up ? 'up' : ''}"></span>${name}
      ${info?.models?.length ? `<span style="opacity:.6">(${info.models.length})</span>` : ''}</div>`;
  $('#services').innerHTML = row('ComfyUI', s.comfyui) + row('Ollama', s.ollama);
}

/* -------------------------------------------------------------- viewer */

function wireThumbs() {
  $$('[data-src]').forEach((el) => {
    el.onclick = () => lightbox(el.dataset.src, el.dataset.cap || '');
  });
}

function lightbox(src, cap) {
  const box = document.createElement('div');
  box.className = 'lightbox';
  box.innerHTML = `<div><img src="${src}"><div class="cap">${cap}</div></div>`;
  box.onclick = () => box.remove();
  document.body.append(box);
}

/* ----------------------------------------------------------------- nav */

function switchView(name) {
  $$('#nav li').forEach((li) => li.classList.toggle('active', li.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  if (name === 'runs' || name === 'outcomes') refreshRuns();
  if (name === 'about') renderAbout();
}

/* ---------------------------------------------------------------- boot */

async function boot() {
  state.schema = await api('/api/schema');
  state.system = await api('/api/system').catch(() => null);
  renderServices();
  await loadConfigs();

  $$('#nav li').forEach((li) => { li.onclick = () => switchView(li.dataset.view); });
  $('#configSelect').onchange = (e) => loadConfig(e.target.value);
  $('#settingsConfig').onchange = (e) => { $('#configSelect').value = e.target.value; loadConfig(e.target.value); };
  $('#btnReload').onclick = () => loadConfig(state.current);
  $('#btnSave').onclick = () => saveConfig().catch((e) => banner(e.message, true));
  $('#btnSaveSettings').onclick = () => saveConfig().catch((e) => banner(e.message, true));
  $('#btnRun').onclick = startRun;
  $('#btnRefreshRuns').onclick = refreshRuns;
  $('#btnRaw').onclick = () => {
    const w = $('#rawWrap');
    w.classList.toggle('hidden');
    $('#form').classList.toggle('hidden', !w.classList.contains('hidden'));
  };

  // Poll while anything is live. Cheap: the payload is a directory listing.
  state.poll = setInterval(() => {
    if (!$('#autoRefresh').checked) return;
    const live = state.runs.some((r) => r.running);
    const onRuns = $('#view-runs').classList.contains('active');
    if (live || onRuns) refreshRuns();
  }, 4000);

  refreshRuns();
}

boot().catch((e) => {
  document.body.innerHTML =
    `<pre style="padding:24px;font-family:var(--mono)">Failed to start UI: ${e.message}</pre>`;
});
