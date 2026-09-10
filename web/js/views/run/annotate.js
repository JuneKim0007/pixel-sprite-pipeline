/* Annotate a reference image: mark where the parts are in THIS picture.
 *
 * Deliberately not the two-canvas pose editor. That one authors a body you
 * intend to generate, in 3D body space, projectable to any angle. This one
 * marks up an image that already exists — one canvas, because a photograph
 * offers no depth to set; partial, because a cropped thigh has no position;
 * and with no bone-length rules, because foreshortening genuinely shortens a
 * limb on screen.
 *
 * Click-to-place, AND drag-to-correct. This used to argue for the first alone,
 * on the grounds that dragging a standing skeleton onto a seated figure is
 * slower than placing the five joints that matter. Half of that still holds and
 * the sparse path is untouched — but Auto-fit already seeds all eighteen from
 * the image, and without dragging there was no way to nudge one of them. So a
 * dot is grabbable wherever it came from, and the joint list addresses every
 * joint rather than only the placed ones: removing a point used to leave the
 * click target pointing somewhere else with nothing on screen saying so.
 */

import { api } from '../../api.js';
import { autosaver, saveLabel } from '../../core/autosave.js';
import { drawFaceGuide, drawFaceLegend } from './faceguide.js';
import { projectPoint } from '../../features/pose.js';
import { el } from '../../core/dom.js';
import { toast } from '../../store.js';
import { Disclosure } from '../../ui/index.js';
import { EDGE, weightPainter } from './weights.js';

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
  let showGuide = true;
  let drag = null;      // the joint under the pointer while the button is down
  let hover = null;     // the joint the pointer is over, named on the canvas

  const jointSel = el('select', { className: 'select' });
  const placedList = el('div', { className: 'placedlist' });
  const derived = el('div', { className: 'derived' });
  const saveState = el('span', { className: 'mini savestate' });
  const saver = autosaver(async () => {
    const data = await api.saveAnnotation(imagePath, rigName, points);
    showDerived(data);
    onSaved?.(data);
  }, {
    onState: (phase, detail) => {
      saveState.classList.toggle('bad', phase === 'error');
      saveState.textContent = saveLabel(phase, detail);
    },
  });
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
      const lit = joint === drag || joint === hover;
      ctx.fillStyle = joint === next ? '#fff' : lit ? '#b9c4ff' : '#7c8cff';
      ctx.beginPath();
      ctx.arc(x, y, lit ? DOT + 2 : DOT, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.65)';
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // A dot with no name is what made this editor unreadable: eighteen
    // identical circles, and no way to tell which one you were about to move.
    const named = drag || hover;
    if (named && points[named]) {
      const [x, y] = at(points[named]);
      ctx.font = '600 13px ui-monospace, monospace';
      const w = ctx.measureText(named).width + 12;
      ctx.fillStyle = 'rgba(12,14,20,.88)';
      ctx.fillRect(x + 12, y - 24, w, 20);
      ctx.fillStyle = '#e8ecff';
      ctx.fillText(named, x + 18, y - 10);
    }

    counter.textContent = rigDef
      ? `${Object.keys(points).length} placed · ${rigDef.joints.length - Object.keys(points).length} skipped`
      : '';
  }

  /* --------------------------------------------------------- interaction */

  /** Where a pointer event lands, in image space. Null outside the picture. */
  function pointerAt(e) {
    const r = canvas.getBoundingClientRect();
    const box = fit();
    const x = ((e.clientX - r.left) * (canvas.width / r.width) - box.x) / box.w;
    const y = ((e.clientY - r.top) * (canvas.height / r.height) - box.y) / box.h;
    return x < 0 || x > 1 || y < 0 || y > 1 ? null : [x, y];
  }

  const GRAB = 0.035;   // image-space radius, so the target scales with the image

  function nearest(pos) {
    let best = null, bestD = GRAB;
    for (const [joint, q] of Object.entries(points)) {
      const d = Math.hypot(q[0] - pos[0], q[1] - pos[1]);
      if (d < bestD) { bestD = d; best = joint; }
    }
    return best;
  }

  const mark = (joint, pos) => {
    points[joint] = [Number(pos[0].toFixed(4)), Number(pos[1].toFixed(4))];
    saver.touch();
  };

  canvas.onpointerdown = (e) => {
    const pos = pointerAt(e);
    if (!pos) return;
    const under = nearest(pos);
    if (under) {
      // Grabbing an existing dot, wherever it came from - a click you placed,
      // an Auto-fit proposal, or a seeded T-pose.
      drag = under;
      next = under;
      jointSel.value = under;
      canvas.setPointerCapture?.(e.pointerId);
      render();
      return;
    }
    if (!next) return;
    mark(next, pos);
    advance();
    render();
  };

  canvas.onpointermove = (e) => {
    const pos = pointerAt(e);
    if (drag && pos) { mark(drag, pos); render(); return; }
    const over = pos ? nearest(pos) : null;
    if (over !== hover) { hover = over; draw(); }
    canvas.style.cursor = over ? 'grab' : (next ? 'crosshair' : 'default');
  };

  const release = () => { if (drag) { drag = null; render(); } };
  canvas.onpointerup = release;
  canvas.onpointercancel = release;
  canvas.onpointerleave = () => { hover = null; release(); draw(); };

  /** Step to the next unplaced joint, so a run of clicks needs no menu trips. */
  function advance() {
    if (!rigDef) return;
    const ordered = orderJoints(rigDef.joints);
    const from = ordered.indexOf(next);
    const following = ordered.slice(from + 1).find((j) => !points[j]);
    next = following || ordered.find((j) => !points[j]) || null;
    jointSel.value = next || '';
  }

  /** Aim the next click at one joint, whether or not it is already placed. */
  function target(joint) {
    next = joint;
    jointSel.value = joint;
    render();
  }

  /* Every joint, not only the placed ones.
   *
   * The list used to hold a chip per placed joint, so an unplaced joint was
   * reachable only through the dropdown. Removing a point then left `next`
   * pointing at whatever advance() had moved on to, and nothing on screen said
   * which joint the next click would land on. Listing all of them makes the
   * target visible and every joint one click away, so a removal is undone by
   * clicking again rather than by hunting through a menu. */
  function render() {
    placedList.replaceChildren();
    for (const joint of orderJoints(rigDef?.joints || [])) {
      const placed = !!points[joint];
      const row = el('div', {
        className: `jointrow ${placed ? 'placed' : ''} ${joint === next ? 'aimed' : ''}`,
      },
        el('span', { className: 'dotmark' }),
        el('span', { className: 'jointname', textContent: joint }));
      row.onclick = () => target(joint);

      if (placed) {
        const drop = el('button', { className: 'x', textContent: '✕',
                                    title: `Remove ${joint} and aim here` });
        drop.onclick = (e) => {
          e.stopPropagation();
          delete points[joint];
          saver.touch();
          // Aim at what was just removed. Without this the next click landed on
          // an unrelated joint, which is what made a deletion feel permanent.
          target(joint);
        };
        row.append(drop);
      }
      placedList.append(row);
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
        saver.touch();
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

  /* Dots to drag, for when there is nothing to aim at yet.
   *
   * The rig already carries a neutral pose in body space and `projectPoint`
   * already flattens it, so this is two things that exist rather than a new
   * layout. Front-on, because a reference sheet usually is; anything else is a
   * drag away, which is the point. */
  const tpose = el('button', { className: 'btn ghost', textContent: 'T-pose',
                               title: 'Place every joint in a neutral pose to drag from' });
  tpose.onclick = () => {
    if (!rigDef?.pose) { toast('this rig carries no neutral pose', 'warn'); return; }
    for (const [joint, p3] of Object.entries(rigDef.pose)) {
      if (points[joint]) continue;          // never overwrite your own work
      const [x, y] = projectPoint(p3, 0);
      points[joint] = [Number(x.toFixed(4)), Number(y.toFixed(4))];
    }
    saver.touch();
    toast('Neutral pose placed — drag each joint onto the figure');
    render();
  };

  const clear = el('button', { className: 'btn ghost', textContent: 'Clear' });
  clear.onclick = () => {
    points = {};
    saver.touch();
    next = orderJoints(rigDef?.joints || []).find(() => true) || null;
    jointSel.value = next || '';
    render();
  };

  const weightState = el('span', { className: 'mini savestate' });
  let pending = null;

  const weightSaver = autosaver(async () => {
    const values = pending === null
      ? null : Array.from(pending, (v) => Math.round(v * 1e4) / 1e4);
    await api.saveWeightmap(imagePath, values, EDGE);
  }, {
    onState: (phase, detail) => {
      weightState.classList.toggle('bad', phase === 'error');
      weightState.textContent = saveLabel(phase, detail);
    },
  });

  // A conditioning mask is a float per latent cell, not a flag - samplers.py
  // does mask * mask_strength * strength - so a painted map is what the
  // sampler already takes. It sits beside the annotation because both describe
  // this image and outlive any run using it.
  const painter = weightPainter({
    imagePath,
    onChange: (values) => { pending = values; weightSaver.touch(); },
  });

  const weightSection = Disclosure('Emphasis map', {
    open: false,
    note: 'where the model should attend',
    actions: weightState,
  }, painter.node);

  (async () => {
    try {
      const saved = await api.weightmap(imagePath);
      if (saved.painted && saved.values?.length) painter.set(saved.values);
    } catch { /* nothing painted yet */ }
  })();

  root.append(
    el('div', { className: 'annotbar' },
      el('span', { className: 'mini', textContent: 'Place' }), jointSel,
      counter,
      guideToggle,
      el('span', { className: 'sep' }), auto, tpose, clear, saveState),
    el('p', { className: 'help', textContent:
      'Click where each part is in this image, or drag a dot that is already '
      + 'there. Skip anything cropped or hidden — absent is a real answer, and a '
      + 'partial skeleton tells the model what is known while leaving the rest '
      + 'to it.' }),
    el('div', { className: 'annotmain' },
      canvas,
      el('div', { className: 'annotside' },
        el('h3', { textContent: 'Joints' }), placedList,
        el('h3', { textContent: 'What this implies' }), derived,
        el('h3', { textContent: 'Head construction' }), legend)),
    weightSection);

  drawFaceLegend(legend);

  return root;
}
