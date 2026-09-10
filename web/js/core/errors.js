import { toast } from '../store.js';

/* What each kind of refusal is, and how loudly to say it.
 *
 * The server already classifies: PixelError subclasses carry a `kind` and a
 * status, api.js keeps both on the Error, and every call site then threw them
 * away with `catch (e) { toast(e.message, 'error') }` - seventeen of them,
 * identical. So a 409 saying "that run is already going" looked the same as a
 * 500, and neither could be treated differently without editing each one.
 *
 * `tone` is what the toast shows. `expected` marks a refusal the user caused
 * and can act on, which is a warning rather than a failure - being told no is
 * not the same as something breaking.
 */
export const KINDS = {
  invalid:     { tone: 'warn',  expected: true },
  not_found:   { tone: 'warn',  expected: true },
  conflict:    { tone: 'warn',  expected: true },
  too_large:   { tone: 'warn',  expected: true },
  denied:      { tone: 'error', expected: false },
  unavailable: { tone: 'error', expected: false },
  internal:    { tone: 'error', expected: false },
};

export function classify(err) {
  const known = KINDS[err?.kind];
  if (known) return { kind: err.kind, ...known };
  // A thrown TypeError has no kind and is a defect, not a refusal.
  return { kind: err?.kind || 'internal', tone: 'error', expected: false };
}

export function messageFor(err) {
  const said = err?.message || String(err ?? 'Something went wrong');
  return said.trim() || 'Something went wrong';
}

/** The one place an error becomes something the user sees. */
export function showError(err, { context = '' } = {}) {
  const { tone, expected, kind } = classify(err);
  const said = messageFor(err);
  toast(context ? `${context}: ${said}` : said, tone);
  // A defect is worth a console entry; being told no is not.
  if (!expected) console.error(`[${kind}]`, err);
  return { kind, tone, expected };
}
