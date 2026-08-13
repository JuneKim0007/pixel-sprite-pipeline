

from __future__ import annotations


import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..orchestration import cooling
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
    """Check the configured order can actually satisfy every dependency."""
    available = set(seeded)
    producers = {p: s.name for s in stages for p in s.produces}

    for stage in stages:
        missing = stage.requires - available
        if missing:
            details = []
            for key in sorted(missing):
                owner = producers.get(key)
                if owner:
                    details.append(f"'{key}' is produced by '{owner}', which runs later")
                else:
                    details.append(f"'{key}' is produced by no configured stage")
            raise PipelineError(
                f"stage '{stage.name}' cannot run in this order:\n  "
                + "\n  ".join(details)
                + "\n\nReorder `pipeline.stages` in your config, or add the "
                  "missing producer."
            )
        available |= stage.produces


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

        # Only join the open batch if it neither feeds nor is fed by it.
        conflict = any(
            stage.requires & other.produces or other.requires & stage.produces
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


def run(
    stages: list[Stage],
    ctx: Context,
    verbose: bool = True,
    stop_after: str | None = None,
    skip: set[str] | None = None,
) -> Context:
    """Execute the plan, optionally stopping after a named stage.

    `stop_after` is the gate: it exists so a human can inspect or edit what a
    stage produced — the skeletons, in the rig editor — before the expensive
    GPU stages consume them. `skip` is its other half, letting a resumed run
    step over stages whose output already exists.
    """
    skip = skip or set()
    if stop_after and stop_after not in {s.name for s in stages}:
        raise PipelineError(
            f"stop_after '{stop_after}' is not in this pipeline. Stages: "
            + ", ".join(s.name for s in stages)
        )

    validate(stages, seeded=set(ctx.artifacts))
    batches = plan([s for s in stages if s.name not in skip])
    started = time.time()
    ctx.completed = list(skip)

    # Rest between GPU stages so a long queue does not hold the machine at its
    # throttle point all night. Counted up front so the last one can be skipped
    # - nothing follows it, so it protects nothing and only holds the queue.
    #
    # "Last" has to mean the last one that will ACTUALLY RUN, not the last in
    # the plan. A gated run stops early, and counting the whole plan made the
    # stage before the gate look like it had work after it: `stop_after:
    # canonical` slept three minutes and then returned, which is the exact
    # opposite of what gating is for - you gate in order to look at something
    # quickly.
    executed = batches
    if stop_after:
        for i, b in enumerate(batches):
            if stop_after in [s.name for s in b.stages]:
                executed = batches[: i + 1]
                break
    gpu_left = sum(1 for b in executed
                   if any(s.resource == Resource.GPU for s in b.stages))

    if skip and verbose:
        print(f"resuming — skipping completed: {', '.join(sorted(skip))}")

    for batch in batches:
        if batch.parallel:
            if verbose:
                names = ", ".join(s.name for s in batch.stages)
                print(f"\n== {names} (parallel) ==")
            with ThreadPoolExecutor(max_workers=len(batch.stages)) as pool:
                futures = {pool.submit(_one, s, ctx, verbose): s for s in batch.stages}
                results: list[tuple[Stage, dict[str, Any]]] = []
                for fut in futures:
                    results.append((futures[fut], fut.result()))
            # Merge after the batch so concurrent stages never observe each
            # other's partial writes.
            for stage, produced in results:
                ctx.artifacts.update(produced)
                ctx.completed.append(stage.name)
        else:
            stage = batch.stages[0]
            if verbose:
                print(f"\n== {stage.name} ==")
            ctx.artifacts.update(_one(stage, ctx, verbose))
            ctx.completed.append(stage.name)

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
    produced = stage.run(ctx) or {}

    unexpected = set(produced) - set(stage.produces)
    if unexpected:
        raise PipelineError(
            f"stage '{stage.name}' returned undeclared artifacts: "
            f"{sorted(unexpected)}. Add them to `produces` so ordering stays "
            f"checkable."
        )
    withheld = set(stage.produces) - set(produced)
    if withheld:
        raise PipelineError(
            f"stage '{stage.name}' declared {sorted(withheld)} but did not "
            f"return them."
        )

    if verbose:
        print(f"   {stage.name} done in {time.time() - t0:.1f}s")
    return produced
