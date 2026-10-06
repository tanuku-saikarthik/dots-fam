/** A tiny tick-driven FIFO used to play one agent's animation clips in order. */
export interface Clip<T> {
  duration: number;
  data: T;
}

export interface AgentQueueState<T> {
  current: Clip<T> | null;
  elapsed: number;
  pending: Clip<T>[];
}

export function emptyQueue<T>(): AgentQueueState<T> {
  return { current: null, elapsed: 0, pending: [] };
}

export function push<T>(queue: AgentQueueState<T>, clip: Clip<T>) {
  queue.pending.push(clip);
}

/** Clears everything so the next pushed clip starts immediately (used to interrupt e.g. idle loops). */
export function clear<T>(queue: AgentQueueState<T>) {
  queue.current = null;
  queue.elapsed = 0;
  queue.pending = [];
}

export function tick<T>(
  queue: AgentQueueState<T>,
  deltaMs: number,
  handlers: { onStart: (clip: Clip<T>) => void; onEnd: (clip: Clip<T>) => void },
) {
  if (!queue.current) {
    const next = queue.pending.shift();
    if (!next) return;
    queue.current = next;
    queue.elapsed = 0;
    handlers.onStart(next);
  }
  queue.elapsed += deltaMs;
  if (queue.elapsed >= queue.current.duration) {
    const finished = queue.current;
    queue.current = null;
    queue.elapsed = 0;
    handlers.onEnd(finished);
  }
}

/** 0..1 progress through the currently-playing clip (for interpolation in render). */
export function queueProgress<T>(queue: AgentQueueState<T>): number {
  if (!queue.current) return 1;
  return Math.max(0, Math.min(1, queue.elapsed / queue.current.duration));
}
