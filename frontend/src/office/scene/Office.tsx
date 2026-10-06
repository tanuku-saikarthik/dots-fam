import { useEffect, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { ContactShadows, Grid, Html, OrbitControls, OrthographicCamera, RoundedBox } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import { useOfficeStore } from '../store/officeStore';
import { CELL_SIZE as CELL, makeCellToWorld } from '../nav/layout';
import { dotColorHex } from '../events/types';
import { Desk } from './Desk';
import { Agent } from './Agent';
import { AssignBeam } from './AssignBeam';

/** World-space box around every desk and stand point, plus a margin: the part of the office in use. */
export function deskBounds(layout: NonNullable<ReturnType<typeof useOfficeStore.getState>['layout']>) {
  const toWorld = makeCellToWorld(layout.width, layout.depth);
  const points = layout.desks.flatMap((d) => [toWorld(d.cell), toWorld(d.standPoint)]);
  const xs = points.map((p) => p[0]);
  const zs = points.map((p) => p[1]);
  const margin = 1.1;
  const minX = Math.min(...xs) - margin, maxX = Math.max(...xs) + margin;
  const minZ = Math.min(...zs) - margin, maxZ = Math.max(...zs) + margin;
  return { cx: (minX + maxX) / 2, cz: (minZ + maxZ) / 2, w: maxX - minX, d: maxZ - minZ };
}

/** A floating dark stage with a faint grid, so the office reads as a lit set, not a room box. */
function Floor() {
  const layout = useOfficeStore((s) => s.layout);
  if (!layout) return null;
  const { cx, cz, w, d } = deskBounds(layout);
  return (
    <group position={[cx, 0, cz]}>
      <RoundedBox args={[w, 0.24, d]} radius={0.08} position={[0, -0.12, 0]} receiveShadow>
        <meshStandardMaterial color="#171a24" roughness={0.85} metalness={0.05} />
      </RoundedBox>
      <Grid
        position={[0, 0.002, 0]}
        args={[w - 0.2, d - 0.2]}
        cellSize={CELL / 2}
        cellThickness={0.6}
        cellColor="#252a38"
        sectionSize={CELL}
        sectionThickness={1}
        sectionColor="#2e3446"
        fadeDistance={60}
        infiniteGrid={false}
      />
    </group>
  );
}

function CoordinatorDot({ id, toWorld }: { id: string; toWorld: (cell: { x: number; z: number }) => [number, number] }) {
  const layout = useOfficeStore((s) => s.layout);
  const color = useOfficeStore((s) => dotColorHex(s.dotMeta[id]?.color ?? 'purple'));
  const orb = useRef<THREE.MeshStandardMaterial>(null);
  useFrame((state) => {
    const busy = useOfficeStore.getState().coordinatorQueue.length > 0;
    if (orb.current) orb.current.emissiveIntensity = busy ? 0.8 + Math.sin(state.clock.elapsedTime * 3) * 0.2 : 0.35;
  });
  if (!layout) return null;
  const desk = layout.deskByAgent[id];
  const [sx, sz] = toWorld(desk.standPoint);
  const [dx, dz] = toWorld(desk.cell);
  const x = sx + (dx - sx) * 0.42;
  const z = sz + (dz - sz) * 0.42;
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.84, 0]} castShadow>
        <sphereGeometry args={[0.3, 48, 48]} />
        <meshStandardMaterial ref={orb} color={color} emissive={color} emissiveIntensity={0.35} roughness={0.25} metalness={0.1} />
      </mesh>
      <mesh position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]} renderOrder={2}>
        <circleGeometry args={[0.4, 40]} />
        <meshBasicMaterial color={color} transparent opacity={0.16} depthWrite={false} />
      </mesh>
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
  // drei's Html loses its content if it mounts before the canvas is attached; wait one frame.
  const [labelsReady, setLabelsReady] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setLabelsReady(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  if (!layout || !coordinatorId) return null;
  const toWorld = makeCellToWorld(layout.width, layout.depth);
  const beams = useOfficeStore.getState().assignBeams;
  const coordinatorDesk = layout.deskByAgent[coordinatorId];

  return (
    <>
      <hemisphereLight args={['#c9d3ff', '#0b0d14', 0.55]} />
      <directionalLight
        position={[5, 10, 6]}
        intensity={1.1}
        color="#f2f4ff"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-10}
        shadow-camera-right={10}
        shadow-camera-top={10}
        shadow-camera-bottom={-10}
        shadow-bias={-0.0005}
      />
      <ambientLight intensity={0.25} />

      <Floor />
      <ContactShadows position={[0, 0.003, 0]} opacity={0.55} scale={20} blur={2.2} far={2} color="#000000" />

      {layout.desks.map((desk) => (
        <Desk
          key={desk.id}
          desk={desk}
          toWorld={toWorld}
          accent={dotColorHex(dotMeta[desk.id]?.color ?? 'blue')}
        />
      ))}

      <CoordinatorDot id={coordinatorId} toWorld={toWorld} />
      {labelsReady && (() => {
        const chief = layout.deskByAgent[coordinatorId];
        const [sx, sz] = toWorld(chief.standPoint);
        const [dx, dz] = toWorld(chief.cell);
        const [lx, lz] = [sx + (dx - sx) * 0.42, sz + (dz - sz) * 0.42];
        return (
          <Html position={[lx, 1.4, lz]} center zIndexRange={[20, 0]} pointerEvents="none">
            <div className="office-label phase-chief">
              <strong>{dotMeta[coordinatorId]?.name ?? 'Chief'}</strong>
              <span>Chief of Staff</span>
            </div>
          </Html>
        );
      })()}

      {workerIds.map((id) => (
        <Agent
          key={id}
          id={id}
          desk={layout.deskByAgent[id]}
          color={dotColorHex(dotMeta[id]?.color ?? 'blue')}
          deskByAgent={layout.deskByAgent}
          toWorld={toWorld}
          showLabel={labelsReady}
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

/** Zoom the isometric camera so the whole office fills the stage, at any window size. */
function FitCamera() {
  const layout = useOfficeStore((s) => s.layout);
  const { camera, size } = useThree();
  useEffect(() => {
    if (!layout) return;
    const { cx, cz, w, d } = deskBounds(layout);
    const span = w + d;
    camera.zoom = Math.min(size.width / (span * 0.75), size.height / (span * 0.42 + 2.4));
    camera.position.set(cx + 10, 10, cz + 10);
    camera.lookAt(cx, 0.5, cz);
    camera.updateProjectionMatrix();
  }, [layout, camera, size.width, size.height]);
  return null;
}

function Controls() {
  const layout = useOfficeStore((s) => s.layout);
  const center = layout ? deskBounds(layout) : { cx: 0, cz: 0 };
  return (
    <OrbitControls
      makeDefault
      target={[center.cx, 0.5, center.cz]}
      enablePan={false}
      minZoom={30}
      maxZoom={220}
      minPolarAngle={Math.PI / 6}
      maxPolarAngle={Math.PI / 2.6}
      minAzimuthAngle={-Math.PI / 3}
      maxAzimuthAngle={Math.PI / 3}
    />
  );
}

export function OfficeCanvas() {
  return (
    <Canvas shadows dpr={[1, 2]} gl={{ antialias: true, alpha: true }}>
      <OrthographicCamera makeDefault position={[10, 10, 10]} zoom={60} near={0.1} far={80} />
      <FitCamera />
      <SceneContent />
      <Controls />
      <EffectComposer multisampling={4}>
        <Bloom luminanceThreshold={0.6} luminanceSmoothing={0.3} intensity={0.9} mipmapBlur radius={0.55} />
      </EffectComposer>
    </Canvas>
  );
}
