import { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import type { CatmullRomCurve3 } from 'three';
import { useOfficeStore } from '../store/officeStore';
import type { GridCell } from '../nav/grid';
import type { DeskSpec } from '../nav/layout';
import type { AgentId } from '../events/types';
import { queueProgress } from '../events/queue';
import { easeInOutSine, easeOutExpo } from '../agents/animations';
import { PathLine } from './PathLine';

const STATUS_COLOR: Record<string, string> = {
  working: '#3ddc84',
  waiting: '#f0b14a',
  handing_off: '#5b8fff',
  idle: '#9aa0b0',
  standing_up: '#9aa0b0',
  sitting_down: '#9aa0b0',
  walking: '#9aa0b0',
};

const SHELL = '#f6f6f9';
const SHELL_SHADOW = '#dfe1ea';
const VISOR = '#2a2d38';

export function Agent({
  id,
  desk,
  color,
  deskByAgent,
  toWorld,
}: {
  id: AgentId;
  desk: DeskSpec;
  color: string;
  deskByAgent: Record<AgentId, DeskSpec>;
  toWorld: (cell: GridCell) => [number, number];
}) {
  const group = useRef<THREE.Group>(null);
  const bodyY = useRef<THREE.Group>(null);
  const eyeL = useRef<THREE.Mesh>(null);
  const eyeR = useRef<THREE.Mesh>(null);
  const chestMat = useRef<THREE.MeshStandardMaterial>(null);
  const folder = useRef<THREE.Group>(null);
  const pulse = useRef<THREE.Mesh>(null);
  const armL = useRef<THREE.Group>(null);
  const armR = useRef<THREE.Group>(null);
  const legL = useRef<THREE.Group>(null);
  const legR = useRef<THREE.Group>(null);
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
      bodyY.current.position.y = standHeight * 0.1 + breathe + walkBob;
    }

    // walk cycle: legs and arms swing in counter-phase; otherwise ease back to rest
    const walking = phase === 'walking';
    const stride = walking ? Math.sin(et * 9) * 0.55 : 0;
    const easeTo = (ref: React.RefObject<THREE.Group | null>, x: number, speed = 0.18) => {
      if (ref.current) ref.current.rotation.x = THREE.MathUtils.lerp(ref.current.rotation.x, x, speed);
    };
    easeTo(legL, stride, walking ? 0.4 : 0.15);
    easeTo(legR, -stride, walking ? 0.4 : 0.15);
    easeTo(armL, -stride * 0.7, walking ? 0.4 : 0.15);
    easeTo(armR, stride * 0.7, walking ? 0.4 : 0.15);

    // reach forward during a handoff
    const reaching = phase === 'handing_off';
    if (armL.current) armL.current.rotation.z = THREE.MathUtils.lerp(armL.current.rotation.z, reaching ? -0.55 : 0.08, 0.15);
    if (armR.current) armR.current.rotation.z = THREE.MathUtils.lerp(armR.current.rotation.z, reaching ? 0.55 : -0.08, 0.15);

    // eyes glow brighter while working/handing off, in the Dot's own color
    [eyeL, eyeR].forEach((eye) => {
      if (!eye.current) return;
      const mat = eye.current.material as THREE.MeshStandardMaterial;
      const targetGlow = phase === 'working' || phase === 'handing_off' ? 1.3 : phase === 'waiting' ? 0.8 : 0.55;
      mat.emissiveIntensity = THREE.MathUtils.lerp(mat.emissiveIntensity, targetGlow, 0.1);
    });

    // chest status light
    if (chestMat.current) {
      const target = new THREE.Color(STATUS_COLOR[phase] ?? '#9aa0b0');
      chestMat.current.color.lerp(target, 0.15);
      chestMat.current.emissive.lerp(target, 0.15);
    }

    // carried folder: visible while walking with a payload, or during handoff
    const carrying =
      (phase === 'walking' && clip?.data.phase === 'walking' && clip.data.carrying) ||
      (phase === 'handing_off' && clip?.data.phase === 'handing_off' ? clip.data.taskId : null);
    if (folder.current) {
      folder.current.visible = !!carrying;
      if (carrying) folder.current.position.y = 0.56 + Math.sin(et * 6) * 0.01;
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
            {/* legs, pivoted at the hip */}
            <group ref={legL} position={[0.075, 0.48, 0]}>
              <mesh position={[0, -0.17, 0]} castShadow>
                <capsuleGeometry args={[0.044, 0.26, 4, 8]} />
                <meshStandardMaterial color={SHELL_SHADOW} roughness={0.6} />
              </mesh>
              <RoundedBox args={[0.1, 0.045, 0.13]} radius={0.02} position={[0, -0.32, 0.02]} castShadow>
                <meshStandardMaterial color="#babecb" roughness={0.6} />
              </RoundedBox>
            </group>
            <group ref={legR} position={[-0.075, 0.48, 0]}>
              <mesh position={[0, -0.17, 0]} castShadow>
                <capsuleGeometry args={[0.044, 0.26, 4, 8]} />
                <meshStandardMaterial color={SHELL_SHADOW} roughness={0.6} />
              </mesh>
              <RoundedBox args={[0.1, 0.045, 0.13]} radius={0.02} position={[0, -0.32, 0.02]} castShadow>
                <meshStandardMaterial color="#babecb" roughness={0.6} />
              </RoundedBox>
            </group>

            {/* torso */}
            <mesh position={[0, 0.63, 0]} castShadow>
              <capsuleGeometry args={[0.145, 0.22, 6, 14]} />
              <meshStandardMaterial color={SHELL} roughness={0.55} metalness={0.04} />
            </mesh>
            {/* chest status disc */}
            <mesh position={[0, 0.66, 0.145]} rotation={[0.15, 0, 0]}>
              <circleGeometry args={[0.052, 20]} />
              <meshStandardMaterial ref={chestMat} color="#9aa0b0" emissive="#9aa0b0" emissiveIntensity={0.9} roughness={0.3} />
            </mesh>
            <mesh position={[0, 0.66, 0.148]} rotation={[0.15, 0, 0]}>
              <ringGeometry args={[0.055, 0.066, 20]} />
              <meshStandardMaterial color="#d7d9e2" roughness={0.6} />
            </mesh>

            {/* arms, pivoted at the shoulder */}
            <group ref={armL} position={[0.185, 0.76, 0]}>
              <mesh position={[0, -0.12, 0]} castShadow>
                <capsuleGeometry args={[0.037, 0.2, 4, 8]} />
                <meshStandardMaterial color={SHELL} roughness={0.55} />
              </mesh>
              <mesh position={[0, -0.24, 0]} castShadow>
                <sphereGeometry args={[0.042, 12, 12]} />
                <meshStandardMaterial color={SHELL_SHADOW} roughness={0.5} />
              </mesh>
            </group>
            <group ref={armR} position={[-0.185, 0.76, 0]}>
              <mesh position={[0, -0.12, 0]} castShadow>
                <capsuleGeometry args={[0.037, 0.2, 4, 8]} />
                <meshStandardMaterial color={SHELL} roughness={0.55} />
              </mesh>
              <mesh position={[0, -0.24, 0]} castShadow>
                <sphereGeometry args={[0.042, 12, 12]} />
                <meshStandardMaterial color={SHELL_SHADOW} roughness={0.5} />
              </mesh>
            </group>

            {/* neck */}
            <mesh position={[0, 0.83, 0]}>
              <cylinderGeometry args={[0.055, 0.065, 0.05, 12]} />
              <meshStandardMaterial color={SHELL_SHADOW} roughness={0.6} />
            </mesh>

            {/* head */}
            <mesh position={[0, 0.95, 0]} castShadow>
              <sphereGeometry args={[0.135, 24, 24]} />
              <meshStandardMaterial color={SHELL} roughness={0.5} metalness={0.03} />
            </mesh>
            {/* visor plate */}
            <RoundedBox args={[0.17, 0.1, 0.03]} radius={0.045} position={[0, 0.955, 0.1]} castShadow>
              <meshStandardMaterial color={VISOR} roughness={0.35} metalness={0.2} />
            </RoundedBox>
            {/* eyes */}
            <mesh ref={eyeL} position={[0.04, 0.955, 0.117]}>
              <capsuleGeometry args={[0.014, 0.02, 4, 8]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} roughness={0.2} />
            </mesh>
            <mesh ref={eyeR} position={[-0.04, 0.955, 0.117]}>
              <capsuleGeometry args={[0.014, 0.02, 4, 8]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} roughness={0.2} />
            </mesh>
            {/* little antenna, for personality */}
            <mesh position={[0, 1.1, 0]}>
              <cylinderGeometry args={[0.006, 0.006, 0.07, 6]} />
              <meshStandardMaterial color={SHELL_SHADOW} roughness={0.6} />
            </mesh>
            <mesh position={[0, 1.14, 0]}>
              <sphereGeometry args={[0.016, 10, 10]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.5} />
            </mesh>
          </group>

          {/* carried folder */}
          <group ref={folder} position={[0.16, 0.56, 0.13]} visible={false}>
            <RoundedBox args={[0.14, 0.1, 0.015]} radius={0.015}>
              <meshStandardMaterial color="#ffd166" emissive="#ffb703" emissiveIntensity={0.9} transparent opacity={0.92} />
            </RoundedBox>
          </group>
          {/* handoff pulse ring */}
          <mesh ref={pulse} position={[0, 0.6, 0]} rotation={[Math.PI / 2, 0, 0]} visible={false}>
            <ringGeometry args={[0.2, 0.26, 24]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1} transparent opacity={0.6} />
          </mesh>
        </group>
        {selected && (
          <mesh position={[0, 0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.32, 0.38, 32]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.8} transparent opacity={0.7} />
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
