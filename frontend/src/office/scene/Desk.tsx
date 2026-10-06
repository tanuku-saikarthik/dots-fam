import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import { useOfficeStore } from '../store/officeStore';
import type { GridCell } from '../nav/grid';
import type { DeskSpec } from '../nav/layout';

const TOP = '#2a2f3e';
const FRAME = '#3a4152';

/** A graphite desk whose screen lights up in the Dot's color while that Dot works. */
export function Desk({
  desk,
  toWorld,
  accent,
}: {
  desk: DeskSpec;
  toWorld: (cell: GridCell) => [number, number];
  accent: string;
}) {
  const [x, z] = toWorld(desk.cell);
  const height = desk.raised ? 0.66 : 0.56;
  const width = desk.raised ? 1.5 : 1.05;
  const depth = desk.raised ? 0.7 : 0.6;
  const away = desk.facing === 0 ? -1 : 1; // the screen sits on the far side, facing the Dot
  const screen = useRef<THREE.MeshStandardMaterial>(null);
  const edge = useRef<THREE.MeshStandardMaterial>(null);

  useFrame((state) => {
    const phase = useOfficeStore.getState().agents[desk.id]?.phase;
    const active = desk.raised || phase === 'working' || phase === 'handing_off';
    const flicker = active ? 1.4 + Math.sin(state.clock.elapsedTime * 2.2 + x) * 0.15 : 0.12;
    if (screen.current) screen.current.emissiveIntensity = THREE.MathUtils.lerp(screen.current.emissiveIntensity, flicker, 0.08);
    if (edge.current) edge.current.emissiveIntensity = THREE.MathUtils.lerp(edge.current.emissiveIntensity, active ? 0.9 : 0.25, 0.08);
  });

  return (
    <group position={[x, 0, z]} rotation={[0, desk.facing, 0]}>
      <RoundedBox args={[width, 0.05, depth]} radius={0.025} position={[0, height, 0]} castShadow receiveShadow>
        <meshStandardMaterial color={TOP} roughness={0.55} metalness={0.15} />
      </RoundedBox>
      {/* the Dot's color as a thin light strip along the front edge */}
      <mesh position={[0, height - 0.012, (depth / 2) * -away + 0.002 * away]}>
        <boxGeometry args={[width - 0.08, 0.012, 0.012]} />
        <meshStandardMaterial ref={edge} color={accent} emissive={accent} emissiveIntensity={0.25} toneMapped={false} />
      </mesh>
      {/* two slab legs read cleaner than four posts at this scale */}
      {[-1, 1].map((side) => (
        <RoundedBox key={side} args={[0.05, height, depth - 0.12]} radius={0.02} position={[(side * (width - 0.14)) / 2, height / 2, 0]} castShadow>
          <meshStandardMaterial color={FRAME} roughness={0.6} metalness={0.2} />
        </RoundedBox>
      ))}
      <group position={[0, height + 0.025, (depth / 2 - 0.12) * away]} rotation={[0, away > 0 ? Math.PI : 0, 0]}>
        <mesh position={[0, 0.05, 0]}>
          <boxGeometry args={[0.05, 0.1, 0.03]} />
          <meshStandardMaterial color={FRAME} roughness={0.6} />
        </mesh>
        <RoundedBox args={[desk.raised ? 0.7 : 0.5, 0.32, 0.03]} radius={0.015} position={[0, 0.27, 0]} castShadow>
          <meshStandardMaterial color="#141722" roughness={0.4} />
        </RoundedBox>
        <mesh position={[0, 0.27, 0.017]}>
          <planeGeometry args={[desk.raised ? 0.64 : 0.45, 0.27]} />
          <meshStandardMaterial ref={screen} color="#0b0d14" emissive={accent} emissiveIntensity={0.12} toneMapped={false} />
        </mesh>
      </group>
    </group>
  );
}
