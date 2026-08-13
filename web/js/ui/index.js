/* Every presentational piece, in one import.
 *
 * Three groups, and the split is by how much a caller has to know:
 *
 *   kit         widgets. Button, Select, Row, Head. A caller names what a
 *               thing IS - Button.primary - and never writes a class string.
 *   primitives  structure. Section, Heading, HelpTip.
 *   field/card  bases a caller subclasses when a widget is not enough.
 *
 * Nothing here knows what a rig or a palette is. A primitive that understands
 * the domain has stopped being one, which is the rule that keeps this folder
 * reusable and testable without a server.
 */

export {
  Button, Check, Empty, Fact, FactGrid, Fields, Head, Mini, Mono, Note, Num, Ok,
  PanelHead, Range, Row, Segmented, Select, Warn,
} from './kit.js';
export { Heading, HelpTip, LabelWithTip, Section, Subsection } from './primitives.js';
export { BaseCard } from './card.js';
export { BaseField } from './field.js';
