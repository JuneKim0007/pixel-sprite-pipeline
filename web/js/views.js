/* Viewing angles and the 18-joint skeleton, mirrored from the Python side.
 *
 * Duplicated deliberately: the rig editor has to project poses at 60fps while
 * dragging, and a round-trip per frame would make it unusable. The values are
 * a fixed protocol — the OpenPose layout and the named views — so they do not
 * drift the way tunable settings would.
 */

export const VIEWS = {
  front: 0,
  three_quarter_front: 40,
  side: 90,
  three_quarter_rear: 145,
  rear_turned: 170,
  rear: 180,
};

export const VIEW_OPTIONS = Object.keys(VIEWS);

export const JOINTS = [
  'nose', 'neck',
  'r_shoulder', 'r_elbow', 'r_wrist',
  'l_shoulder', 'l_elbow', 'l_wrist',
  'r_hip', 'r_knee', 'r_ankle',
  'l_hip', 'l_knee', 'l_ankle',
  'r_eye', 'l_eye', 'r_ear', 'l_ear',
];

export const LIMBS = [
  [1, 2], [1, 5], [2, 3], [3, 4], [5, 6], [6, 7],
  [1, 8], [8, 9], [9, 10], [1, 11], [11, 12], [12, 13],
  [1, 0], [0, 14], [14, 16], [0, 15], [15, 17],
];

/* The canonical OpenPose colour wheel. ControlNet was trained against these
 * exact hues, so they are protocol rather than decoration — the editor shows
 * the same colours the model will see. */
export const COLORS = [
  [255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0],
  [85, 255, 0], [0, 255, 0], [0, 255, 85], [0, 255, 170], [0, 255, 255],
  [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255], [170, 0, 255],
  [255, 0, 255], [255, 0, 170], [255, 0, 85],
];

export const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

/* `?? 0` never fired: parseFloat returns a number or NaN, never null, so a
 * junk view name produced NaN and every downstream angle computed from it
 * became NaN — silently, since NaN propagates rather than throwing. Guard on
 * finiteness, which is what the 0 fallback was reaching for. */
export const resolveView = (view) => {
  if (typeof view === 'number') return Number.isFinite(view) ? view : 0;
  if (view in VIEWS) return VIEWS[view];
  const deg = parseFloat(view);
  return Number.isFinite(deg) ? deg : 0;
};

export const FACE_ONLY = new Set(['nose', 'r_eye', 'l_eye']);

/** Whether a keypoint is emitted at this angle.
 *
 * Only the face is angle-dependent, and it matters: the presence or absence of
 * a nose is how a skeleton tells ControlNet which way the character faces. */
export function visibleJoint(joint, yawDeg) {
  if (!FACE_ONLY.has(joint)) return true;
  const facing = Math.cos((yawDeg * Math.PI) / 180);
  return joint === 'nose' ? facing > -0.15 : facing > 0.05;
}

/** Body-space (lateral, depth, height) -> screen (x, y) in 0..1. */
export function projectPoint(point, yawDeg, { centre = 0.5, depthScale = 1, lateralScale = 1 } = {}) {
  const yaw = (yawDeg * Math.PI) / 180;
  const [lateral, depth, height] = point;
  return [
    centre + depth * depthScale * Math.sin(yaw) - lateral * lateralScale * Math.cos(yaw),
    height,
  ];
}

/** Inverse of projectPoint for a drag: solve for whichever axis the view shows.
 *
 * At yaw 0 the horizontal axis is pure lateral; at yaw 90 it is pure depth.
 * In between both contribute, so a drag is ambiguous — which is exactly why
 * the editor offers two orthogonal views instead of one. */
export function unprojectX(x, yawDeg, point, { centre = 0.5, depthScale = 1, lateralScale = 1 } = {}) {
  const yaw = (yawDeg * Math.PI) / 180;
  const sin = Math.sin(yaw), cos = Math.cos(yaw);
  const [lateral, depth] = point;
  const dx = x - centre;

  // Whichever axis this view is more sensitive to absorbs the change.
  if (Math.abs(cos) >= Math.abs(sin)) {
    const nextLateral = (depth * depthScale * sin - dx) / (lateralScale * cos);
    return { lateral: nextLateral, depth };
  }
  const nextDepth = (dx + lateral * lateralScale * cos) / (depthScale * sin);
  return { lateral, depth: nextDepth };
}


export const SKELETON_TREE = {
  neck: ['nose', 'r_shoulder', 'l_shoulder', 'r_hip', 'l_hip'],
  r_shoulder: ['r_elbow'], r_elbow: ['r_wrist'],
  l_shoulder: ['l_elbow'], l_elbow: ['l_wrist'],
  r_hip: ['r_knee'], r_knee: ['r_ankle'],
  l_hip: ['l_knee'], l_knee: ['l_ankle'],
  nose: ['r_eye', 'l_eye', 'r_ear', 'l_ear'],
};

export const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** Parent of a joint in the given tree, or null for the root. */
export function parentOf(tree, joint) {
  for (const [parent, kids] of Object.entries(tree)) {
    if (kids.includes(joint)) return parent;
  }
  return null;
}

/** Every joint at or below `joint`. */
export function subtree(tree, joint) {
  const out = [joint];
  for (let i = 0; i < out.length; i++) {
    for (const kid of tree[out[i]] || []) out.push(kid);
  }
  return out;
}

/** Move one joint, taking its own limb with it and leaving the rest alone.
 *
 * Forward kinematics, which is what a rig drag should feel like:
 *
 *   the dragged joint  cannot leave the sphere its parent's bone length
 *                      describes, so the drag rotates that limb rather than
 *                      stretching it
 *   its descendants    travel with it rigidly, then have their own bone
 *                      lengths restored
 *   everything else    is not touched at all
 *
 * Re-snapping the whole skeleton from the root, which is what this replaced,
 * could perturb joints on limbs the user never touched.
 */
export function dragJoint(pose, tree, neutral, joint, target) {
  const next = {};
  for (const [k, v] of Object.entries(pose)) next[k] = [...v];

  const parent = parentOf(tree, joint);
  let landed = [...target];

  if (parent && next[parent] && neutral[joint] && neutral[parent]) {
    const want = dist3(neutral[parent], neutral[joint]);
    const p = next[parent];
    let d = [landed[0] - p[0], landed[1] - p[1], landed[2] - p[2]];
    let len = Math.hypot(...d);
    if (len < 1e-6) {
      d = [
        neutral[joint][0] - neutral[parent][0],
        neutral[joint][1] - neutral[parent][1],
        neutral[joint][2] - neutral[parent][2],
      ];
      len = Math.hypot(...d) || 1;
    }
    const k = want / len;
    landed = [p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k];
  }

  const delta = [
    landed[0] - next[joint][0],
    landed[1] - next[joint][1],
    landed[2] - next[joint][2],
  ];
  // Carry the limb below the dragged joint along with it.
  for (const child of subtree(tree, joint)) {
    if (!next[child]) continue;
    next[child] = [
      next[child][0] + delta[0],
      next[child][1] + delta[1],
      next[child][2] + delta[2],
    ];
  }

  // Restore bone lengths inside that limb only.
  const queue = [joint];
  while (queue.length) {
    const p = queue.shift();
    for (const child of tree[p] || []) {
      if (!next[child] || !neutral[child] || !neutral[p]) continue;
      const want = dist3(neutral[p], neutral[child]);
      let d = [
        next[child][0] - next[p][0],
        next[child][1] - next[p][1],
        next[child][2] - next[p][2],
      ];
      let len = Math.hypot(...d);
      if (len < 1e-6) {
        d = [
          neutral[child][0] - neutral[p][0],
          neutral[child][1] - neutral[p][1],
          neutral[child][2] - neutral[p][2],
        ];
        len = Math.hypot(...d) || 1;
      }
      const k = want / len;
      next[child] = [
        next[p][0] + d[0] * k, next[p][1] + d[1] * k, next[p][2] + d[2] * k,
      ];
      queue.push(child);
    }
  }
  return next;
}

export function snapToAnatomy(pose, neutral) {
  const out = {};
  for (const key of Object.keys(neutral)) out[key] = [...(pose[key] || neutral[key])];

  const queue = ['neck'];
  while (queue.length) {
    const parent = queue.shift();
    for (const child of SKELETON_TREE[parent] || []) {
      if (!out[child] || !neutral[child]) continue;
      const want = dist3(neutral[parent], neutral[child]);
      const [px, py, pz] = out[parent];
      let [dx, dy, dz] = [out[child][0] - px, out[child][1] - py, out[child][2] - pz];
      let length = Math.hypot(dx, dy, dz);
      if (length < 1e-6) {
        dx = neutral[child][0] - neutral[parent][0];
        dy = neutral[child][1] - neutral[parent][1];
        dz = neutral[child][2] - neutral[parent][2];
        length = Math.hypot(dx, dy, dz) || 1;
      }
      const k = want / length;
      out[child] = [px + dx * k, py + dy * k, pz + dz * k];
      queue.push(child);
    }
  }
  return out;
}
