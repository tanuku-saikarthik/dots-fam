import { useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, OrthographicCamera, ContactShadows, RoundedBox, MeshReflectorMaterial } from '@react-three/drei';
import { EffectComposer, Bloom, Vignette, N8AO } from '@react-three/postprocessing';
import * as THREE from 'three';
import { useOfficeStore } from '../store/officeStore';
import { makeCellToWorld } from '../nav/layout';
import { dotColorHex } from '../events/types';
import { Desk } from './Desk';
import { Agent } from './Agent';
import { HoloScreen } from './HoloScreen';
import { AssignBeam } from './AssignBeam';

function Floor({ width, depth }: { width: number; depth: number }) {
  const w = width * 1.7 + 1.2;
  const d = depth * 1.7 + 1.2;
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <MeshReflectorMaterial
          color="#f6f5f1"
          roughness={0.95}
          blur={[400, 140]}
          mixBlur={1}
          mixStrength={4}
          resolution={512}
          depthScale={0.1}
          minDepthThreshold={0.9}
          metalness={0}
          mirror={0}
        />
      </mesh>
      {/* a soft inset rug under the desks, for warmth and depth cueing */}
      <mesh position={[0, 0.003, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[w - 1.1, d - 1.1]} />
        <meshStandardMaterial color="#efece4" roughness={1} />
      </mesh>
      <mesh position={[0, 1.1, -d / 2]} receiveShadow castShadow>
        <boxGeometry args={[w, 2.2, 0.12]} />
        <meshStandardMaterial color="#e9e7e0" roughness={0.88} />
      </mesh>
      <mesh position={[-w / 2, 1.1, 0]} receiveShadow castShadow>
        <boxGeometry args={[0.12, 2.2, d]} />
        <meshStandardMaterial color="#e9e7e0" roughness={0.88} />
      </mesh>
      <mesh position={[w / 2, 1.1, 0]} receiveShadow castShadow>
        <boxGeometry args={[0.12, 2.2, d]} />
        <meshStandardMaterial color="#e9e7e0" roughness={0.88} />
      </mesh>
      {/* skirting accent line, a quiet touch of the brand accent */}
      <mesh position={[0, 0.03, -d / 2 + 0.07]}>
        <boxGeometry args={[w, 0.06, 0.02]} />
        <meshStandardMaterial color="#8d7bff" emissive="#8d7bff" emissiveIntensity={0.35} roughness={0.4} />
      </mesh>
      <mesh position={[-w / 2 + 0.08, 1.3, -2]} rotation={[0, Math.PI / 2, 0]}>
        <planeGeometry args={[1.6, 1]} />
        <meshStandardMaterial color="#ffffff" roughness={0.5} />
      </mesh>
      {[
        [-w / 2 + 0.6, -d / 2 + 0.6],
        [w / 2 - 0.6, -d / 2 + 0.6],
      ].map(([x, z], i) => (
        <group key={i} position={[x, 0, z]}>
          <mesh position={[0, 0.18, 0]} castShadow receiveShadow>
            <cylinderGeometry args={[0.16, 0.13, 0.36, 12]} />
            <meshStandardMaterial color="#d8d3c8" roughness={0.85} />
          </mesh>
          <mesh position={[0, 0.5, 0]} castShadow>
            <icosahedronGeometry args={[0.25, 1]} />
            <meshStandardMaterial color="#6fa05a" roughness={0.75} />
          </mesh>
          <mesh position={[0, 0.34, 0]} castShadow>
            <icosahedronGeometry args={[0.17, 1]} />
            <meshStandardMaterial color="#84b56a" roughness={0.75} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function SceneContent() {
  useOfficeStore((s) => s.tick);
  const layout = useOfficeStore((s) => s.layout);
  const coordinatorId = useOfficeStore((s) => s.coordinatorId);
  const workerIds = useOfficeStore((s) => s.workerIds);
  const dotMeta = useOfficeStore((s) => s.dotMeta);
  const stepFrame = useOfficeStore((s) => s.stepFrame);

  useFrame((_, delta) => stepFrame(delta * 1000));

  if (!layout || !coordinatorId) return null;
  const toWorld = makeCellToWorld(layout.width, layout.depth);
  const beams = useOfficeStore.getState().assignBeams;
  const coordinatorDesk = layout.deskByAgent[coordinatorId];

  return (
    <>
      <hemisphereLight args={['#fff7ea', '#cfd4e0', 0.42]} />
      <directionalLight
        position={[6, 9, 4]}
        intensity={0.85}
        color="#fff6e6"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-9}
        shadow-camera-right={9}
        shadow-camera-top={9}
        shadow-camera-bottom={-9}
        shadow-bias={-0.0005}
        shadow-radius={4}
      />
      {/* cool rim light from the back, the app's accent color, for a touch of premium contrast */}
      <directionalLight position={[-5, 4, -7]} intensity={0.22} color="#8d7bff" />
      <ambientLight intensity={0.34} />

      <Floor width={layout.width} depth={layout.depth} />
      <ContactShadows position={[0, 0.002, 0]} opacity={0.4} scale={16} blur={2.4} far={2.2} />

      {layout.desks.map((desk) => {
        const [x, z] = toWorld(desk.cell);
        const isCoordinator = desk.id === coordinatorId;
        return (
          <group key={desk.id}>
            <Desk desk={desk} toWorld={toWorld} accent={isCoordinator ? '#f5a524' : dotColorHex(dotMeta[desk.id]?.color ?? 'blue')} />
            {!isCoordinator && (
              <HoloScreen
                agentId={desk.id}
                color={dotColorHex(dotMeta[desk.id]?.color ?? 'blue')}
                position={[x, desk.raised ? 1.25 : 1.05, z - (desk.facing === 0 ? 0.2 : -0.2)]}
                rotationY={desk.facing}
              />
            )}
          </group>
        );
      })}

      {workerIds.map((id) => (
        <Agent
          key={id}
          id={id}
          desk={layout.deskByAgent[id]}
          color={dotColorHex(dotMeta[id]?.color ?? 'blue')}
          deskByAgent={layout.deskByAgent}
          toWorld={toWorld}
        />
      ))}

      <CoordinatorCards coordinatorId={coordinatorId} toWorld={toWorld} />

      {beams
        .filter((b) => b.to !== coordinatorId && layout.deskByAgent[b.to])
        .map((b) => (
          <AssignBeam
            key={b.id}
            to={b.to}
            bornAt={b.bornAt}
            color={dotColorHex(dotMeta[b.to]?.color ?? 'blue')}
            coordinatorDesk={coordinatorDesk}
            targetDesk={layout.deskByAgent[b.to]}
            toWorld={toWorld}
          />
        ))}
    </>
  );
}

function CoordinatorCards({
  coordinatorId,
  toWorld,
}: {
  coordinatorId: string;
  toWorld: (cell: { x: number; z: number }) => [number, number];
}) {
  useOfficeStore((s) => s.tick);
  const layout = useOfficeStore.getState().layout;
  const queue = useOfficeStore.getState().coordinatorQueue;
  const group = useRef<THREE.Group>(null);
  useFrame((state) => {
    if (group.current) group.current.rotation.y = Math.sin(state.clock.elapsedTime * 0.5) * 0.04;
  });
  if (!layout || !queue.length) return null;
  const [cx, cz] = toWorld(layout.deskByAgent[coordinatorId].cell);
  return (
    <group ref={group} position={[cx, 1.75, cz - 0.1]}>
      {queue.slice(0, 4).map((card, i) => (
        <RoundedBox
          key={card.taskId}
          args={[0.22, 0.14, 0.015]}
          radius={0.015}
          position={[i * 0.1 - (queue.length - 1) * 0.05, i * 0.04, -i * 0.04]}
        >
          <meshStandardMaterial color="#fff4de" emissive="#f5a524" emissiveIntensity={0.4} roughness={0.5} />
        </RoundedBox>
      ))}
    </group>
  );
}

export function OfficeCanvas() {
  return (
    <Canvas shadows dpr={[1, 1.8]} gl={{ antialias: true }}>
      <color attach="background" args={['#f1efe9']} />
      <fog attach="fog" args={['#f1efe9', 15, 27]} />
      <OrthographicCamera makeDefault position={[9, 9, 9]} zoom={52} near={0.1} far={60} onUpdate={(cam) => cam.lookAt(0, 0.4, 0)} />
      <SceneContent />
      <OrbitControls
        enablePan={false}
        minDistance={8}
        maxDistance={16}
        minPolarAngle={Math.PI / 5}
        maxPolarAngle={Math.PI / 2.4}
        minAzimuthAngle={-Math.PI / 4}
        maxAzimuthAngle={Math.PI / 4}
      />
      <EffectComposer multisampling={0}>
        <N8AO aoRadius={0.6} intensity={1.1} distanceFalloff={1} />
        <Bloom luminanceThreshold={0.95} luminanceSmoothing={0.25} intensity={0.5} mipmapBlur radius={0.45} />
        <Vignette eskil={false} offset={0.2} darkness={0.35} />
      </EffectComposer>
    </Canvas>
  );
}
