import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Line } from '@react-three/drei';
import type { CatmullRomCurve3 } from 'three';

interface Line2Like {
  material: { opacity: number };
}

/** A faint dotted line on the floor tracing an agent's walking route; fades itself out once `active` goes false. */
export function PathLine({
  curve,
  isActive,
  color,
  onFaded,
}: {
  curve: CatmullRomCurve3;
  isActive: () => boolean;
  color: string;
  onFaded: () => void;
}) {
  const points = useMemo(() => curve.getPoints(40).map((p) => [p.x, 0.012, p.z] as [number, number, number]), [curve]);
  const matRef = useRef<Line2Like>(null);
  const opacity = useRef(0);
  const faded = useRef(false);

  useFrame((_, delta) => {
    const active = isActive();
    opacity.current = active
      ? Math.min(0.55, opacity.current + delta * 3)
      : Math.max(0, opacity.current - delta * 1.4);
    if (matRef.current) matRef.current.material.opacity = opacity.current;
    if (!active && opacity.current <= 0.001 && !faded.current) {
      faded.current = true;
      onFaded();
    }
  });

  return (
    <Line
      ref={matRef as never}
      points={points}
      color={color}
      lineWidth={1.4}
      dashed
      dashScale={6}
      dashSize={0.35}
      gapSize={0.35}
      transparent
      opacity={0}
    />
  );
}
