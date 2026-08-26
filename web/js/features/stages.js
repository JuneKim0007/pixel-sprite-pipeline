/* Whether a stage order can work, and the nearest one that can.
 * The Python twin is pipeline/generation/runner.py's validate and build. */
export function orderProblems(active, stages, resources = []) {
  const meta = Object.fromEntries(stages.map((s) => [s.name, s]));
  const producers = {};
  for (const name of active) for (const p of meta[name]?.gives || []) producers[p] = name;

  const supplied = new Set(resources);
  const have = new Set();
  const problems = [];
  for (const name of active) {
    for (const need of meta[name]?.needs || []) {
      /* A stage declares one set of needs; the run answers some of them, so a
       * name the resolvers cover is not a missing artifact. */
      if (!have.has(need) && !supplied.has(need)) {
        const owner = producers[need];
        problems.push(owner
          ? `${name} needs "${need}" from ${owner}, which runs later`
          : `${name} needs "${need}", which no enabled stage produces`);
      }
    }
    for (const p of meta[name]?.gives || []) have.add(p);
  }
  return problems;
}

export function autoOrder(active, stages) {
  const meta = Object.fromEntries(stages.map((s) => [s.name, s]));
  const producers = {};
  for (const name of active) for (const p of meta[name]?.gives || []) producers[p] = name;

  const out = [], placed = new Set();
  let guard = active.length + 1;
  while (out.length < active.length && guard-- > 0) {
    for (const name of active) {
      if (placed.has(name)) continue;
      const deps = (meta[name]?.needs || [])
        .map((r) => producers[r]).filter((d) => d && d !== name);
      if (deps.every((d) => placed.has(d))) { out.push(name); placed.add(name); }
    }
  }
  return [...out, ...active.filter((s) => !placed.has(s))];  // cycles keep their slot
}
