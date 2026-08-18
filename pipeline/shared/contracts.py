"""One declaration, three enforcement policies.

The same shape as errors.py, one axis over: an exception there carries the
status it means, and a field here carries the bounds it declares. Before this
existed, min and max were declared on 137 config fields and enforced on none
of them - the settings form read them to build a slider and the server took
whatever the request sent, which is how upscale=64 reached a control that
declares max=16.

A single declaration serves three different surfaces, and they need different
policies rather than one:

    clamp            a dragged slider - there is no error surface, so a value
                      outside the range is silently corrected to the nearest
                      edge (or to the default, if it cannot be read at all).
    check             a config save - the file is a person's own text, and
                      rewriting `steps: 400` to 150 on save means the file no
                      longer says what they typed. This refuses instead.
    clamp-and-record  a pipeline read - the job still has to run, so the value
                      is corrected like a slider, but the correction is worth
                      recording rather than swallowing silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .errors import Invalid


@dataclass
class Field:
    """One configurable value, and everything any consumer needs to know."""

    key: str
    label: str
    kind: str = "float"                       # float | int | bool | select | text
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[tuple[str, str]] = dc_field(default_factory=list)
    default: Any = None
    # A control that only makes sense when another is set a certain way.
    when: dict[str, Any] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fires at import: a field built with no explanation cannot reach a
        # form, rather than reaching one with an empty "?" where help belongs.
        if not self.help:
            raise ValueError(f"field '{self.key}' has no help text")

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind,
            "help": self.help, "min": self.min, "max": self.max,
            "step": self.step, "options": [list(o) for o in self.options],
            "default": self.default, "when": self.when,
        }

    def _coerce(self, value):
        """The caller's value, converted to this field's kind.

        Returns a two-tuple: (converted-or-None, ok). Kept separate from
        deciding what to do with a bad or out-of-range value, because clamp
        and check disagree on that and would otherwise duplicate this.
        """
        if value is None:
            return None, False
        try:
            if self.kind == "int":
                return int(value), True
            if self.kind == "float":
                return float(value), True
            if self.kind == "bool":
                return bool(value), True
        except (TypeError, ValueError):
            return None, False
        return value, True

    def _in_range(self, value) -> bool:
        """Whether `value` (already coerced) satisfies this field's bounds.

        The one place "in range" is defined, so clamp and check are built on
        the same comparisons and cannot silently drift apart.
        """
        if self.kind == "select":
            if not self.options:
                return True
            return str(value) in {str(o[0]) for o in self.options}
        if self.kind in ("int", "float"):
            if self.min is not None and value < type(value)(self.min):
                return False
            if self.max is not None and value > type(value)(self.max):
                return False
        return True

    def clamp(self, value):
        """The caller's value, coerced to this field's kind and bounds.

        min and max were declared here from the beginning and, until this
        existed, went nowhere but the browser: as_dict sent them to build the
        form and the server then took whatever arrived in the request body.
        A zoom declared max=16 accepted 64, which is sixteen times the pixels
        the control admits to - and a request is not a form. Anything the UI
        cannot ask for, the API now will not accept either.
        """
        value, ok = self._coerce(value)
        if not ok:
            return self.default
        if self.kind == "bool":
            return value
        if self.kind == "select":
            return value if self._in_range(value) else self.default
        if self.kind in ("int", "float"):
            if self.min is not None:
                value = max(value, type(value)(self.min))
            if self.max is not None:
                value = min(value, type(value)(self.max))
        return value

    def check(self, value):
        """The caller's value, or a refusal naming why it cannot be saved.

        Unlike clamp, an out-of-range value is not corrected - a config file
        is a person's own text, and silently rewriting it on save means the
        file no longer says what they typed.
        """
        coerced, ok = self._coerce(value)
        if not ok or not self._in_range(coerced):
            bounds = ""
            if self.kind == "select":
                bounds = "one of " + ", ".join(str(o[0]) for o in self.options)
            elif self.min is not None or self.max is not None:
                bounds = f"between {self.min} and {self.max}"
            detail = f" ({bounds})" if bounds else ""
            raise Invalid(f"'{self.key}' got {value!r}, which is out of range"
                          f"{detail}", field=self.key)
        return coerced


@dataclass
class LayerField(Field):
    """A `Field` describing one setting of a `definitive` layer.

    No attributes of its own: it exists so the surface has a name distinct
    from the base, and so a bound that only ever makes sense for a layer -
    something about how it interacts with admission control, say - has
    somewhere to go later that is not `Field` itself, where it would apply to
    every consumer instead of one.
    """


@dataclass
class ConfigField(Field):
    """A `Field` on the pipeline settings form.

    `kind` also accepts `text`, `textarea`, `styles`, `stages` here.
    `styles`/`stages` are opaque lists owned by the frontend's list editor
    (fields.js) - no bounds apply, so clamp/check just pass them through.
    """

    group: str = ""
    modules: list[str] = dc_field(default_factory=list)
    options_from: str = ""
    free_numeric: bool = False

    def as_dict(self) -> dict:
        base = super().as_dict()
        base["path"] = base.pop("key")
        base["type"] = base.pop("kind")
        base["group"] = self.group
        # Every `options` in this file is a flat list of strings, not the
        # base's (value, label) tuples - the base's `list(o)` would shred
        # each string into its characters. Only listify actual tuples.
        base["options"] = [list(o) if isinstance(o, (list, tuple)) else o
                            for o in self.options]
        # No `default` on a ConfigField: fields_for() fills it from the
        # stage's own DEFAULTS and detects that via `"default" not in entry`.
        # Keeping a `default: None` key here would defeat that check.
        del base["default"]
        # Unset bounds are dropped, not nulled, so a field that never
        # declared one doesn't grow a key it never had.
        for key, empty in (("min", None), ("max", None), ("step", None),
                            ("options", []), ("when", {})):
            if base[key] == empty:
                del base[key]
        if self.modules:
            base["modules"] = self.modules
        if self.options_from:
            base["options_from"] = self.options_from
        if self.free_numeric:
            base["free_numeric"] = self.free_numeric
        return base

    def _in_range(self, value) -> bool:
        # The base assumes (value, label) tuples and reads o[0]; on a flat
        # string that reads the first character instead of the option.
        if self.kind == "select" and self.options and not isinstance(
                self.options[0], (list, tuple)):
            return str(value) in {str(o) for o in self.options}
        return super()._in_range(value)

