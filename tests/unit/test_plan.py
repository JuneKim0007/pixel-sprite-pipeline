"""The one dependency walk, in both the modes its two engines need."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pipeline.shared import plan
from pipeline.shared.errors import Invalid


@dataclass
class N:
    name: str
    needs: frozenset = field(default_factory=frozenset)
    gives: frozenset = field(default_factory=frozenset)
    optional: frozenset = field(default_factory=frozenset)


def test_a_satisfied_order_has_nothing_unmet():
    assert plan.unmet([N("a", gives={"x"}), N("b", needs={"x"})]) == []


def test_a_producer_that_runs_later_is_named():
    problems = plan.unmet([N("b", needs={"x"}), N("a", gives={"x"})])
    assert [(p.node, p.name, p.producer) for p in problems] == [("b", "x", "a")]


def test_strict_refuses_a_name_nothing_gives_at_all():
    problems = plan.unmet([N("b", needs={"x"})], strict=True)
    assert problems and problems[0].producer is None


def test_ordering_permits_a_name_nothing_gives_at_all():
    # A stack with no grid has no lattice for palette to contradict: doing it in the wrong order is the failure, not doing it at all.
    assert plan.unmet([N("b", needs={"x"})], strict=False) == []


def test_a_seeded_name_counts_as_given():
    assert plan.unmet([N("b", needs={"x"})], seeded=frozenset({"x"})) == []


def test_a_supplied_name_counts_as_given():
    # The run answers `rig`; the stage does not say so, and does not have to.
    assert plan.unmet([N("b", needs={"rig"})],
                      supplied=lambda n: n == "rig") == []


def test_an_optional_need_absent_altogether_is_not_a_hole():
    assert plan.unmet([N("b", optional={"x"})], strict=True) == []


def test_an_optional_need_satisfied_too_late_still_is():
    # `optional` used to be subtracted from `needs`, and no stage ever named one in both — so the subtraction never fired and an order that quietly defeated a soft input passed. Putting depth after canonical meant canonical ran with no depthmaps and said nothing.
    problems = plan.unmet([N("b", optional={"x"}), N("a", gives={"x"})],
                          strict=True)
    assert [(p.node, p.name, p.producer) for p in problems] == [("b", "x", "a")]


def test_naming_one_in_both_reads_as_optional():
    assert plan.unmet([N("b", needs={"x"}, optional={"x"})], strict=True) == []


def test_an_empty_declaration_is_a_set_not_a_tuple():
    # frozenset() is falsy, so `x or ()` quietly turned an empty declaration into a tuple and the set arithmetic raised.
    assert plan.unmet([N("a"), N("b")]) == []


def test_refuse_is_silent_when_there_is_nothing_to_refuse():
    plan.refuse([])


def test_refuse_names_every_hole_in_the_first_node_that_has_one():
    problems = plan.unmet([N("b", needs={"x", "y"}), N("a", gives={"x"})])
    with pytest.raises(Invalid) as caught:
        plan.refuse(problems, tail="\n\nReorder them.")
    message = caught.value.message
    assert "'x' is produced by 'a', which runs later" in message
    assert "'y' is produced by no configured node" in message
    assert caught.value.detail["field"] == "b"
    assert message.endswith("Reorder them.")


def test_a_caller_supplies_its_own_words_and_its_own_way_forward():
    problems = plan.unmet([N("b", needs={"x"}), N("a", gives={"x"})])
    with pytest.raises(Invalid) as caught:
        plan.refuse(problems, why=lambda p: f"{p.node} before {p.producer}",
                    hint=lambda p: f"move {p.node} after {p.producer}")
    assert caught.value.message == "b before a"
    assert caught.value.hint == "move b after a"
