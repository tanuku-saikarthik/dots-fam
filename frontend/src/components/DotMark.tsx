import type { Dot } from '../api';

export function DotMark({ dot, size = 'm' }: { dot?: Pick<Dot, 'name' | 'color'>; size?: 's' | 'm' | 'l' }) {
  return (
    <span className={`dot-mark size-${size} c-${dot?.color ?? 'blue'}`} aria-hidden="true">
      {dot?.name.slice(0, 1).toUpperCase() ?? '?'}
    </span>
  );
}
