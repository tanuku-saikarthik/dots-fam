export interface GridCell {
  x: number;
  z: number;
}

/**
 * The office floor is a simple walkable grid. Desks occupy cells and are
 * obstacles; each desk exposes a "stand point" (an adjacent open cell) that
 * agents path to instead of the desk cell itself.
 */
export class Grid {
  width: number;
  depth: number;
  private blocked = new Set<string>();

  constructor(width: number, depth: number) {
    this.width = width;
    this.depth = depth;
  }

  private key(x: number, z: number) {
    return `${x},${z}`;
  }

  block(x: number, z: number) {
    this.blocked.add(this.key(x, z));
  }

  isBlocked(x: number, z: number) {
    if (x < 0 || z < 0 || x >= this.width || z >= this.depth) return true;
    return this.blocked.has(this.key(x, z));
  }

  neighbors(cell: GridCell): GridCell[] {
    const candidates = [
      { x: cell.x + 1, z: cell.z },
      { x: cell.x - 1, z: cell.z },
      { x: cell.x, z: cell.z + 1 },
      { x: cell.x, z: cell.z - 1 },
    ];
    return candidates.filter((c) => !this.isBlocked(c.x, c.z));
  }
}
