import { CatmullRomCurve3, Vector3 } from 'three';
import type { GridCell } from '../nav/grid';

const REDUCED_MOTION =
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export const WALK_SPEED = REDUCED_MOTION ? 5.2 : 2.4; // world units / second

/** Turns a grid-cell path into a smooth Catmull-Rom curve agents walk along. */
export function pathToCurve(cells: GridCell[], toWorld: (cell: GridCell) => [number, number]): CatmullRomCurve3 | null {
  if (cells.length < 2) return null;
  const points = cells.map((cell) => {
    const [x, z] = toWorld(cell);
    return new Vector3(x, 0, z);
  });
  return new CatmullRomCurve3(points, false, 'catmullrom', 0.3);
}

export function curveLength(curve: CatmullRomCurve3) {
  return curve.getLength();
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export function clamp01(t: number) {
  return Math.max(0, Math.min(1, t));
}

/** Ease-out for snappy, decisive motion (stand/sit/handoff beats) rather than linear. */
export function easeOutExpo(t: number) {
  return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
}

export function easeInOutSine(t: number) {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}
