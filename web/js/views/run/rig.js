/* Rig editor: two orthogonal canvases, joint dragging, overlays.
 *
 * Two views rather than one because a 2D drag can only ever set two of the
 * three body-space coordinates. The front view fixes lateral and height; the
 * side view fixes depth and height. Every 3D rigging tool solves it this way,
 * and the alternative — typing depth into a number box — is not editing.
 *
 * The reference image is an overlay on the same canvas rather than a separate
 * page, so calibrating the rig to your art uses exactly the same drag
 * interaction as posing it.
 */

import { api } from '../../api.js';
import { el } from '../../core/dom.js';
import { state, toast } from '../../store.js';
import {
  COLORS, JOINTS, LIMBS, dragJoint, projectPoint, rgb,
  unprojectX, visibleJoint,
} from '../../features/pose.js';

const HIT_RADIUS = 11;   // px, generous — joints are small targets
const tree = () => activeRig?.tree || {};
let neutral = null;      // reference proportions, for bone-length snapping
let activeRig = null;    // whose tree/joints the canvases are drawing
let dragging = null;

/* ------------------------------------------------------------------ draw */

function drawSkeleton(ctx, pose, yaw, w, h, { highlight = null, alpha = 1 } = {}) {
  const joints = activeRig?.joints || JOINTS;
  const limbs = activeRig?.limbs || LIMBS;
  const pts = joints.map((joint) =>
    (pose[joint] && visibleJoint(joint, yaw))
      ? projectPoint(pose[joint], yaw).map((v, i) => v * (i === 0 ? w : h))
      : null);

  ctx.globalAlpha = alpha;
  ctx.lineCap = 'round';
  limbs.forEach(([a, b], i) => {
    if (!pts[a] || !pts[b]) return;
    ctx.strokeStyle = rgb(COLORS[i % COLORS.length]);
    ctx.lineWidth = Math.max(3, Math.min(w, h) / 90);
    ctx.beginPath();
    ctx.moveTo(pts[a][0], pts[a][1]);
    ctx.lineTo(pts[b][0], pts[b][1]);
    ctx.stroke();
  });

  pts.forEach((p, i) => {
    if (!p) return;
    const isHot = joints[i] === highlight;
    ctx.fillStyle = rgb(COLORS[i % COLORS.length]);
    ctx.beginPath();
    ctx.arc(p[0], p[1], isHot ? 7 : 4.5, 0, Math.PI * 2);
    ctx.fill();
    if (isHot) {
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });
  ctx.globalAlpha = 1;
  return pts;
}

/** Crude depth shading, matching what the depth stage will actually render. */
function drawDepth(ctx, pose, yaw, w, h) {
  const yawRad = (yaw * Math.PI) / 180;
  const depths = {};
  for (const [joint, p] of Object.entries(pose)) {
    depths[joint] = p[1] * Math.cos(yawRad) + p[0] * Math.sin(yawRad);
  }
  const values = Object.values(depths);
  if (!values.length) return;
  const lo = Math.min(...values), hi = Math.max(...values), span = (hi - lo) || 1;

  const bones = (activeRig?.bones || [
    ['neck', 'r_hip', 0.115], ['neck', 'l_hip', 0.115],
    ['neck', 'r_shoulder', 0.075], ['neck', 'l_shoulder', 0.075],
    ['r_shoulder', 'r_elbow', 0.045], ['l_shoulder', 'l_elbow', 0.045],
    ['r_elbow', 'r_wrist', 0.036], ['l_elbow', 'l_wrist', 0.036],
    ['r_hip', 'r_knee', 0.055], ['l_hip', 'l_knee', 0.055],
    ['r_knee', 'r_ankle', 0.042], ['l_knee', 'l_ankle', 0.042],
    ['neck', 'nose', 0.07],
  ]).slice().sort((a, b) =>
    ((depths[a[0]] + depths[a[1]]) / 2) - ((depths[b[0]] + depths[b[1]]) / 2));

  ctx.save();
  ctx.filter = 'blur(6px)';
  for (const [a, b, thickness] of bones) {
    if (!pose[a] || !pose[b]) continue;
    const pa = projectPoint(pose[a], yaw), pb = projectPoint(pose[b], yaw);
    const shade = Math.round(60 + 195 * (((depths[a] + depths[b]) / 2 - lo) / span));
    ctx.strokeStyle = `rgb(${shade},${shade},${shade})`;
    ctx.lineWidth = thickness * Math.min(w, h);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(pa[0] * w, pa[1] * h);
    ctx.lineTo(pb[0] * w, pb[1] * h);
    ctx.stroke();
  }
  ctx.restore();
}

function paint(canvas, yaw, pose, refImage) {
  const ctx = canvas.getContext('2d');
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--panel-2') || '#222';
  ctx.fillRect(0, 0, w, h);

  if (state.overlay.reference && refImage?.complete && refImage.naturalWidth) {
    ctx.globalAlpha = state.overlay.opacity;
    const scale = Math.min(w / refImage.naturalWidth, h / refImage.naturalHeight);
    const dw = refImage.naturalWidth * scale, dh = refImage.naturalHeight * scale;
    ctx.drawImage(refImage, (w - dw) / 2, (h - dh) / 2, dw, dh);
    ctx.globalAlpha = 1;
  }
  if (state.overlay.depth) drawDepth(ctx, pose, yaw, w, h);
  if (state.overlay.skeleton) {
    drawSkeleton(ctx, pose, yaw, w, h, { highlight: state.selectedJoint });
  }

  ctx.fillStyle = 'rgba(255,255,255,.45)';
  ctx.font = '11px ui-monospace, monospace';
  ctx.fillText(`${yaw.toFixed(0)}°`, 8, 16);
}

/* ----------------------------------------------------------- interaction */

function hitTest(canvas, yaw, pose, mx, my) {
  let best = null, bestDist = HIT_RADIUS;
  for (const joint of (activeRig?.joints || JOINTS)) {
    if (!pose[joint] || !visibleJoint(joint, yaw)) continue;
    const [x, y] = projectPoint(pose[joint], yaw);
    const d = Math.hypot(x * canvas.width - mx, y * canvas.height - my);
    if (d < bestDist) { bestDist = d; best = joint; }
  }
  return best;
}

function attach(canvas, getYaw, onEdit, refImage) {
  const toLocal = (e) => {
    const r = canvas.getBoundingClientRect();
    return [
      (e.clientX - r.left) * (canvas.width / r.width),
      (e.clientY - r.top) * (canvas.height / r.height),
    ];
  };

  canvas.onpointerdown = (e) => {
    const entry = state.poseEntries[state.poseFrame];
    if (!entry) return;
    const [mx, my] = toLocal(e);
    const joint = hitTest(canvas, getYaw(), entry.pose, mx, my);
    if (!joint) return;
    state.selectedJoint = joint;
    dragging = { canvas, joint };
    canvas.setPointerCapture(e.pointerId);
    onEdit({ redrawOnly: true });
  };

  canvas.onpointermove = (e) => {
    const entry = state.poseEntries[state.poseFrame];
    if (!entry) return;
    const [mx, my] = toLocal(e);

    if (!dragging || dragging.canvas !== canvas) {
      const over = hitTest(canvas, getYaw(), entry.pose, mx, my);
      canvas.style.cursor = over ? 'grab' : 'default';
      return;
    }

    canvas.style.cursor = 'grabbing';
    const yaw = getYaw();
    const joint = dragging.joint;
    const point = entry.pose[joint];
    const x = mx / canvas.width, y = my / canvas.height;

    const solved = unprojectX(x, yaw, point);
    const target = [solved.lateral, solved.depth, Math.max(0, Math.min(1, y))];

    // Forward kinematics: the dragged joint takes its own limb with it and
    // leaves every other limb untouched.
    entry.pose = neutral
      ? dragJoint(entry.pose, tree(), neutral, joint, target)
      : { ...entry.pose, [joint]: target };
    onEdit({});
  };

  const release = (e) => {
    if (dragging?.canvas === canvas) {
      dragging = null;
      canvas.releasePointerCapture?.(e.pointerId);
      onEdit({});
    }
  };
  canvas.onpointerup = release;
  canvas.onpointercancel = release;
}

/* ----------------------------------------------------------------- panel */

function inspector(onEdit) {
  const entry = state.poseEntries[state.poseFrame];
  const box = el('div', { className: 'inspector' });

  if (!entry) {
    box.append(el('p', { className: 'empty', textContent: 'No pose loaded.' }));
    return box;
  }
  if (!state.selectedJoint) {
    box.append(el('p', { className: 'empty', textContent: 'Click a joint to edit it.' }));
    return box;
  }

  const joint = state.selectedJoint;
  const point = entry.pose[joint] || [0, 0, 0];
  box.append(el('h3', { textContent: joint }));

  ['lateral', 'depth', 'height'].forEach((label, axis) => {
    const [min, max] = axis === 2 ? [0, 1] : [-0.5, 0.5];
    const num = el('input', {
      type: 'number', step: 0.005, value: point[axis].toFixed(3), className: 'num',
    });
    const range = el('input', { type: 'range', min, max, step: 0.005, value: point[axis] });

    const commit = (raw) => {
      const value = Math.max(min, Math.min(max, parseFloat(raw) || 0));
      entry.pose[joint][axis] = value;
      num.value = value.toFixed(3);
      range.value = value;
      onEdit({});
    };
    range.oninput = () => { num.value = parseFloat(range.value).toFixed(3); };
    range.onchange = () => commit(range.value);
    num.onchange = () => commit(num.value);

    box.append(el('label', { className: 'mini', textContent: label }),
      el('div', { className: 'control' }, range, num));
  });

  const reset = el('button', { className: 'btn ghost', textContent: 'Reset joint' });
  reset.onclick = () => {
    if (neutral?.[joint]) {
      entry.pose[joint] = [...neutral[joint]];
      onEdit({});
    }
  };
  box.append(reset);
  return box;
}

/* ------------------------------------------------------------------ view */

export function rigEditor({ runId, onDirty } = {}) {
  const root = el('div', { className: 'rig' });
  const refImage = new Image();

  const front = el('canvas', { width: 420, height: 520, className: 'rigcanvas' });
  const side = el('canvas', { width: 420, height: 520, className: 'rigcanvas' });
  const yawLabel = el('span', { className: 'mono' });
  const yawSlider = el('input', { type: 'range', min: 0, max: 355, step: 5, value: 90 });

  const insp = el('div', { className: 'rigside' });
  const timeline = el('div', { className: 'timeline' });

  const redraw = () => {
    const entry = state.poseEntries[state.poseFrame];
    if (!entry) return;
    const yaw = Number(yawSlider.value);
    yawLabel.textContent = `${yaw}°`;
    paint(front, 0, entry.pose, refImage);          // front: lateral + height
    paint(side, yaw, entry.pose, refImage);         // side: depth + height
    insp.replaceChildren(inspector(onEdit));
    drawTimeline();
  };

  const onEdit = ({ redrawOnly } = {}) => {
    if (!redrawOnly) onDirty?.();
    redraw();
  };

  function drawTimeline() {
    timeline.replaceChildren();
    state.poseEntries.forEach((entry, i) => {
      const chip = el('button', {
        className: `framechip ${i === state.poseFrame ? 'on' : ''}`,
        textContent: String(i + 1),
        title: `${entry.yaw}°`,
      });
      chip.onclick = () => { state.poseFrame = i; redraw(); };
      timeline.append(chip);
    });
  }

  yawSlider.oninput = redraw;
  attach(front, () => 0, onEdit, refImage);
  attach(side, () => Number(yawSlider.value), onEdit, refImage);

  /* overlay controls */
  const toggle = (key, label) => {
    const box = el('input', { type: 'checkbox', checked: state.overlay[key] });
    box.onchange = () => { state.overlay[key] = box.checked; redraw(); };
    return el('label', { className: 'chk' }, box, ` ${label}`);
  };

  const opacity = el('input', {
    type: 'range', min: 0, max: 1, step: 0.05, value: state.overlay.opacity,
  });
  opacity.oninput = () => { state.overlay.opacity = Number(opacity.value); redraw(); };

  const refPicker = el('select', { className: 'select' });
  refPicker.append(el('option', { value: '', textContent: 'no reference' }));
  refPicker.onchange = () => {
    state.overlay.refPath = refPicker.value || null;
    state.overlay.reference = !!refPicker.value;
    if (refPicker.value) {
      refImage.onload = redraw;
      refImage.src = api.fileUrl(refPicker.value);
    } else {
      redraw();
    }
  };

  api.browse('', true)
    .then(({ entries }) => {
      const images = entries.filter((e) => e.is_image);
      for (const item of images) {
        refPicker.append(el('option', { value: item.path, textContent: item.name }));
      }
      // Prefer whatever this pipeline already references, else the first
      // upload: opening the editor with no underlay wastes the feature.
      // The underlay is an identity reference: it is the character, drawn
      // behind the skeleton so joints can be placed against it.
      const configured = (state.effective?.references?.identity || [])[0]?.path;
      const preferred = configured
        || state.overlay.refPath
        || images[0]?.path;
      if (preferred) {
        refPicker.value = preferred;
        refPicker.onchange();
      }
    })
    .catch(() => { /* no input dir yet — the picker just stays empty */ });

  root.append(
    el('div', { className: 'rigbar' },
      toggle('skeleton', 'Skeleton'),
      toggle('depth', 'Depth'),
      el('span', { className: 'sep' }),
      el('span', { className: 'mini', textContent: 'Reference' }), refPicker,
      el('span', { className: 'mini', textContent: 'opacity' }), opacity),
    el('div', { className: 'rigmain' },
      el('div', { className: 'rigcanvases' },
        el('figure', {},
          el('figcaption', {}, 'FRONT ', el('span', { className: 'mini' }, 'lateral + height')),
          front),
        el('figure', {},
          el('figcaption', {}, 'SIDE ', el('span', { className: 'mini' }, 'depth + height'), ' ', yawLabel),
          side)),
      insp),
    el('div', { className: 'rigfoot' },
      el('span', { className: 'mini', textContent: 'view angle' }), yawSlider,
      el('span', { className: 'sep' }), timeline));

  /* ---------------------------------------------------------- loading */

  const sourceSel = el('select', { className: 'select' });
  const status = el('span', { className: 'mini' });

  const setEntries = (entries, note, rigDef = null) => {
    // Without this the canvases fall back to the humanoid layout and a spider
    // draws as a person — the topology has to travel with the pose.
    activeRig = rigDef;
    state.poseEntries = entries;
    state.poseFrame = 0;
    state.selectedJoint = null;
    neutral = entries[0] ? structuredClone(entries[0].pose) : null;
    status.textContent = note;
    redraw();
  };

  /** Pose data always resolves to *something* drawable.
   *
   * A run that failed before its pose stage has no skeletons to edit, but
   * showing an empty canvas and an error is useless — the library and the rig
   * T-poses are always available, so fall back to those and say so. */
  async function loadSources() {
    const options = [];
    let runEntries = null;
    let runRig = null;

    if (runId) {
      try {
        const data = await api.poses(runId);
        if (data.entries?.length) {
          runEntries = data.entries;
          runRig = data.rig_def || null;
          options.push({ id: 'run', label: `run · ${data.entries.length} frames (editable)` });
        }
      } catch {
        // No pose stage output. Not an error worth blocking on.
      }
    }

    let library = {};
    try {
      library = (await api.poses('')).library || {};
    } catch { /* poses/ may be empty */ }

    for (const [name, data] of Object.entries(library)) {
      options.push({ id: `lib:${name}`, label: `library · ${name} (${data.frames?.length || 0})` });
    }
    for (const rig of state.schema.options.rig_info || []) {
      options.push({ id: `rig:${rig.name}`, label: `T-pose · ${rig.label}` });
    }

    sourceSel.replaceChildren(
      ...options.map((o) => el('option', { value: o.id, textContent: o.label })));

    sourceSel.onchange = () => {
      const value = sourceSel.value;
      if (value === 'run') {
        setEntries(structuredClone(runEntries),
          'editing run output — Save writes it back', runRig);
      } else if (value.startsWith('lib:')) {
        const entry = library[value.slice(4)] || {};
        const frames = entry.frames || [];
        api.rigPose(entry.rig || 'humanoid').then((def) => {
          setEntries(frames.map((pose) => ({ pose: structuredClone(pose), yaw: 90, spec: 0 })),
            'library preview — not attached to a run', def);
        });
      } else {
        const name = value.slice(4);
        api.rigPose(name).then((def) => {
          setEntries([{ pose: def.pose, yaw: 40, spec: 0 }],
            `${def.label} T-pose — preview only`, def);
        }).catch((e) => { status.textContent = e.message; });
      }
    };

    if (!options.length) {
      status.textContent = 'No poses available. Run tools/make_poses.py.';
      return;
    }
    sourceSel.value = options[0].id;
    sourceSel.onchange();
  }

  root.querySelector('.rigbar').prepend(
    el('span', { className: 'mini', textContent: 'Pose' }), sourceSel,
    status, el('span', { className: 'sep' }));

  loadSources();
  return root;
}

export async function savePoses(runId) {
  if (!runId) { toast('Run the pose stage first', 'warn'); return false; }
  await api.savePoses(runId, state.poseEntries);
  toast('Skeletons saved and re-rendered');
  return true;
}
