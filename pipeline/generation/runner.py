

from __future__ import annotations


import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..shared import cooling
from ..shared import plan as plan_mod
from .stage import Context, Resource, Stage, get
from ..shared.errors import Invalid


class PipelineError(Invalid):
    """A stage order, or a stage, that cannot work as declared."""


@dataclass
class Batch:
    """Stages that may run together. Length 1 for anything GPU-bound."""

    stages: list[Stage]

    @property
    def parallel(self) -> bool:
        return len(self.stages) > 1


def build(order: list[str]) -> list[Stage]:
    return [get(name)() for name in order]


def validate(stages: list[Stage], seeded: set[str]) -> None:
    from . import resources

    plan_mod.refuse(
        plan_mod.unmet(stages, seeded=frozenset(seeded),
                       supplied=lambda name: name in resources.RESOLVERS,
                       strict=True),
        error=PipelineError,
        tail="\n\nReorder `pipeline.stages` in your config, add the missing "
             "producer, or declare a resolver in generation/resources.py.")


def plan(stages: list[Stage]) -> list[Batch]:
    """Group consecutive independent CPU stages into concurrent batches."""
    batches: list[Batch] = []
    current: list[Stage] = []

    def flush() -> None:
        if current:
            batches.append(Batch(list(current)))
            current.clear()

    for stage in stages:
        if stage.resource not in Resource.PARALLELISABLE:
            flush()
            batches.append(Batch([stage]))
            continue

        conflict = any(
            stage.needs & other.gives or other.needs & stage.gives
            for other in current
        )
        if conflict:
            flush()
        current.append(stage)

    flush()
    return batches


def describe(stages: list[Stage]) -> str:
    lines = ["execution plan:"]
    for i, batch in enumerate(plan(stages), 1):
        tag = f"batch {i}" + (" (parallel)" if batch.parallel else "")
        lines.append(f"  {tag}")
        for stage in batch.stages:
            lines.append(f"    {stage.describe()}")
    return "\n".join(lines)


def _check_gate(stages: list[Stage], stop_after: str | None) -> None:
    if stop_after and stop_after not in {s.name for s in stages}:
        raise PipelineError(
            f"stop_after '{stop_after}' is not in this pipeline. Stages: "
            + ", ".join(s.name for s in stages)
        )


def _gpu_batches_ahead(batches: list, stop_after: str | None) -> int:
    """How many GPU batches will actually run, so cooling knows which is last."""
    executed = batches
    if stop_after:
        for i, b in enumerate(batches):
            if stop_after in [s.name for s in b.stages]:
                executed = batches[: i + 1]
                break
    return sum(1 for b in executed
               if any(s.resource == Resource.GPU for s in b.stages))


def _run_batch(batch, ctx: Context, verbose: bool) -> None:
    """One batch, in parallel or alone. Artifacts land only once every stage is done."""
    if not batch.parallel:
        stage = batch.stages[0]
        if verbose:
            print(f"\n== {stage.name} ==")
        ctx.artifacts.update(_one(stage, ctx, verbose))
        ctx.completed.append(stage.name)
        return

    if verbose:
        print(f"\n== {', '.join(s.name for s in batch.stages)} (parallel) ==")
    with ThreadPoolExecutor(max_workers=len(batch.stages)) as pool:
        futures = {pool.submit(_one, s, ctx, verbose): s for s in batch.stages}
        results = [(futures[fut], fut.result()) for fut in futures]
    for stage, produced in results:
        ctx.artifacts.update(produced)
        ctx.completed.append(stage.name)


def run(
    stages: list[Stage],
    ctx: Context,
    verbose: bool = True,
    stop_after: str | None = None,
    skip: set[str] | None = None,
) -> Context:
    skip = skip or set()
    _check_gate(stages, stop_after)

    validate(stages, seeded=set(ctx.artifacts))
    batches = plan([s for s in stages if s.name not in skip])
    started = time.time()
    ctx.completed = list(skip)
    gpu_left = _gpu_batches_ahead(batches, stop_after)

    if skip and verbose:
        print(f"resuming — skipping completed: {', '.join(sorted(skip))}")

    for batch in batches:
        _run_batch(batch, ctx, verbose)

        if any(s.resource == Resource.GPU for s in batch.stages):
            gpu_left -= 1
            cooling.rest(
                ctx.config,
                after=", ".join(s.name for s in batch.stages),
                last=gpu_left <= 0,
                report=print if verbose else (lambda _m: None),
            )

        if stop_after and stop_after in [s.name for s in batch.stages]:
            ctx.stopped_at = stop_after
            if verbose:
                remaining = [
                    s.name for s in stages
                    if s.name not in ctx.completed and s.name not in skip
                ]
                print(
                    f"\nstopped after '{stop_after}' as configured"
                    + (f" — remaining: {', '.join(remaining)}" if remaining else "")
                )
                print(f"pipeline ran {time.time() - started:.1f}s")
            return ctx

    if verbose:
        print(f"\npipeline finished in {time.time() - started:.1f}s")
    return ctx


def _one(stage: Stage, ctx: Context, verbose: bool) -> dict[str, Any]:
    t0 = time.time()
    prep = stage.prepare(ctx) or {}
    if verbose and prep:
        print(f"   prepared in {time.time() - t0:.1f}s: {', '.join(sorted(prep))}")
    produced = stage.run(ctx, prep) or {}

    wrong = plan_mod.undeclared(stage.name, produced, stage.gives,
                                required=stage.gives)
    if wrong:
        raise PipelineError(wrong)

    if verbose:
        print(f"   {stage.name} done in {time.time() - t0:.1f}s")
    return produced
