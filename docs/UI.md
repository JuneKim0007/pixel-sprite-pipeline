# The layout rules

What a container is, how deep they nest, and which values a view is allowed to
pick. Written 2026-09-10 because none of it was written down, and eight views
had therefore answered separately.

Measured before writing, in `web/app.css`: **13 distinct border-radius values**
across 369 classes, with `--radius` already defined and losing to a hardcoded
`8px` thirty times. Now four tokens over 83 rules.

---

## The system already exists, and was abandoned

`web/js/ui/` exports 25 primitives. **12 are used by no view at all**:
`Section`, `Subsection`, `Heading`, `PanelHead`, `Fact`, `FactGrid`, `BaseCard`,
`Check`, `Mono`, `Note`, `Range`, `LabelWithTip`.

Their CSS, `.ui-section`, `.ui-subsection`, `.ui-section-body`,
`.ui-subsection-body`, `.ui-h1`, `.ui-h2`, `.ui-h3`, has never matched an
element in the DOM. Seven rules, zero users, since `125d429` ("Phase 0+A: a DOM
to test against, and the ui/ primitives layer"). Phase B did not happen.

So the rules below are not new. They are what `ui/primitives.js` already
encodes, written down so the next view can follow them instead of inventing a
third dialect.

## Four responsibilities, and nothing else is a container

A container earns a border only if it is one of these.

| Role | What it is | Border | Class |
|---|---|---|---|
| **Surface** | The page area a tab owns. One per view. | none | the view host |
| **Section** | A titled block of related things. | 1px | `ui-section` |
| **Pane** | A region of a split surface, sized by the user. | divider only | `pane` |
| **Item** | One repeated thing in a list or grid. | 1px | `card`, `listcard` |

Anything else, a row, a bar, a label, a group of chips, is layout, not a
container, and paints no border.

**Panes do not nest inside sections, and sections do not nest inside panes.**
A surface is divided into panes, or filled with sections. Choosing both is what
produced the editor's four bordered cards with four different gaps between them.

## Depth stops at two

Surface, then section, then content. A section may hold a subsection; a
subsection holds no further block. Three borders inside each other is the
signal that a responsibility above is wrong, not that a fourth is needed.

## The token scale

Views pick from these. A value not on the scale is a bug unless the comment
beside it says what it was measured against.

```
--r-sm    5px     chips, swatches, inline controls
--r-md    8px     buttons, inputs, items
--radius  10px    sections and surfaces
--r-pill  999px   status pills only
```

Radius grows with the thing it wraps: a chip is not a section. `50%` stays
literal - a dot is a circle, not a rounded rectangle.

**Padding is deliberately not on a scale.** Measured: 69 distinct values across
105 occurrences, the most common appearing four times, and most of them
asymmetric and tuned to their content. There is no cluster to collapse, so a
token would be three unused declarations pretending to be a rule. Radius had
8px thirty times and 9px eleven more, which is why it did collapse: 13 values
became 4 tokens over 83 rules.

## Ratios and minimums

A split surface stores its ratios as fractions of the container, never as pixel
widths, so the layout still answers to the window. Every split clamps to
`0.15-0.85`: a pane that reaches an edge takes its own drag handle with it and
there is no way back. Below 1100px a split collapses to one column and the
handles are hidden rather than left pointing at nothing.

A pane that holds an image gives it a fixed box and fits the image to it. Two
engines drawing the same picture at different intrinsic sizes is what made the
editor's preview resize on every drag; the box is what stops it. Magnification
that a file will have but the screen does not is stated in a label, never
performed by making the element larger.

## Where an action goes

- Acts on the whole view: the view's header, right side.
- Acts on one section: that section's heading, right side (`Heading`'s
  `actions`).
- Acts on one item: inside the item.
- Destroys something: never the primary variant, and never first in a row.

## Headings

`Heading(text, {level})` picks the tag so the document outline is real, and the
look comes from `.ui-h{n}` rather than from the tag. Restyling a level must not
silently restyle another. A view has one h1; a section an h2; a subsection an h3.

## What is not settled

Adoption. Twelve primitives are still unused and every view still hand-rolls
its heading row as `el('div', {className: 'ovhead'}, el('h2', ...))`. Converting
eight views is roughly 2,800 lines with no visual regression test to catch a
mistake, so it is one view at a time, and each conversion deletes the
hand-rolled classes it replaces rather than leaving both.
