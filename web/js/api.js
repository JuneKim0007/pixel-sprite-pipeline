/* Every server call in one place, so no view builds a URL by hand. */

async function call(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.error || `${res.status} ${res.statusText}`);
    err.kind = body.kind;
    err.status = res.status;
    throw err;
  }
  return body;
}

const json = (method, path, body) =>
  call(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const api = {
  schema:   (module) => call(`/api/schema${module ? `?module=${encodeURIComponent(module)}` : ''}`),
  system:   () => call('/api/system'),
  configs:  () => call('/api/configs'),
  config:   (name) => call(`/api/config?name=${encodeURIComponent(name)}`),
  saveConfig: (name, payload) => json('PUT', `/api/config?name=${encodeURIComponent(name)}`, payload),
  global:   () => call('/api/global'),
  saveGlobal: (config) => json('PUT', '/api/global', { config }),

  styles:   () => call('/api/styles'),
  stylePreview: (config, withStyles = []) =>
    call(`/api/style/preview?config=${encodeURIComponent(config)}`
      + withStyles.map((s) => `&with=${encodeURIComponent(s)}`).join('')),
  styleDetail: (name) => call(`/api/style/detail?name=${encodeURIComponent(name)}`),
  styleNote:   (name, text) => json('POST', '/api/style/note', { name, text }),
  styleTraining: (name) => call(`/api/style/training?name=${encodeURIComponent(name)}`),

  palettes:    () => call('/api/palettes'),
  editPreview: (params) => json('POST', '/api/edit/preview', params),
  editApply:   (params) => json('POST', '/api/edit/apply', params),

  styleExemplar: (name, paths, remove = false) =>
    json('POST', '/api/style/exemplar', { name, paths, remove }),
  stylePrompts: (name, vocabulary, notes) =>
    json('POST', '/api/style/prompts', { name, vocabulary, notes }),

  queue:       () => call('/api/queue'),
  queueLog:    () => call('/api/queue/log'),
  queueSubmit: (spec, priority = 50) => json('POST', '/api/queue/submit', { spec, priority }),
  queueJob:    (id, action) => json('POST', '/api/queue/job', { id, action }),
  autopilot:   (payload) => json('POST', '/api/queue/autopilot', payload),

  runs:     () => call('/api/runs'),
  run:      (id) => call(`/api/run?id=${encodeURIComponent(id)}`),
  start:    (payload) => json('POST', '/api/run', payload),
  stop:     (run_id) => json('POST', '/api/stop', { run_id }),

  poses:    (runId = '') => call(`/api/poses${runId ? `?run=${encodeURIComponent(runId)}` : ''}`),
  savePoses: (run_id, entries) => json('POST', '/api/poses', { run_id, entries }),
  autorig:  (image, rig = 'humanoid') =>
    call(`/api/autorig?image=${encodeURIComponent(image)}&rig=${encodeURIComponent(rig)}`),
  rigPose:  (rig) => call(`/api/rigpose?rig=${encodeURIComponent(rig)}`),
  annotation: (image, rig = 'humanoid') =>
    call(`/api/annotation?image=${encodeURIComponent(image)}&rig=${encodeURIComponent(rig)}`),
  saveAnnotation: (image, rig, points) =>
    json('POST', '/api/annotation', { image, rig, points }),

  browse:   (path, imagesOnly = false) =>
    call(`/api/browse?path=${encodeURIComponent(path || '')}&images=${imagesOnly ? 1 : 0}`),
  fileUrl:  (path) => `/api/file?path=${encodeURIComponent(path)}`,

  upload(files) {
    const form = new FormData();
    for (const f of files) form.append('files', f, f.name);
    return call('/api/upload', { method: 'POST', body: form });
  },

  downloadPlan: (payload) => json('POST', '/api/download/plan', payload),
  download:     (payload) => json('POST', '/api/download', payload),
};

/* Dotted-path helpers, shared by the settings form and the wizard. */

export const getPath = (obj, path) =>
  path.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);

export function setPath(obj, path, value) {
  const parts = path.split('.');
  let node = obj;
  for (const key of parts.slice(0, -1)) {
    if (typeof node[key] !== 'object' || node[key] === null) node[key] = {};
    node = node[key];
  }
  node[parts.at(-1)] = value;
}

export function delPath(obj, path) {
  const parts = path.split('.');
  let node = obj;
  for (const key of parts.slice(0, -1)) {
    if (typeof node[key] !== 'object' || node[key] === null) return false;
    node = node[key];
  }
  return delete node[parts.at(-1)];
}
