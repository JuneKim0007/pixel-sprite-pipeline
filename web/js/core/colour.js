/* Colour, in the forms a person types it.
 *
 * The same forms `parse_colour` in pipeline/definitive/builtin.py accepts. Two
 * implementations is a real cost and it is paid deliberately: a live preview
 * keying a different colour from the one the written file keys would be worse.
 *
 * It lives in core/ because both the shader and the settings form need it, and
 * routing the settings form through the WebGPU module to reach a regex would
 * couple a text box to a graphics API.
 */

export function parseColour(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return null;

  const hex = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const d = hex[1].length === 3
      ? [...hex[1]].map((c) => c + c).join('') : hex[1];
    return [0, 2, 4].map((i) => parseInt(d.slice(i, i + 2), 16));
  }

  const rgb = /^(?:rgb\s*\(\s*)?(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*\)?$/.exec(text);
  if (rgb) {
    const channels = rgb.slice(1, 4).map(Number);
    return channels.every((c) => c >= 0 && c <= 255) ? channels : null;
  }
  return null;
}

/** The same value as `#rrggbb`, or null if it is not a colour at all. */
export function normaliseColour(raw) {
  const rgb = parseColour(raw);
  return rgb ? '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('') : null;
}
