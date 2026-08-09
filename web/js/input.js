/* Input tab — what to make this time.
 *
 * The prompt composer is the centre of gravity here, not a field in a list:
 * it is the thing you rewrite twenty times per character, so it gets room,
 * auto-grows, and keeps its secondary prompts one click away rather than
 * buried among sliders.
 */

import { api, getPath } from './api.js';
import { draftConfig, el, state, toast } from './store.js';
import { VIEW_OPTIONS } from './views.js';

/* Textareas that grow with their content — a two-line box for a paragraph of
 * prompt is the single most cramped thing in the old layout. */
function autoGrow(area, min = 90) {
  const fit = () => {
    area.style.height = 'auto';
    area.style.height = `${Math.max(min, area.scrollHeight + 2)}px`;
  };
  area.addEventListener('input', fit);
  requestAnimationFrame(fit);
  return area;
}

function promptBox({ label, path, placeholder, hint, rows = 3, onChange }) {
  const cfg = draftConfig();
  const area = autoGrow(el('textarea', {
    className: 'prompt', rows, placeholder, value: getPath(cfg, path) ?? '',
  }), rows * 26 + 20);
  area.onchange = () => onChange(path, area.value);

  const count = el('span', { className: 'promptcount' });
  const tick = () => {
    // CLIP takes 75 tokens per chunk; a rough word count is enough to warn
    // before a prompt starts diluting itself across chunks.
    const words = area.value.trim().split(/\s+/).filter(Boolean).length;
    count.textContent = words ? `${words} words` : '';
    count.classList.toggle('over', words > 60);
  };
  area.addEventListener('input', tick);
  tick();

  return el('div', { className: 'promptfield' },
    el('div', { className: 'promptlabel' },
      el('label', { textContent: label }), count),
    area,
    hint ? el('p', { className: 'help', textContent: hint }) : null);
}

/* ------------------------------------------------------------- creature */

function creaturePicker(onChange) {
  const cfg = draftConfig();
  const info = state.schema.options.rig_info || [];
  const current = getPath(cfg, 'rig') || 'humanoid';

  const sel = el('select', { className: 'select big' });
  for (const rig of info) {
    sel.append(el('option', {
      value: rig.name, textContent: `${rig.label}  ·  ${rig.joints} joints`,
      selected: rig.name === current,
    }));
  }
  sel.onchange = () => onChange('rig', sel.value);

  const meta = info.find((r) => r.name === current);
  const channel = meta?.skeleton_control;
  const line = channel === 'openpose'
    ? 'Uses OpenPose + depth — the only rig with a matching pose model.'
    : channel
      ? 'No OpenPose model exists for this body plan, so the skeleton is sent as a scribble alongside depth.'
      : 'Depth only. A stick figure would mislead here; add soft-body nodes for movement.';

  return el('div', { className: 'creaturebar' },
    el('div', {},
      el('label', { textContent: 'Creature' }),
      el('div', { className: 'path', textContent: 'rig' })),
    el('div', { className: 'creaturemain' },
      sel,
      el('p', { className: `channelnote ${channel ? '' : 'depthonly'}`, textContent: line }),
      meta?.note ? el('p', { className: 'help', textContent: meta.note }) : null));
}

/* ------------------------------------------------------------- browsing */

export function browseDialog(startPath = '', imagesOnly = false) {
  return new Promise((resolve) => {
    const listing = el('div', { className: 'browser' });
    const crumb = el('div', { className: 'crumb mono' });
    const chosen = new Set();
    let currentDir = startPath;

    const cancel = el('button', { className: 'btn ghost', textContent: 'Cancel' });
    const ok = el('button', { className: 'btn primary', textContent: imagesOnly ? 'Select' : 'Use folder' });

    const load = async (path) => {
      try {
        const data = await api.browse(path, imagesOnly);
        currentDir = data.dir;
        crumb.textContent = data.dir;
        listing.replaceChildren();

        if (data.parent) {
          const up = el('div', { className: 'browse-row dir' }, '⤴  ..');
          up.onclick = () => load(data.parent);
          listing.append(up);
        }
        for (const item of data.entries) {
          const row = el('div', { className: `browse-row ${item.is_dir ? 'dir' : ''}` },
            item.is_dir ? '📁  ' : '',
            item.is_image ? el('img', { className: 'minithumb', src: api.fileUrl(item.path) }) : null,
            el('span', { textContent: item.name }));
          if (item.is_dir) row.onclick = () => load(item.path);
          else if (imagesOnly) {
            row.onclick = () => {
              row.classList.toggle('sel');
              chosen.has(item.path) ? chosen.delete(item.path) : chosen.add(item.path);
            };
          }
          listing.append(row);
        }
      } catch (e) {
        listing.replaceChildren(el('p', { className: 'empty', textContent: e.message }));
      }
    };

    const modal = el('div', { className: 'modal' },
      el('div', { className: 'modal-card wide' },
        el('h2', { textContent: imagesOnly ? 'Select images' : 'Select a folder' }),
        crumb, listing,
        el('div', { className: 'modal-actions' }, cancel, ok)));

    const close = (value) => { modal.remove(); resolve(value); };
    cancel.onclick = () => close(null);
    ok.onclick = () => close(imagesOnly ? [...chosen] : currentDir);
    modal.onclick = (e) => { if (e.target === modal) close(null); };
    document.body.append(modal);
    load(startPath);
  });
}

/* ----------------------------------------------------------- references */

/* The four roles, with the weight range each actually wants. Identity has to
 * hold a character together; style only has to tint it, and at identity
 * strength it would replace the character with the exemplar. */
export const ROLES = [
  { key: 'identity', label: 'Identity', max: 1.5,
    blurb: 'Who the character is. Illustrations or art — not necessarily pixel art.' },
  { key: 'style', label: 'Style', max: 1.5,
    blurb: 'What the art should look like. Kept weak so it tints rather than replaces.' },
  { key: 'pose', label: 'Pose', max: 1.0,
    blurb: 'A composition to reproduce. Annotate it in the Run tab.' },
  { key: 'palette', label: 'Palette', max: 1.0,
    blurb: 'Colours to lock to. Imposed exactly, so frames cannot drift.' },
];

/* Move one image from one role's list to another, as a single edit.
 *
 * Re-tagging matters more than it looks. The mistake people actually make is
 * dropping eight images in at once and only then noticing that two of them
 * were style references, not identity — and delete-then-re-add loses the view
 * label and the weight that had already been set. */
function moveRole(fromKey, toKey, index, onChange) {
  const fromPath = `references.${fromKey}`;
  const toPath = `references.${toKey}`;
  const from = getPath(draftConfig(), fromPath) || [];
  const to = getPath(draftConfig(), toPath) || [];
  const moving = from[index];
  if (!moving) return;

  // Both arrays are derived from one snapshot before either write, so the
  // re-render the first write triggers cannot make the second one stale.
  const nextFrom = from.filter((_, i) => i !== index);
  const nextTo = [...to, { ...moving, weight: 1 }];
  onChange(fromPath, nextFrom);
  onChange(toPath, nextTo);
}

/* Which role new uploads join. Module-level so switching tabs, uploading, and
 * coming back to the view all agree on it. */
let activeRole = 'identity';

function referenceCards(role, onChange) {
  const path = `references.${role.key}`;
  const images = getPath(draftConfig(), path) || [];
  const grid = el('div', { className: 'refgrid' });

  images.forEach((ref, index) => {
    const roleSel = el('select', { className: 'select rolesel', title: 'What this image is for' });
    for (const other of ROLES) {
      roleSel.append(el('option', {
        value: other.key, textContent: other.label, selected: other.key === role.key,
      }));
    }
    roleSel.onchange = () => moveRole(role.key, roleSel.value, index, onChange);

    const viewSel = el('select', { className: 'select' });
    for (const name of VIEW_OPTIONS) {
      viewSel.append(el('option', { value: name, textContent: name, selected: ref.view === name }));
    }
    viewSel.onchange = () => {
      const next = structuredClone(images);
      next[index].view = viewSel.value;
      onChange(path, next);
    };

    const remove = el('button', { className: 'iconbtn', textContent: '✕', title: 'Remove' });
    remove.onclick = () => onChange(path, images.filter((_, i) => i !== index));

    // Per-image weight, ranged for the role rather than a generic 0-2.
    const weight = el('input', {
      type: 'range', min: 0, max: role.max, step: 0.05,
      value: ref.weight ?? 1.0, className: 'refweight',
    });
    const readout = el('span', { className: 'mono mini', textContent: (ref.weight ?? 1).toFixed(2) });
    weight.oninput = () => { readout.textContent = Number(weight.value).toFixed(2); };
    weight.onchange = () => {
      const next = structuredClone(images);
      next[index].weight = Number(weight.value);
      onChange(path, next);
    };

    grid.append(el('div', { className: 'refcard' },
      el('img', { src: api.fileUrl(ref.path), loading: 'lazy' }),
      el('div', { className: 'refcard-foot' }, roleSel, remove),
      role.key === 'palette' ? null : el('div', { className: 'refcard-foot' },
        el('span', { className: 'mini', textContent: 'shows' }), viewSel),
      el('div', { className: 'refcard-weight' },
        el('span', { className: 'mini', textContent: 'weight' }), weight, readout),
      el('div', { className: 'refcard-path mono', textContent: ref.path, title: ref.path })));
  });

  if (!images.length) {
    grid.append(el('p', { className: 'empty',
      textContent: `No ${role.label.toLowerCase()} references.` }));
  }
  return grid;
}

/** Drop target that also accepts pasted images.
 *
 * Dragging a file in, or pasting a screenshot, is how people actually move
 * images between apps; a file dialog behind a button is the slow path. */
function dropZone(onFiles) {
  const zone = el('div', { className: 'dropzone' },
    el('div', { className: 'dropicon', textContent: '⬓' }),
    el('p', {}, el('b', { textContent: 'Drop images here' }), ', paste, or '),
    el('p', { className: 'help', textContent:
      'Optional. Without a reference the canonical sprite is the only identity '
      + 'anchor — which is fine when you have no base image to start from.' }));

  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  for (const type of ['dragenter', 'dragover']) {
    zone.addEventListener(type, (e) => { stop(e); zone.classList.add('over'); });
  }
  for (const type of ['dragleave', 'drop']) {
    zone.addEventListener(type, (e) => { stop(e); zone.classList.remove('over'); });
  }
  zone.addEventListener('drop', (e) => {
    const files = [...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) onFiles(files);
  });

  // Paste is bound to the tab, not the zone, so it works wherever the cursor
  // is — matching how paste behaves in a chat composer.
  const onPaste = (e) => {
    if (!document.getElementById('view-input')?.classList.contains('active')) return;
    const files = [...(e.clipboardData?.items || [])]
      .filter((i) => i.type.startsWith('image/'))
      .map((i) => i.getAsFile())
      .filter(Boolean);
    if (files.length) { e.preventDefault(); onFiles(files); }
  };
  document.removeEventListener('paste', document._refPaste || (() => {}));
  document._refPaste = onPaste;
  document.addEventListener('paste', onPaste);

  return zone;
}

/* ------------------------------------------------------------------ view */

export function renderInput(host, { onChange, onContinue }) {
  const paths = state.system?.paths || {};
  host.replaceChildren();

  // Which role tab is open is view state, not config, so it does not go
  // through onChange — that would write a draft entry for a UI preference.
  const render = () => renderInput(host, { onChange, onContinue });

  /* --- prompt composer --- */
  const secondary = el('div', { className: 'secondary hidden' },
    promptBox({
      label: 'Style', path: 'style', rows: 2, onChange,
      placeholder: 'pixel art, game sprite, plain flat background',
      hint: 'Appended to the subject. A flat background makes background keying much cleaner.',
    }),
    promptBox({
      label: 'Action', path: 'pose.action', rows: 2, onChange,
      placeholder: 'a character swinging a sword downward, ending in follow-through',
      hint: 'Only read when poses are LLM-generated. Be explicit about the ending position.',
    }),
    promptBox({
      label: 'Negative', path: 'frames.negative', rows: 2, onChange,
      placeholder: 'blurry, soft, antialiased, photo, 3d render…',
      hint: 'Blank uses the built-in pixel-art negative.',
    }));

  const toggle = el('button', { className: 'linkbtn', textContent: '+ style, action, negative' });
  toggle.onclick = () => {
    secondary.classList.toggle('hidden');
    toggle.textContent = secondary.classList.contains('hidden')
      ? '+ style, action, negative' : '− hide extra prompts';
  };

  host.append(el('section', { className: 'composer' },
    promptBox({
      label: 'Subject', path: 'subject', rows: 3, onChange,
      placeholder: 'a knight in steel armor holding a sword',
      hint: 'Read by every stage that writes a prompt, so it lives in one place.',
    }),
    toggle, secondary,
    creaturePicker(onChange)));

  /* --- references --- */
  // Uploads land in whichever role is selected. The backend rejects the old
  // flat `references.images` outright, so writing it here would produce a
  // config that cannot run.
  const upload = el('input', { type: 'file', accept: 'image/*', multiple: true, style: 'display:none' });
  const addRefs = (items) => {
    const path = `references.${activeRole}`;
    const current = getPath(draftConfig(), path) || [];
    onChange(path,
      [...current, ...items.map((p) => ({ path: p, view: 'front', weight: 1 }))]);
  };
  upload.onchange = async () => {
    if (!upload.files.length) return;
    try {
      const { saved } = await api.upload(upload.files);
      addRefs(saved.map((f) => f.path));
      toast(`Uploaded ${saved.length} image(s)`);
    } catch (e) { toast(e.message, 'error'); }
    upload.value = '';
  };

  const sendFiles = async (files) => {
    try {
      const { saved } = await api.upload(files);
      addRefs(saved.map((f) => f.path));
      toast(`Added ${saved.length} image(s)`);
    } catch (e) { toast(e.message, 'error'); }
  };

  const uploadBtn = el('button', { className: 'btn', textContent: 'Browse files…' });
  uploadBtn.onclick = () => upload.click();
  const pickBtn = el('button', { className: 'btn ghost', textContent: 'From input folder…' });
  pickBtn.onclick = async () => {
    const picked = await browseDialog(paths.input_dir || '', true);
    if (picked?.length) addRefs(picked);
  };

  const counts = Object.fromEntries(ROLES.map((r) =>
    [r.key, (getPath(draftConfig(), `references.${r.key}`) || []).length]));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const role = ROLES.find((r) => r.key === activeRole) || ROLES[0];

  // Role tabs rather than four separate drop zones. One target that you aim
  // first is less to hit than four you must aim between, and every card can
  // be re-tagged afterwards anyway.
  const roleTabs = el('div', { className: 'segmented roletabs' });
  for (const r of ROLES) {
    const b = el('button', {
      className: `seg ${r.key === activeRole ? 'on' : ''}`,
      textContent: counts[r.key] ? `${r.label} ${counts[r.key]}` : r.label,
    });
    b.onclick = () => { activeRole = r.key; render(); };
    roleTabs.append(b);
  }

  host.append(el('section', { className: 'group' },
    el('h2', {}, 'Reference images',
      el('span', { className: 'headnote',
        textContent: total
          ? `${total} attached across ${
              ROLES.filter((r) => counts[r.key]).length} role(s)`
          : 'optional' })),
    el('div', { className: 'fields' },
      roleTabs,
      el('p', { className: 'help', textContent: role.blurb }),
      dropZone(sendFiles),
      el('div', { className: 'row' }, uploadBtn, pickBtn, upload),
      referenceCards(role, onChange))));

  /* --- folders --- */
  const dirRow = (label, value, help, path) => {
    const text = el('input', { type: 'text', value: value || '', className: 'wide' });
    text.onchange = () => onChange(path, text.value);
    const browse = el('button', { className: 'btn ghost', textContent: 'Browse…' });
    browse.onclick = async () => {
      const chosen = await browseDialog(value, false);
      if (chosen) { text.value = chosen; onChange(path, chosen); }
    };
    return el('div', { className: 'field' },
      el('div', { className: 'field-top stacked' },
        el('div', {}, el('label', { textContent: label }),
          el('div', { className: 'path', textContent: path })),
        el('div', { className: 'control-wrap' }, text, browse)),
      el('p', { className: 'help', textContent: help }));
  };

  host.append(el('section', { className: 'group' },
    el('h2', { textContent: 'Folders' }),
    el('div', { className: 'fields' },
      dirRow('Input', paths.input_dir, 'Where uploads land and the browser opens.', 'paths.input_dir'),
      dirRow('Output', paths.output_dir, 'Where runs are written.', 'paths.output_dir'),
      dirRow('Export', paths.download_dir, 'Default target when exporting results.', 'paths.download_dir'))));

  const cont = el('button', { className: 'btn primary lg', textContent: 'Continue to Run →' });
  cont.onclick = onContinue;
  host.append(el('div', { className: 'formfoot' }, cont));
}
