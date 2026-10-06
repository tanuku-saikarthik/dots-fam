import type { AgentId, EventSource, OfficeEvent } from './types';

const TITLES = [
  'Follow up on the owner\'s request',
  'Compile findings into a summary',
  'Double-check the details before replying',
  'Draft a first pass for review',
  'Cross-check against the latest info',
];

let counter = 0;
const nextId = () => `demo-${++counter}`;

/**
 * Demo-mode fallback: plays believable handoff flows across whatever roster it's given,
 * so switching to "Demo" still shows your real team, just simulated instead of live.
 */
export class MockSimulator implements EventSource {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private speed: number;
  private coordinatorId: AgentId;
  private workerIds: AgentId[];

  constructor(coordinatorId: AgentId, workerIds: AgentId[], speed = 1) {
    this.coordinatorId = coordinatorId;
    this.workerIds = workerIds;
    this.speed = speed;
  }

  setSpeed(speed: number) {
    this.speed = speed;
  }

  start(onEvent: (event: OfficeEvent) => void) {
    this.stopped = false;
    void this.run(onEvent);
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }

  private delay(ms: number) {
    return new Promise<void>((resolve) => {
      this.timer = setTimeout(resolve, ms / this.speed);
    });
  }

  private pick<T>(list: T[]): T {
    return list[Math.floor(Math.random() * list.length)];
  }

  private async run(onEvent: (event: OfficeEvent) => void) {
    if (!this.workerIds.length) return;
    while (!this.stopped) {
      const taskId = nextId();
      const hops = 1 + Math.floor(Math.random() * Math.min(3, this.workerIds.length));
      const pool = [...this.workerIds].sort(() => Math.random() - 0.5).slice(0, hops);
      const title = this.pick(TITLES);

      onEvent({ type: 'task_assigned', taskId, title, to: pool[0] });
      await this.delay(500);

      let holder = pool[0];
      onEvent({ type: 'task_started', taskId, agent: holder });
      await this.delay(900);
      onEvent({ type: 'task_progress', taskId, agent: holder, progress: 1 });
      await this.delay(500);

      for (let i = 1; i < pool.length; i++) {
        const next = pool[i];
        onEvent({ type: 'handoff', taskId, from: holder, to: next });
        await this.delay(2200);
        onEvent({ type: 'task_started', taskId, agent: next });
        await this.delay(900);
        onEvent({ type: 'task_progress', taskId, agent: next, progress: 1 });
        await this.delay(500);
        holder = next;
      }

      onEvent({ type: 'handoff', taskId, from: holder, to: this.coordinatorId, note: 'Done' });
      await this.delay(2200);
      onEvent({ type: 'task_completed', taskId, agent: this.coordinatorId });
      await this.delay(1500);
    }
  }
}
