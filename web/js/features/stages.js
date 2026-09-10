// Whether a stage order can work; the twin is pipeline/generation/runner.py.
export function orderProblems(active, stages, resources = []) {
  const meta = Object.fromEntries(stages.map((s) => [s.name, s]));
  const producers = {};
  for (const name of active) for (const p of meta[name]?.gives || []) producers[p] = name;

  const supplied = new Set(resources);
  const have = new Set();
  const problems = [];
  for (const name of active) {
    const soft = new Set(meta[name]?.optional || []);
    const hard = (meta[name]?.needs || []).filter((n) => !soft.has(n));
    for (const need of [...hard, ...soft]) {
      if (have.has(need) || supplied.has(need)) continue;
      const owner = producers[need];
      if (owner) {
        problems.push(`${name} needs "${need}" from ${owner}, which runs later`);
      } else if (!soft.has(need)) {
        problems.push(`${name} needs "${need}", which no enabled stage produces`);
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
      /* Soft needs order too, or Auto-order proposes what validate refuses. */
      const deps = [...(meta[name]?.needs || []), ...(meta[name]?.optional || [])]
        .map((r) => producers[r]).filter((d) => d && d !== name);
      if (deps.every((d) => placed.has(d))) { out.push(name); placed.add(name); }
    }
  }
  return [...out, ...active.filter((s) => !placed.has(s))];  // cycles keep their slot
}
