import { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import type { CatmullRomCurve3 } from 'three';
import { useOfficeStore } from '../store/officeStore';
import type { GridCell } from '../nav/grid';
import type { DeskSpec } from '../nav/layout';
import type { AgentId } from '../events/types';
import { queueProgress } from '../events/queue';
import { easeInOutSine, easeOutExpo } from '../agents/animations';
import { PathLine } from './PathLine';



export function Agent({
  id,
  desk,
  color,
  deskByAgent,
  toWorld,
  showLabel = true,
}: {
  id: AgentId;
  showLabel?: boolean;
  desk: DeskSpec;
  color: string;
  deskByAgent: Record<AgentId, DeskSpec>;
  toWorld: (cell: GridCell) => [number, number];
}) {
  const group = useRef<THREE.Group>(null);
  const bodyY = useRef<THREE.Group>(null);
  const eyeL = useRef<THREE.Mesh>(null);
  const eyeR = useRef<THREE.Mesh>(null);
  const chestMat = useRef<THREE.MeshStandardMaterial>(null); // orb material
  const folder = useRef<THREE.Group>(null);
  const pulse = useRef<THREE.Mesh>(null);
  const prevPhase = useRef<string>('idle');
  const pulseStart = useRef<number | null>(null);
  const [activeCurve, setActiveCurve] = useState<CatmullRomCurve3 | null>(null);
  const lastCurve = useRef<CatmullRomCurve3 | null>(null);

  const selected = useOfficeStore((s) => s.selectedAgent === id);
  const select = useOfficeStore((s) => s.selectAgent);

  useFrame((state) => {
    const agent = useOfficeStore.getState().agents[id];
    if (!agent || !group.current) return;
    const clip = agent.queue.current;
    const phase = agent.phase;
    const t = easeInOutSine(queueProgress(agent.queue));
    const et = state.clock.elapsedTime;

    let pos = new THREE.Vector3();
    let facing = desk.facing;

    if (phase === 'walking' && clip?.data.phase === 'walking') {
      const curve = clip.data.curve;
      pos = curve.getPoint(t);
      const tangent = curve.getTangent(Math.min(0.98, Math.max(0.02, t)));
      facing = Math.atan2(tangent.x, tangent.z);
      if (lastCurve.current !== curve) {
        lastCurve.current = curve;
        setActiveCurve(curve);
      }
    } else {
      const [x, z] = toWorld(agent.cellPos);
      pos.set(x, 0, z);
      // At its own desk, a Dot sits right behind the desk rather than out in the aisle.
      const atDesk = agent.cellPos.x === desk.standPoint.x && agent.cellPos.z === desk.standPoint.z;
      if (atDesk && phase !== 'handing_off') {
        const [dx, dz] = toWorld(desk.cell);
        pos.set(x + (dx - x) * 0.42, 0, z + (dz - z) * 0.42);
      }
      if (phase === 'handing_off' && clip?.data.phase === 'handing_off') {
        const partnerDesk = deskByAgent[clip.data.withAgent];
        if (partnerDesk) {
          const [px, pz] = toWorld(partnerDesk.standPoint);
          facing = Math.atan2(px - pos.x, pz - pos.z);
        }
      } else {
        facing = desk.facing;
      }
    }

    group.current.position.lerp(pos, phase === 'walking' ? 1 : 0.22);
    const targetQuat = new THREE.Quaternion().setFromEuler(new THREE.Euler(0, facing, 0));
    group.current.quaternion.slerp(targetQuat, 0.18);

    // seated / standing height
    const seated = phase === 'idle' || phase === 'working';
    const standHeight = seated ? 0 : phase === 'standing_up' ? easeOutExpo(t) : phase === 'sitting_down' ? 1 - easeOutExpo(t) : 1;
    if (bodyY.current) {
      const breathe = Math.sin(et * 1.8 + id.length) * 0.012;
      const walkBob = phase === 'walking' ? Math.abs(Math.sin(et * 9)) * 0.045 : 0;
      bodyY.current.position.y = standHeight * 0.12 + breathe * 2 + walkBob;
    }

    // orb: pulses while working, amber halo while waiting, steady when idle
    const working = phase === 'working' || phase === 'handing_off';
    if (chestMat.current) {
      const glow = working ? 0.75 + Math.sin(et * 3 + id.length) * 0.25 : phase === 'waiting' ? 0.5 : 0.3;
      chestMat.current.emissiveIntensity = THREE.MathUtils.lerp(chestMat.current.emissiveIntensity, glow, 0.12);
    }
    if (eyeL.current) {
      const halo = eyeL.current.material as THREE.MeshBasicMaterial;
      halo.color.set(phase === 'waiting' ? '#f5a524' : color);
      halo.opacity = THREE.MathUtils.lerp(halo.opacity, phase === 'waiting' ? 0.35 : working ? 0.22 : 0.08, 0.1);
      eyeL.current.scale.setScalar(1 + (working ? Math.sin(et * 3) * 0.06 : 0));
    }
    if (eyeR.current) {
      const pool = eyeR.current.material as THREE.MeshBasicMaterial;
      pool.opacity = THREE.MathUtils.lerp(pool.opacity, working ? 0.28 : 0.1, 0.1);
    }

    // carried folder: visible while walking with a payload, or during handoff
    const carrying =
      (phase === 'walking' && clip?.data.phase === 'walking' && clip.data.carrying) ||
      (phase === 'handing_off' && clip?.data.phase === 'handing_off' ? clip.data.taskId : null);
    if (folder.current) {
      folder.current.visible = !!carrying;
      if (carrying) folder.current.position.y = 1.0 + Math.sin(et * 6) * 0.015;
    }

    // handoff particle pulse
    if (phase === 'handing_off' && prevPhase.current !== 'handing_off') pulseStart.current = et;
    prevPhase.current = phase;
    if (pulse.current) {
      if (pulseStart.current !== null) {
        const age = et - pulseStart.current;
        if (age < 0.5) {
          pulse.current.visible = true;
          const s = 0.2 + age * 2.2;
          pulse.current.scale.setScalar(s);
          const mat = pulse.current.material as THREE.MeshStandardMaterial;
          mat.opacity = Math.max(0, 0.8 - age * 1.8);
        } else {
          pulse.current.visible = false;
          pulseStart.current = null;
        }
      } else {
        pulse.current.visible = false;
      }
    }
  });

  return (
    <>
      <group ref={group}>
        <group ref={bodyY}>
          <group onClick={(e) => (e.stopPropagation(), select(id))} onPointerOver={(e) => e.stopPropagation()}>
            {/* the Dot itself: a glossy orb in its own color, with a bright core while it works */}
            <mesh position={[0, 0.78, 0]} castShadow>
              <sphereGeometry args={[0.28, 48, 48]} />
              <meshStandardMaterial ref={chestMat} color={color} emissive={color} emissiveIntensity={0.35} roughness={0.25} metalness={0.1} />
            </mesh>
            <mesh ref={eyeL} position={[0, 0.78, 0]}>
              <sphereGeometry args={[0.36, 32, 32]} />
              <meshBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
            </mesh>
            <mesh ref={eyeR} position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]} renderOrder={2}>
              <circleGeometry args={[0.34, 40]} />
              <meshBasicMaterial color={color} transparent opacity={0.22} depthWrite={false} />
            </mesh>
            {showLabel && (
              <Html position={[0, 1.22, 0]} center zIndexRange={[20, 0]} pointerEvents="none">
                <AgentLabel id={id} />
              </Html>
            )}
          </group>

          {/* carried brief */}
          <group ref={folder} position={[0.22, 0.98, 0.1]} visible={false}>
            <RoundedBox args={[0.2, 0.14, 0.02]} radius={0.02}>
              <meshStandardMaterial color="#fff3d6" emissive="#ffb703" emissiveIntensity={0.8} />
            </RoundedBox>
          </group>
          {/* handoff pulse ring */}
          <mesh ref={pulse} position={[0, 0.78, 0]} rotation={[Math.PI / 2, 0, 0]} visible={false}>
            <ringGeometry args={[0.2, 0.26, 32]} />
            <meshBasicMaterial color={color} transparent opacity={0.6} />
          </mesh>
        </group>
        {selected && (
          <mesh position={[0, 0.015, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.46, 0.52, 48]} />
            <meshBasicMaterial color={color} transparent opacity={0.8} />
          </mesh>
        )}
      </group>
      {activeCurve && (
        <PathLine
          curve={activeCurve}
          isActive={() => {
            const a = useOfficeStore.getState().agents[id];
            return a.phase === 'walking' && a.queue.current?.data.phase === 'walking' && a.queue.current.data.curve === activeCurve;
          }}
          color={color}
          onFaded={() => setActiveCurve(null)}
        />
      )}
    </>
  );
}

const PHASE_TEXT: Record<string, string> = {
  working: 'Working',
  waiting: 'Needs you',
  handing_off: 'Handing off',
  walking: 'On the move',
  standing_up: 'On the move',
  sitting_down: 'Back at desk',
};

/** Name and state above a Dot, as crisp DOM text rather than 3D text. */
export function AgentLabel({ id }: { id: AgentId }) {
  useOfficeStore((s) => s.tick);
  const { agents, dotMeta } = useOfficeStore.getState();
  const phase = agents[id]?.phase ?? 'idle';
  const state = PHASE_TEXT[phase];
  return (
    <div className={`office-label phase-${phase}`}>
      <strong>{dotMeta[id]?.name ?? id}</strong>
      {state && <span>{state}</span>}
    </div>
  );
}
