# The layout rules

What a container is, how deep they nest, and which values a view is allowed to
pick. Written 2026-09-10 because none of it was written down, and eight views
had therefore answered separately.

Measured before writing, in `web/app.css`: **13 distinct border-radius values**
across 369 classes, with `--radius` already defined and losing to a hardcoded
`8px` thirty times. Now four tokens over 83 rules.

---

## Two dialects, and the unused one did not fit

An earlier draft of this file said the primitives were written and simply not
adopted, and that `Section` was the bordered container. That was asserted from
the names, not read from the CSS, and it was wrong.

`.ui-section` was `margin: 0 0 1.5rem` - no background, no border, no padding.
`.group` is the surface: background, 1px border, radius, and a styled heading
bar. Adopting `Section` would have removed a border from every panel in the app.
They were unused because they did not fit, not because nobody got round to it,
and the rem/px split says they were written apart from everything else.

Removed 2026-09-10: `Section`, `Subsection`, `Heading`, and the eleven `.ui-*`
rules that matched nothing. `PanelHead` already produced exactly what five views
were building by hand and now has five callers.

## Four responsibilities, and nothing else is a container

A container earns a border only if it is one of these.

| Role | What it is | Border | Class |
|---|---|---|---|
| **Surface** | The page area a tab owns. One per view. | none | the view host |
| **Section** | A titled block of related things. | 1px | `group` + `PanelHead` |
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

`PanelHead(title, {note, action})` is the section head: an h2, an optional note,
an optional action on the right. It is the only one - a second heading component
existed, keyed off its own classes, and was deleted rather than reconciled.

## What is not settled

Seven primitives still have no caller: `Fact`, `FactGrid`, `BaseCard`, `Check`,
`Mono`, `Range`, `LabelWithTip`. Each needs the same check `Section` failed -
does its CSS match what a view actually needs - before it is either adopted or
deleted. Do not sweep them as a batch; that is how `Section` was documented as
a bordered container without anyone reading the rule.

A test refuses CSS that matches nothing, so a third dialect cannot accumulate
quietly again.
