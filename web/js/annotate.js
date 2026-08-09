/* Annotate a reference image: mark where the parts are in THIS picture.
 *
 * Deliberately not the two-canvas pose editor. That one authors a body you
 * intend to generate, in 3D body space, projectable to any angle. This one
 * marks up an image that already exists — one canvas, because a photograph
 * offers no depth to set; partial, because a cropped thigh has no position;
 * and with no bone-length rules, because foreshortening genuinely shortens a
 * limb on screen.
 *
 * Click-to-place rather than drag-to-correct: a seated, cropped reference has
 * nothing in common with a standing skeleton, so starting from one and
 * dragging every joint into place is slower than placing the five that matter.
 */

import { api } from './api.js';
import { drawFaceGuide, drawFaceLegend } from './faceguide.js';
import { el, toast } from './store.js';

const DOT = 7;

/* Ordered so the most useful landmarks come first — a handful of these is
 * usually enough to describe a composition. */
const PRIORITY = [
  'neck', 'nose', 'l_shoulder', 'r_shoulder', 'l_elbow', 'r_elbow',
  'l_wrist', 'r_wrist', 'l_hip', 'r_hip', 'l_knee', 'r_knee',
  'l_ankle', 'r_ankle', 'l_eye', 'r_eye', 'l_ear', 'r_ear',
];

function orderJoints(joints) {
  const known = PRIORITY.filter((j) => joints.includes(j));
  return [...known, ...joints.filter((j) => !known.includes(j))];
}

export function annotator({ imagePath, rigName = 'humanoid', onSaved } = {}) {
  const root = el('div', { className: 'annot' });
  const canvas = el('canvas', { width: 640, height: 640, className: 'annotcanvas' });
  const img = new Image();

  let rigDef = null;
  let points = {};
  let next = null;
  let dirty = false;
  let showGuide = true;

  const jointSel = el('select', { className: 'select' });
  const placedList = el('div', { className: 'placedlist' });
  const derived = el('div', { className: 'derived' });
  const saveBtn = el('button', { className: 'btn primary', textContent: 'Save annotation', disabled: true });
  const counter = el('span', { className: 'mini' });

  /* ------------------------------------------------------------- drawing */

  function fit() {
    if (!img.naturalWidth) return { x: 0, y: 0, w: canvas.width, h: canvas.height };
    const scale = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    return { x: (canvas.width - w) / 2, y: (canvas.height - h) / 2, w, h };
  }

  function draw() {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#111318';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const box = fit();
    if (img.complete && img.naturalWidth) ctx.drawImage(img, box.x, box.y, box.w, box.h);

    const at = (p) => [box.x + p[0] * box.w, box.y + p[1] * box.h];

    if (rigDef) {
      ctx.lineCap = 'round';
      rigDef.bones.forEach(([a, b], i) => {
        if (!points[a] || !points[b]) return;
        const [ax, ay] = at(points[a]);
        const [bx, by] = at(points[b]);
        const c = rigDef.colors?.[i % (rigDef.colors?.length || 1)] || [120, 200, 255];
        ctx.strokeStyle = `rgba(${c[0]},${c[1]},${c[2]},.85)`;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      });
    }

    // Decorative only, and drawn under the joints: a head is a volume and the
    // annotation is a few dots, so the construction is what makes eye and ear
    // placement judgeable at all.
    if (showGuide && rigDef?.face_joints?.length) {
      drawFaceGuide(ctx, points, at);
    }

    for (const [joint, p] of Object.entries(points)) {
      const [x, y] = at(p);
      ctx.fillStyle = joint === next ? '#fff' : '#7c8cff';
      ctx.beginPath();
      ctx.arc(x, y, DOT, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.65)';
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    counter.textContent = rigDef
      ? `${Object.keys(points).length} placed · ${rigDef.joints.length - Object.keys(points).length} skipped`
      : '';
  }

  /* --------------------------------------------------------- interaction */

  canvas.onclick = (e) => {
    if (!next) return;
    const r = canvas.getBoundingClientRect();
    const box = fit();
    const x = ((e.clientX - r.left) * (canvas.width / r.width) - box.x) / box.w;
    const y = ((e.clientY - r.top) * (canvas.height / r.height) - box.y) / box.h;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;

    points[next] = [Number(x.toFixed(4)), Number(y.toFixed(4))];
    dirty = true;
    saveBtn.disabled = false;
    advance();
    render();
  };

  /** Step to the next unplaced joint, so a run of clicks needs no menu trips. */
  function advance() {
    if (!rigDef) return;
    const ordered = orderJoints(rigDef.joints);
    const from = ordered.indexOf(next);
    const following = ordered.slice(from + 1).find((j) => !points[j]);
    next = following || ordered.find((j) => !points[j]) || null;
    jointSel.value = next || '';
  }

  function render() {
    placedList.replaceChildren();
    for (const joint of Object.keys(points)) {
      const chip = el('span', { className: 'placedchip' },
        joint,
        el('button', { className: 'x', textContent: '✕', title: 'Remove' }));
      chip.querySelector('button').onclick = () => {
        delete points[joint];
        dirty = true;
        saveBtn.disabled = false;
        render();
      };
      placedList.append(chip);
    }
    if (!Object.keys(points).length) {
      placedList.append(el('span', { className: 'mini',
        textContent: 'Nothing placed yet. Five or six landmarks is usually plenty.' }));
    }
    draw();
  }

  function showDerived(data) {
    derived.replaceChildren();
    if (!data || !data.placed) return;
    const rows = [
      ['View', `${data.inferred_view}°  (confidence ${Math.round((data.view_confidence || 0) * 100)}%)`],
      ['Proportions', Object.entries(data.proportions || {})
        .map(([k, v]) => `${k} ×${v}`).join(', ') || 'matches the default build'],
      ['Framing', data.crop?.framing || 'full body'],
    ];
    for (const [label, value] of rows) {
      derived.append(el('div', { className: 'derivedrow' },
        el('span', { className: 'mini', textContent: label }),
        el('span', { textContent: value })));
    }
    if (data.crop?.absent?.length) {
      derived.append(el('p', { className: 'help', textContent:
        `Out of frame: ${data.crop.absent.join(', ')} — the generator is told the `
        + 'subject is cropped rather than assuming a full-body composition.' }));
    }
  }

  /* ---------------------------------------------------------------- load */

  (async () => {
    try {
      rigDef = await api.rigPose(rigName);
      rigDef.colors = rigDef.colors || null;

      jointSel.replaceChildren(
        ...orderJoints(rigDef.joints).map((j) =>
          el('option', { value: j, textContent: j })));
      jointSel.onchange = () => { next = jointSel.value; draw(); };

      const existing = await api.annotation(imagePath, rigName);
      points = existing.points || {};
      if (existing.exists) showDerived(existing);

      next = orderJoints(rigDef.joints).find((j) => !points[j]) || null;
      jointSel.value = next || '';

      img.onload = render;
      img.src = api.fileUrl(imagePath);
      render();
    } catch (e) {
      root.replaceChildren(el('p', { className: 'empty', textContent: e.message }));
    }
  })();

  saveBtn.onclick = async () => {
    try {
      const data = await api.saveAnnotation(imagePath, rigName, points);
      dirty = false;
      saveBtn.disabled = true;
      showDerived(data);
      toast(`Annotation saved — ${data.placed} point(s)`);
      onSaved?.(data);
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const guideBox = el('input', { type: 'checkbox', checked: showGuide });
  guideBox.onchange = () => { showGuide = guideBox.checked; draw(); };
  const guideToggle = el('label', { className: 'chk' }, guideBox, ' Face guide');

  const legend = el('canvas', { width: 150, height: 190, className: 'facelegend' });

  // A proposal, never a commitment: it lands in the editor for review, because
  // a wrong fit should cost a glance rather than a GPU run.
  const auto = el('button', { className: 'btn', textContent: 'Auto-fit' });
  auto.onclick = async () => {
    auto.disabled = true;
    auto.textContent = 'Fitting…';
    try {
      const fit = await api.autorig(imagePath, rigName);
      if (!Object.keys(fit.points || {}).length) {
        toast(fit.notes?.[0] || 'nothing to fit', 'warn');
      } else {
        points = { ...fit.points, ...points };   // never overwrite your own work
        dirty = true;
        saveBtn.disabled = false;
        const pct = Math.round((fit.confidence || 0) * 100);
        toast(`Proposed ${Object.keys(fit.points).length} joints (${pct}% confidence) — check them`);
        for (const note of fit.notes || []) toast(note, 'warn');
        advance();
        render();
      }
    } catch (e) {
      toast(e.message, 'error');
    }
    auto.disabled = false;
    auto.textContent = 'Auto-fit';
  };

  const clear = el('button', { className: 'btn ghost', textContent: 'Clear' });
  clear.onclick = () => {
    points = {};
    dirty = true;
    saveBtn.disabled = false;
    next = orderJoints(rigDef?.joints || []).find(() => true) || null;
    jointSel.value = next || '';
    render();
  };

  root.append(
    el('div', { className: 'annotbar' },
      el('span', { className: 'mini', textContent: 'Place' }), jointSel,
      counter,
      guideToggle,
      el('span', { className: 'sep' }), auto, clear, saveBtn),
    el('p', { className: 'help', textContent:
      'Click where each part is in this image. Skip anything cropped or hidden — '
      + 'absent is a real answer, and a partial skeleton tells the model what is '
      + 'known while leaving the rest to it.' }),
    el('div', { className: 'annotmain' },
      canvas,
      el('div', { className: 'annotside' },
        el('h3', { textContent: 'Placed' }), placedList,
        el('h3', { textContent: 'What this implies' }), derived,
        el('h3', { textContent: 'Head construction' }), legend)));

  drawFaceLegend(legend);

  return root;
}
