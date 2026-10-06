import { Grid, type GridCell } from './grid';
import type { AgentId } from '../events/types';

export const CELL_SIZE = 1.7;

export interface DeskSpec {
  id: AgentId;
  cell: GridCell;
  standPoint: GridCell;
  /** Which way the agent faces while seated/working, in radians. */
  facing: number;
  raised?: boolean;
}

const NORTH = Math.PI; // facing -z
const SOUTH = 0; // facing +z

export interface OfficeLayout {
  desks: DeskSpec[];
  deskByAgent: Record<AgentId, DeskSpec>;
  grid: Grid;
  width: number;
  depth: number;
}

/**
 * Lays the office out from whatever roster you give it: a raised Coordinator
 * desk up front, and the rest in up to two rows of three. Grows sideways if
 * there are more than six worker Dots.
 */
export function buildLayout(coordinatorId: AgentId, workerIds: AgentId[]): OfficeLayout {
  const perRow = Math.min(3, Math.max(1, Math.ceil(workerIds.length / 2)));
  const rows = Math.max(1, Math.ceil(workerIds.length / perRow));
  const width = Math.max(5, perRow * 3 + 1);
  const depth = 2 + rows * 3 + 1;

  const desks: DeskSpec[] = [
    {
      id: coordinatorId,
      cell: { x: Math.round(width / 2), z: 1 },
      standPoint: { x: Math.round(width / 2), z: 2 },
      facing: SOUTH,
      raised: true,
    },
  ];

  workerIds.forEach((id, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const colsInRow = Math.min(perRow, workerIds.length - row * perRow);
    const rowStartX = Math.round((width - (colsInRow * 3 - 1)) / 2);
    const x = rowStartX + col * 3;
    const facingSouth = row % 2 === 0;
    const z = 3 + row * 3; // rows 3 cells apart, starting at z=3
    desks.push({
      id,
      cell: { x, z },
      standPoint: { x, z: facingSouth ? z + 1 : z - 1 },
      facing: facingSouth ? SOUTH : NORTH,
    });
  });

  const grid = new Grid(width, depth);
  for (const desk of desks) grid.block(desk.cell.x, desk.cell.z);

  return {
    desks,
    deskByAgent: Object.fromEntries(desks.map((d) => [d.id, d])),
    grid,
    width,
    depth,
  };
}

/** Cell -> world-space [x, z], centered on a layout of the given size. */
export function makeCellToWorld(width: number, depth: number) {
  return (cell: GridCell): [number, number] => [
    (cell.x - (width - 1) / 2) * CELL_SIZE,
    (cell.z - (depth - 1) / 2) * CELL_SIZE,
  ];
}
