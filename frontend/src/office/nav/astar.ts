import type { Grid, GridCell } from './grid';

function key(c: GridCell) {
  return `${c.x},${c.z}`;
}

function heuristic(a: GridCell, b: GridCell) {
  return Math.abs(a.x - b.x) + Math.abs(a.z - b.z);
}

/** Simple grid A*. Returns a cell path from start to goal inclusive, or [] if unreachable. */
export function findPath(grid: Grid, start: GridCell, goal: GridCell): GridCell[] {
  if (grid.isBlocked(goal.x, goal.z)) return [];

  const open = new Map<string, GridCell>([[key(start), start]]);
  const cameFrom = new Map<string, GridCell>();
  const gScore = new Map<string, number>([[key(start), 0]]);
  const fScore = new Map<string, number>([[key(start), heuristic(start, goal)]]);
  const closed = new Set<string>();

  while (open.size) {
    let currentKey = '';
    let current: GridCell | null = null;
    let best = Infinity;
    for (const [k, cell] of open) {
      const f = fScore.get(k) ?? Infinity;
      if (f < best) {
        best = f;
        current = cell;
        currentKey = k;
      }
    }
    if (!current) break;
    if (current.x === goal.x && current.z === goal.z) {
      const path: GridCell[] = [current];
      let k = currentKey;
      while (cameFrom.has(k)) {
        const prev = cameFrom.get(k)!;
        path.unshift(prev);
        k = key(prev);
      }
      return path;
    }
    open.delete(currentKey);
    closed.add(currentKey);

    for (const neighbor of grid.neighbors(current)) {
      const nk = key(neighbor);
      if (closed.has(nk)) continue;
      const tentative = (gScore.get(currentKey) ?? Infinity) + 1;
      if (tentative < (gScore.get(nk) ?? Infinity)) {
        cameFrom.set(nk, current);
        gScore.set(nk, tentative);
        fScore.set(nk, tentative + heuristic(neighbor, goal));
        if (!open.has(nk)) open.set(nk, neighbor);
      }
    }
  }
  return [];
}
