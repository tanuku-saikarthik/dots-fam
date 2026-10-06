import type { CatmullRomCurve3 } from 'three';
import type { Grid, GridCell } from '../nav/grid';
import { findPath } from '../nav/astar';
import type { DeskSpec } from '../nav/layout';
import { makeCellToWorld } from '../nav/layout';
import { pathToCurve, curveLength, WALK_SPEED } from './animations';
import type { AgentId } from '../events/types';
import type { Clip } from '../events/queue';

export type AgentPhase =
  | 'idle'
  | 'working'
  | 'standing_up'
  | 'walking'
  | 'handing_off'
  | 'walking_back'
  | 'sitting_down'
  | 'waiting';

export type ClipData =
  | { phase: 'idle' }
  | { phase: 'working'; taskId: string }
  | { phase: 'standing_up' }
  | { phase: 'sitting_down' }
  | { phase: 'walking'; curve: CatmullRomCurve3; carrying: string | null; toCell: GridCell; returning?: boolean }
  | { phase: 'handing_off'; withAgent: AgentId; taskId: string; role: 'giver' | 'receiver' }
  | { phase: 'waiting'; reason?: string };

const REDUCED_MOTION =
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const SCALE = REDUCED_MOTION ? 0.45 : 1;

const STAND_MS = 420 * SCALE;
const SIT_MS = 420 * SCALE;
const HANDOFF_MS = 950 * SCALE;
const MIN_WALK_MS = 600 * SCALE;

function walkClip(
  grid: Grid,
  toWorld: (cell: GridCell) => [number, number],
  from: GridCell,
  to: GridCell,
  carrying: string | null,
  returning = false,
): Clip<ClipData> | null {
  const cells = findPath(grid, from, to);
  const curve = pathToCurve(cells, toWorld);
  if (!curve) return null;
  const meters = curveLength(curve);
  const duration = Math.max(MIN_WALK_MS, (meters / WALK_SPEED) * 1000);
  return { duration, data: { phase: 'walking', curve, carrying, toCell: to, returning } };
}

/**
 * Builds the giver's full clip sequence for a handoff (stand → walk → hand off → walk back → sit)
 * and reports how long it takes to *arrive* (stand + walk), so the receiver's wait can be timed to match.
 */
export function planHandoff(
  grid: Grid,
  from: AgentId,
  to: AgentId,
  taskId: string,
  fromCell: GridCell,
  deskByAgent: Record<AgentId, DeskSpec>,
  gridWidth: number,
  gridDepth: number,
): { giverClips: Clip<ClipData>[]; preHandoffMs: number } {
  const toWorld = makeCellToWorld(gridWidth, gridDepth);
  const toDesk = deskByAgent[to];
  const homeDesk = deskByAgent[from];
  const clips: Clip<ClipData>[] = [{ duration: STAND_MS, data: { phase: 'standing_up' } }];
  let preHandoffMs = STAND_MS;

  if (toDesk) {
    const toWork = walkClip(grid, toWorld, fromCell, toDesk.standPoint, taskId);
    if (toWork) {
      clips.push(toWork);
      preHandoffMs += toWork.duration;
    }
  }
  clips.push({ duration: HANDOFF_MS, data: { phase: 'handing_off', withAgent: to, taskId, role: 'giver' } });

  if (toDesk && homeDesk) {
    const back = walkClip(grid, toWorld, toDesk.standPoint, homeDesk.standPoint, null, true);
    if (back) clips.push(back);
  }
  clips.push({ duration: SIT_MS, data: { phase: 'sitting_down' } });

  return { giverClips: clips, preHandoffMs };
}

export function clipForReceiving(withAgent: AgentId, taskId: string): Clip<ClipData> {
  return { duration: HANDOFF_MS, data: { phase: 'handing_off', withAgent, taskId, role: 'receiver' } };
}

export function clipForWorking(taskId: string): Clip<ClipData> {
  return { duration: 100000, data: { phase: 'working', taskId } };
}

export function clipForWaiting(ms: number): Clip<ClipData> {
  return { duration: ms, data: { phase: 'waiting' } };
}

export function clipForIdle(): Clip<ClipData> {
  return { duration: 100000, data: { phase: 'idle' } };
}
