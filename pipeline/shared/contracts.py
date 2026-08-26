"""One declaration, three enforcement policies."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field, fields as dc_fields
from typing import Any

from .errors import Invalid


@dataclass
class Field:
    """One configurable value, and everything any consumer needs to know."""

    key: str
    label: str
    kind: str = "float"
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[tuple[str, str]] = dc_field(default_factory=list)
    default: Any = None
    when: dict[str, Any] = dc_field(default_factory=dict)

    PLACEHOLDERS = frozenset({"todo", "tbd", "fixme", "xxx", "?", "-", "n/a",
                              "help", "none", "description"})

    def __post_init__(self) -> None:
        # A blank help was already refused; twenty fields answered that by saying "TODO", which reaches the settings form as a (?) opening onto nothing.
        if not self.help or self.help.strip().rstrip(".").lower() in self.PLACEHOLDERS:
            raise ValueError(f"field '{self.key}' has no help text: "
                             f"{self.help!r}")

    def declared(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}

    def _coerce(self, value):
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
        """The one place "in range" is defined, so clamp and check are built on the same comparisons and cannot silently drift apart."""
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
        """A zoom declared max=16 accepted 64, which is sixteen times the pixels the control admits to - and a request is not a form."""
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
        """Unlike clamp, an out-of-range value is not corrected - a config file is a person's own text, and silently rewriting it on save means the file no longer says what they typed."""
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
    """A `Field` describing one setting of a `definitive` layer."""


@dataclass
class ConfigField(Field):
    """A `Field` on the pipeline settings form."""

    group: str = ""
    modules: list[str] = dc_field(default_factory=list)
    options_from: str = ""
    free_numeric: bool = False

    def _in_range(self, value) -> bool:
        if self.kind == "select" and self.options and not isinstance(
                self.options[0], (list, tuple)):
            return str(value) in {str(o) for o in self.options}
        return super()._in_range(value)

