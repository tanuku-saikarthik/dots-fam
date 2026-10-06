import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Line } from '@react-three/drei';
import * as THREE from 'three';
import type { GridCell } from '../nav/grid';
import type { DeskSpec } from '../nav/layout';
import type { AgentId } from '../events/types';

const DURATION_MS = 900;

/** A glowing arc beam from the Coordinator's desk to a worker's desk, with a task card riding along it. */
export function AssignBeam({
  to,
  bornAt,
  color,
  coordinatorDesk,
  targetDesk,
  toWorld,
}: {
  to: AgentId;
  bornAt: number;
  color: string;
  coordinatorDesk: DeskSpec;
  targetDesk: DeskSpec;
  toWorld: (cell: GridCell) => [number, number];
}) {
  const card = useRef<THREE.Mesh>(null);

  const curve = useMemo(() => {
    const [cx, cz] = toWorld(coordinatorDesk.cell);
    const [wx, wz] = toWorld(targetDesk.cell);
    const mid = new THREE.Vector3((cx + wx) / 2, 1.9, (cz + wz) / 2);
    return new THREE.QuadraticBezierCurve3(new THREE.Vector3(cx, 0.9, cz), mid, new THREE.Vector3(wx, 0.9, wz));
  }, [coordinatorDesk, targetDesk, toWorld]);

  const points = useMemo(() => curve.getPoints(24), [curve]);
  void to;

  useFrame(() => {
    const t = Math.min(1, (Date.now() - bornAt) / DURATION_MS);
    const point = curve.getPoint(t);
    card.current?.position.copy(point);
    card.current?.lookAt(point.clone().add(new THREE.Vector3(0, 0, 1)));
  });

  return (
    <group>
      <Line points={points} color={color} lineWidth={1.6} transparent opacity={0.45} />
      <mesh ref={card}>
        <planeGeometry args={[0.16, 0.11]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.1} transparent opacity={0.95} />
      </mesh>
    </group>
  );
}
