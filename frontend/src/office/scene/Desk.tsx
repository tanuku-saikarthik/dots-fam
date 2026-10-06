import { RoundedBox } from '@react-three/drei';
import type { GridCell } from '../nav/grid';
import type { DeskSpec } from '../nav/layout';

const TOP = '#f5f4f0';
const LEG = '#c7cad6';
const CHAIR = '#8f93a3';
const CHAIR_PAD = '#aeb2c0';

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
  const height = desk.raised ? 0.78 : 0.62;
  const width = desk.raised ? 1.15 : 0.95;
  const depth = desk.raised ? 0.7 : 0.58;
  const chairOffset = desk.facing === 0 ? -0.55 : 0.55;
  const screenFacing = desk.facing === 0 ? -1 : 1;

  return (
    <group position={[x, 0, z]} rotation={[0, desk.facing, 0]}>
      {/* desktop */}
      <RoundedBox args={[width, 0.06, depth]} radius={0.03} position={[0, height, 0]} castShadow receiveShadow>
        <meshStandardMaterial color={TOP} roughness={0.7} metalness={0.02} />
      </RoundedBox>
      {/* a thin accent edge in the Dot's own color, so each desk reads at a glance */}
      <RoundedBox args={[width + 0.015, 0.014, depth + 0.015]} radius={0.02} position={[0, height - 0.025, 0]}>
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.3} roughness={0.4} />
      </RoundedBox>
      {/* legs */}
      {[-1, 1].map((sx) =>
        [-1, 1].map((sz) => (
          <mesh key={`${sx}-${sz}`} position={[(sx * width) / 2.4, height / 2, (sz * depth) / 2.6]} castShadow>
            <boxGeometry args={[0.05, height, 0.05]} />
            <meshStandardMaterial color={LEG} roughness={0.55} metalness={0.15} />
          </mesh>
        )),
      )}

      {/* monitor */}
      <group position={[0, height, (-depth / 2 + 0.06) * screenFacing]}>
        <mesh position={[0, 0.03, 0]}>
          <boxGeometry args={[0.08, 0.06, 0.02]} />
          <meshStandardMaterial color="#cfd2dc" roughness={0.6} />
        </mesh>
        <RoundedBox args={[0.34, 0.22, 0.015]} radius={0.01} position={[0, 0.2, 0]} rotation={[0, screenFacing > 0 ? Math.PI : 0, 0]} castShadow>
          <meshStandardMaterial color="#22242e" roughness={0.4} />
        </RoundedBox>
        <mesh position={[0, 0.2, screenFacing > 0 ? -0.009 : 0.009]} rotation={[0, screenFacing > 0 ? Math.PI : 0, 0]}>
          <planeGeometry args={[0.3, 0.17]} />
          <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.25} roughness={0.5} />
        </mesh>
      </group>
      {/* keyboard + mug */}
      <RoundedBox args={[0.22, 0.012, 0.09]} radius={0.01} position={[0, height + 0.034, 0.12 * screenFacing * -1]}>
        <meshStandardMaterial color="#e2e4ea" roughness={0.6} />
      </RoundedBox>
      <mesh position={[width / 2 - 0.1, height + 0.045, 0.14 * screenFacing * -1]} castShadow>
        <cylinderGeometry args={[0.028, 0.025, 0.055, 12]} />
        <meshStandardMaterial color={accent} roughness={0.5} />
      </mesh>

      {/* chair: seat, backrest, armrests, star base */}
      <group position={[0, 0, chairOffset]}>
        <RoundedBox args={[0.4, 0.05, 0.4]} radius={0.04} position={[0, 0.4, 0]} castShadow>
          <meshStandardMaterial color={CHAIR_PAD} roughness={0.8} />
        </RoundedBox>
        <RoundedBox
          args={[0.38, 0.46, 0.05]}
          radius={0.05}
          position={[0, 0.65, desk.facing === 0 ? 0.19 : -0.19]}
          castShadow
        >
          <meshStandardMaterial color={CHAIR} roughness={0.75} />
        </RoundedBox>
        {[-1, 1].map((sx) => (
          <RoundedBox key={sx} args={[0.04, 0.03, 0.26]} radius={0.015} position={[sx * 0.22, 0.46, 0]} castShadow>
            <meshStandardMaterial color={CHAIR} roughness={0.7} />
          </RoundedBox>
        ))}
        <mesh position={[0, 0.2, 0]}>
          <cylinderGeometry args={[0.035, 0.045, 0.36, 10]} />
          <meshStandardMaterial color={LEG} roughness={0.4} metalness={0.3} />
        </mesh>
        {[0, 1, 2, 3, 4].map((i) => {
          const a = (i / 5) * Math.PI * 2;
          return (
            <mesh key={i} position={[Math.cos(a) * 0.17, 0.03, Math.sin(a) * 0.17]} rotation={[0, -a, 0]} castShadow>
              <boxGeometry args={[0.05, 0.03, 0.2]} />
              <meshStandardMaterial color="#5c5f6b" roughness={0.6} />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}
