/* Loomis-style head construction, drawn as a guide.
 *
 * Purely decorative and never saved: the annotation stores joint positions,
 * and this only helps a person decide where those positions should go. Eyes,
 * ears and nose are the fiddliest points to place because a head is a volume
 * and the annotation is a handful of dots — the classic sphere-plus-jaw
 * construction makes that volume visible.
 *
 * Everything is derived from whatever the user has already placed, so the
 * guide follows the head rather than sitting at a fixed spot:
 *
 *   neck + nose      give head height and tilt
 *   ear spread       gives the turn, since ears converge as the head rotates
 *   eyes             pin the eye line if placed, otherwise it is estimated
 *
 * Canvas 2D only. No model, no GPU, a fraction of a millisecond.
 */

const CRANIUM = 0.62;   // sphere radius as a fraction of total head height
const EYE_LINE = 0.52;  // eye height down the head, roughly the midpoint
const JAW_WIDTH = 0.72; // jaw width relative to the cranium diameter

/** Work out head geometry from the placed points, in canvas pixels. */
export function headFrame(points, project) {
  const at = (name) => (points[name] ? project(points[name]) : null);

  const nose = at('nose');
  const neck = at('neck');
  const lEar = at('l_ear');
  const rEar = at('r_ear');
  const lEye = at('l_eye');
  const rEye = at('r_eye');

  // Height: prefer neck-to-nose; fall back to ear spread when the neck is
  // absent, which is common in a close crop.
  let height = null;
  let centre = null;
  let tilt = 0;

  if (neck && nose) {
    const dx = nose[0] - neck[0];
    const dy = nose[1] - neck[1];
    height = Math.hypot(dx, dy) * 1.9;
    tilt = Math.atan2(dx, -dy);
    centre = [nose[0] - Math.sin(tilt) * height * 0.10,
              nose[1] + Math.cos(tilt) * height * 0.10];
  } else if (lEar && rEar) {
    height = Math.hypot(lEar[0] - rEar[0], lEar[1] - rEar[1]) * 2.4;
    centre = [(lEar[0] + rEar[0]) / 2, (lEar[1] + rEar[1]) / 2];
  } else if (nose) {
    return null;   // a lone nose says nothing about scale
  } else {
    return null;
  }
  if (!height || height < 4) return null;

  // Turn: ears converge toward each other as the head rotates away from
  // front-on, so their separation against the expected width reads as yaw.
  let turn = 0;
  if (lEar && rEar) {
    const spread = Math.hypot(lEar[0] - rEar[0], lEar[1] - rEar[1]);
    turn = Math.max(-1, Math.min(1, 1 - spread / (height * 0.52)));
    if (nose) {
      const mid = (lEar[0] + rEar[0]) / 2;
      turn *= Math.sign(nose[0] - mid) || 1;
    }
  } else if (lEye && rEye && nose) {
    const mid = (lEye[0] + rEye[0]) / 2;
    turn = Math.max(-1, Math.min(1, (nose[0] - mid) / (height * 0.18)));
  }

  const eyeY = lEye && rEye ? (lEye[1] + rEye[1]) / 2 : null;
  return { centre, height, tilt, turn, eyeY };
}

/** Draw the construction. `project` maps a normalised point to canvas pixels. */
export function drawFaceGuide(ctx, points, project, { color = 'rgba(255,120,150,.85)' } = {}) {
  const frame = headFrame(points, project);
  if (!frame) return false;

  const { centre, height, tilt, turn, eyeY } = frame;
  const r = height * CRANIUM * 0.5;

  ctx.save();
  ctx.translate(centre[0], centre[1]);
  ctx.rotate(tilt);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.setLineDash([]);

  // 1. Cranium — the sphere the whole head is built on.
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, Math.PI * 2);
  ctx.stroke();

  // 2. Side plane — the flat the ear sits on, an ellipse narrowing with turn.
  const planeOffset = turn * r * 0.62;
  ctx.beginPath();
  ctx.ellipse(planeOffset, 0, r * Math.max(0.12, Math.abs(turn) * 0.62 + 0.12), r, 0, 0, Math.PI * 2);
  ctx.stroke();

  // 3. Centre line — bends with the turn, which is what makes a head read as
  //    facing somewhere rather than straight on.
  const cx = -turn * r * 0.85;
  ctx.beginPath();
  ctx.ellipse(cx, 0, Math.max(2, Math.abs(turn) * r * 0.9 + 2), r, 0, -Math.PI / 2, Math.PI / 2);
  ctx.stroke();

  // 4. Eye line — placed eyes win; otherwise the standard proportion.
  const eyeLocal = eyeY != null
    ? (eyeY - centre[1]) * Math.cos(tilt)
    : -r + height * EYE_LINE;
  ctx.beginPath();
  ctx.ellipse(0, eyeLocal, r, Math.max(2, Math.abs(turn) * r * 0.35 + 2), 0, 0, Math.PI * 2);
  ctx.stroke();

  // 5. Jaw — cranium bottom tapering to the chin.
  const jawTop = r * 0.55;
  const chin = height * 0.5;
  const halfW = r * JAW_WIDTH;
  ctx.beginPath();
  ctx.moveTo(-halfW + planeOffset * 0.3, jawTop);
  ctx.quadraticCurveTo(-halfW * 0.75 + planeOffset * 0.5, chin * 0.92,
                       cx * 0.6, chin);
  ctx.quadraticCurveTo(halfW * 0.75 + planeOffset * 0.5, chin * 0.92,
                       halfW + planeOffset * 0.3, jawTop);
  ctx.stroke();

  // 6. Where the ear belongs: behind the centre line, between brow and nose.
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.ellipse(planeOffset, eyeLocal + r * 0.28, r * 0.16, r * 0.26, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.restore();
  return true;
}

/** A standalone diagram for the side panel, so the construction is legible
 *  even before anything has been placed. */
export function drawFaceLegend(canvas) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const fake = {
    neck: [0.5, 0.86], nose: [0.56, 0.46],
    l_ear: [0.40, 0.44], r_ear: [0.60, 0.44],
  };
  const project = (p) => [p[0] * w, p[1] * h];
  drawFaceGuide(ctx, fake, project, { color: 'rgba(255,120,150,.75)' });

  ctx.fillStyle = 'rgba(255,255,255,.55)';
  ctx.font = '9px ui-monospace, monospace';
  ctx.fillText('eye line', 4, h * 0.47);
  ctx.fillText('ear', w * 0.62, h * 0.58);
  ctx.fillText('jaw', w * 0.36, h * 0.86);
}
