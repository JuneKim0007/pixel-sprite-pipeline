from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.definitive import layers
from pipeline.generation import stage
from pipeline.geometry import props, rigs
from pipeline.looks import palettes, styles
from pipeline.shared import NotFound, paths
from pipeline.shared.registry import Decorated, Registry, Scanned

LOOKUPS = {
    "rig": lambda root: rigs.get("no_such_rig"),
    "stage": lambda root: stage.get("no_such_stage"),
    "layer": lambda root: layers.get("no_such_layer"),
    "palette": lambda root: palettes.registry(root).get("no_such_palette"),
    "prop": lambda root: props.registry(root).get("no_such_prop"),
    "style sheet": lambda root: styles.registry(root).get("no_such_style"),
}


@pytest.mark.parametrize("what", sorted(LOOKUPS))
def test_a_missing_entry_raises_and_names_the_alternatives(what, root):
    with pytest.raises(NotFound) as caught:
        LOOKUPS[what](root)
    assert what in str(caught.value)
    assert caught.value.hint, "the lookup did not name the alternatives"


def test_a_file_that_will_not_parse_is_reported_not_omitted(root):
    bad = paths.resolve(root, "palettes") / "_registry_test.hex"
    bad.write_text("// name: Broken\nnot a hex value\n")
    try:
        reg = palettes.registry(root)
        listed = [b for b in reg.broken() if b.path == bad]
        assert listed, "a malformed palette vanished instead of being reported"
        assert "colour" in listed[0].why.lower(), f"unhelpful: {listed[0].why!r}"
        assert "_registry_test" not in reg.all(), "it was loaded anyway"
    finally:
        bad.unlink()

    assert not [b for b in palettes.registry(root).broken() if b.path == bad], \
        "the cache did not notice the file was deleted"


def test_a_registry_read_before_its_modules_import_does_not_cache_the_emptiness():
    late = Decorated()
    growing = Registry("late", late)
    assert len(growing) == 0
    late.add("added_after_first_read", object())
    assert len(growing) == 1


def test_a_second_read_of_unchanged_files_does_not_reparse(root):
    calls = []

    def counted(path: Path):
        calls.append(path)
        return path.stem, path.stem

    probe = Registry("probe", Scanned(paths.resolve(root, "palettes"),
                                      ["**/*.hex"], counted))
    probe.all()
    first = len(calls)
    probe.all()
    assert len(calls) == first, f"reparsed unchanged files ({first} -> {len(calls)})"
