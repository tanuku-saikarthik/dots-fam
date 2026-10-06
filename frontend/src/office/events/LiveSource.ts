import { api } from '../../api';
import type { Activity, AppState, Delegation } from '../../api';
import type { EventSource, OfficeEvent } from './types';

const POLL_MS = 2200;

/**
 * Drives the office from your real Dots Fam team: polls /api/state and /api/activity
 * (the same endpoints the rest of the app uses) and turns delegations + running threads
 * into OfficeEvents. No backend changes needed — this is a pure read-side adapter.
 */
export class LiveSource implements EventSource {
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private onEvent: ((event: OfficeEvent) => void) | null = null;

  /** Only show this team: Dots outside it (another family) never appear in its office. */
  constructor(private readonly ids?: Set<string>) {}

  private inTeam(id: string) {
    return !this.ids || this.ids.has(id);
  }

  private seenDelegations = new Map<string, Delegation['status']>();
  private soloWorking = new Set<string>();

  start(onEvent: (event: OfficeEvent) => void) {
    this.stopped = false;
    this.onEvent = onEvent;
    void this.loop();
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }

  private async loop() {
    while (!this.stopped) {
      try {
        const [state, activity] = await Promise.all([api<AppState>('/state'), api<Activity>('/activity')]);
        this.diff(state, activity);
      } catch {
        // transient network hiccup — try again next tick
      }
      await new Promise((resolve) => {
        this.timer = setTimeout(resolve, POLL_MS);
      });
    }
  }

  private diff(state: AppState, activity: Activity) {
    const emit = this.onEvent;
    if (!emit) return;

    for (const delegation of activity.delegations) {
      if (!this.inTeam(delegation.from_dot_id) || !this.inTeam(delegation.to_dot_id)) continue;
      const prevStatus = this.seenDelegations.get(delegation.id);
      if (prevStatus === undefined) {
        // First time we've seen this delegation: it's already in flight by the time we poll,
        // so play assign + handoff back-to-back rather than guessing at the true start time.
        emit({ type: 'task_assigned', taskId: delegation.id, title: delegation.brief, to: delegation.from_dot_id });
        emit({
          type: 'handoff',
          taskId: delegation.id,
          from: delegation.from_dot_id,
          to: delegation.to_dot_id,
          note: delegation.expected_output?.slice(0, 70),
        });
        if (delegation.status === 'running' || delegation.status === 'queued') {
          emit({ type: 'task_started', taskId: delegation.id, agent: delegation.to_dot_id });
        }
      } else if (prevStatus !== delegation.status) {
        if (delegation.status === 'running' && prevStatus === 'queued') {
          emit({ type: 'task_started', taskId: delegation.id, agent: delegation.to_dot_id });
        }
        if (delegation.finished_at) {
          emit({ type: 'task_completed', taskId: delegation.id, agent: delegation.to_dot_id });
        }
      }
      this.seenDelegations.set(delegation.id, delegation.status);
    }

    // Dots actively running a thread with no open delegation (e.g. answering directly) still
    // show up as "working" at their own desk so the office never looks falsely idle.
    const busyFromDelegation = new Set(
      activity.delegations.filter((d) => !d.finished_at && this.inTeam(d.to_dot_id)).map((d) => d.to_dot_id),
    );
    for (const dotId of state.working_dots.filter((id) => this.inTeam(id))) {
      if (busyFromDelegation.has(dotId)) continue;
      if (!this.soloWorking.has(dotId)) {
        this.soloWorking.add(dotId);
        emit({ type: 'task_started', taskId: `solo-${dotId}`, agent: dotId });
      }
    }
    for (const dotId of [...this.soloWorking]) {
      if (!state.working_dots.includes(dotId)) {
        this.soloWorking.delete(dotId);
        emit({ type: 'task_completed', taskId: `solo-${dotId}`, agent: dotId });
      }
    }

    for (const [dotId, count] of Object.entries(state.waiting_by_dot)) {
      if (count > 0 && this.inTeam(dotId)) emit({ type: 'agent_waiting', agent: dotId, reason: 'Waiting on your approval' });
    }
  }
}
