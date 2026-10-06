import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useOfficeStore } from '../store/officeStore';
import type { AgentId } from '../events/types';

/** A floating translucent screen above a desk: dim when idle, animated "scrolling lines" when working. */
export function HoloScreen({
  agentId,
  color,
  position,
  rotationY = 0,
}: {
  agentId: AgentId | null;
  color: string;
  position: [number, number, number];
  rotationY?: number;
}) {
  const group = useRef<THREE.Group>(null);
  const screenMat = useRef<THREE.MeshStandardMaterial>(null);
  const lineRefs = useRef<THREE.Mesh[]>([]);
  const linesGroup = useRef<THREE.Group>(null);
  const lines = useMemo(() => Array.from({ length: 4 }, (_, i) => i), []);

  useFrame((state) => {
    if (group.current) {
      group.current.position.y = position[1] + Math.sin(state.clock.elapsedTime * 1.6) * 0.015;
    }
    const active = agentId ? useOfficeStore.getState().agents[agentId]?.phase === 'working' : false;
    if (screenMat.current) {
      screenMat.current.emissiveIntensity = THREE.MathUtils.lerp(screenMat.current.emissiveIntensity, active ? 0.85 : 0.18, 0.1);
      screenMat.current.opacity = THREE.MathUtils.lerp(screenMat.current.opacity, active ? 0.5 : 0.22, 0.1);
    }
    if (linesGroup.current) linesGroup.current.visible = active;
    if (active) {
      lineRefs.current.forEach((mesh, i) => {
        if (!mesh) return;
        const t = (state.clock.elapsedTime * 0.6 + i * 0.37) % 1;
        mesh.scale.x = 0.3 + Math.sin(t * Math.PI * 2 + i) * 0.22 + 0.3;
      });
    }
  });

  return (
    <group ref={group} position={position} rotation={[0, rotationY, 0]}>
      <mesh>
        <planeGeometry args={[0.52, 0.34]} />
        <meshStandardMaterial
          ref={screenMat}
          color={color}
          emissive={color}
          emissiveIntensity={0.18}
          transparent
          opacity={0.22}
          side={THREE.DoubleSide}
        />
      </mesh>
      <group ref={linesGroup} visible={false}>
        {lines.map((i) => (
          <mesh
            key={i}
            ref={(el) => {
              if (el) lineRefs.current[i] = el;
            }}
            position={[-0.14, 0.11 - i * 0.07, 0.002]}
          >
            <planeGeometry args={[0.3, 0.025]} />
            <meshStandardMaterial color="#ffffff" emissive="#ffffff" emissiveIntensity={1} transparent opacity={0.85} />
          </mesh>
        ))}
      </group>
    </group>
  );
}
