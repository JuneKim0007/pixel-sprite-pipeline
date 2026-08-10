/* The UI primitives, in one import.
 *
 * Additive for now: nothing in web/js consumes this yet. Modules migrate one
 * at a time so each move is a diff you can read and revert on its own, rather
 * than one commit that touches every screen.
 */

export { Heading, Section, Subsection, HelpTip, LabelWithTip } from './primitives.js';
export { BaseCard } from './card.js';
export { BaseField } from './field.js';
