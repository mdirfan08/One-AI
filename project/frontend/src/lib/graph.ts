import type { Task } from "../types";

/** Assign each task to a layer: layer = 1 + max(layer of dependencies). Tasks in a layer can run in parallel. */
export function computeLayers(tasks: Pick<Task, "id" | "depends_on">[]): string[][] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const memo = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    if (memo.has(id)) return memo.get(id)!;
    if (visiting.has(id)) return 0; // defensive: the backend rejects cycles
    visiting.add(id);
    const deps = (byId.get(id)?.depends_on ?? []).filter((d) => byId.has(d));
    const d = deps.length ? 1 + Math.max(...deps.map(depth)) : 0;
    visiting.delete(id);
    memo.set(id, d);
    return d;
  };
  const layers: string[][] = [];
  for (const t of tasks) (layers[depth(t.id)] ||= []).push(t.id);
  return layers.map((l) => l ?? []);
}

export function money(v: number | null | undefined): string {
  return v == null ? "unknown" : `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`;
}
