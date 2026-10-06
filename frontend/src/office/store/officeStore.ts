import { create } from 'zustand';
import { buildLayout, type OfficeLayout } from '../nav/layout';
import {
  type AgentQueueState,
  emptyQueue,
  clear as clearQueue,
  push as pushClip,
  tick as tickQueue,
} from '../events/queue';
import {
  type AgentPhase,
  type ClipData,
  clipForIdle,
  clipForReceiving,
  clipForWaiting,
  clipForWorking,
  planHandoff,
} from '../agents/stateMachine';
import type { AgentId, DotMeta, EventSource, OfficeEvent, Task } from '../events/types';
import { MockSimulator } from '../events/MockSimulator';
import { LiveSource } from '../events/LiveSource';
import { WebSocketSource } from '../events/WebSocketSource';

export interface AgentRuntime {
  id: AgentId;
  phase: AgentPhase;
  cellPos: { x: number; z: number };
  currentTaskId: string | null;
  progress: number;
  waitingReason?: string;
  queue: AgentQueueState<ClipData>;
}

export interface LogEntry {
  id: string;
  at: number;
  text: string;
}

export interface CoordinatorCard {
  taskId: string;
  title: string;
  to: AgentId;
}

export interface AssignBeam {
  id: string;
  to: AgentId;
  bornAt: number;
}

let uid = 0;
const nextUid = () => `e${++uid}`;

function makeAgent(id: AgentId, layout: OfficeLayout): AgentRuntime {
  const desk = layout.deskByAgent[id];
  return { id, phase: 'idle', cellPos: { ...desk.standPoint }, currentTaskId: null, progress: 0, queue: emptyQueue() };
}

interface OfficeState {
  ready: boolean;
  coordinatorId: AgentId | null;
  workerIds: AgentId[];
  dotMeta: Record<AgentId, DotMeta>;
  layout: OfficeLayout | null;

  agents: Record<AgentId, AgentRuntime>;
  tasks: Record<string, Task>;
  log: LogEntry[];
  coordinatorQueue: CoordinatorCard[];
  assignBeams: AssignBeam[];
  playing: boolean;
  speed: number;
  sourceMode: 'live' | 'demo' | 'ws';
  connectionStatus: 'connecting' | 'open' | 'closed' | 'live' | 'demo';
  selectedAgent: AgentId | null;
  tick: number;

  initRoster: (coordinatorId: AgentId, workerIds: AgentId[], dotMeta: Record<AgentId, DotMeta>) => void;
  applyEvent: (event: OfficeEvent) => void;
  stepFrame: (deltaMs: number) => void;
  setPlaying: (playing: boolean) => void;
  setSpeed: (speed: number) => void;
  selectAgent: (id: AgentId | null) => void;
  connect: (mode: 'live' | 'demo', ids?: string[]) => void;
}

let source: EventSource | null = null;
let lastBump = 0;

export const useOfficeStore = create<OfficeState>((set, get) => ({
  ready: false,
  coordinatorId: null,
  workerIds: [],
  dotMeta: {},
  layout: null,

  agents: {},
  tasks: {},
  log: [],
  coordinatorQueue: [],
  assignBeams: [],
  playing: true,
  speed: 1,
  sourceMode: 'live',
  connectionStatus: 'connecting',
  selectedAgent: null,
  tick: 0,

  initRoster(coordinatorId, workerIds, dotMeta) {
    const layout = buildLayout(coordinatorId, workerIds);
    const agents = Object.fromEntries(workerIds.map((id) => [id, makeAgent(id, layout)])) as Record<AgentId, AgentRuntime>;
    set({ ready: true, coordinatorId, workerIds, dotMeta, layout, agents });
  },

  applyEvent(event) {
    const state = get();
    if (!state.layout) return;
    const layout = state.layout;
    const log = (text: string) => state.log.push({ id: nextUid(), at: Date.now(), text });
    const label = (id: AgentId) => state.dotMeta[id]?.name ?? id;

    switch (event.type) {
      case 'task_assigned': {
        if (!state.tasks[event.taskId]) {
          state.tasks[event.taskId] = {
            id: event.taskId,
            title: event.title,
            owner: event.to,
            history: [event.to],
            progress: 0,
            done: false,
          };
        }
        if (event.to !== state.coordinatorId) {
          state.coordinatorQueue.push({ taskId: event.taskId, title: event.title, to: event.to });
          state.assignBeams.push({ id: nextUid(), to: event.to, bornAt: Date.now() });
        }
        log(`${label(state.coordinatorId ?? event.to)} picked up "${event.title}"`);
        break;
      }
      case 'task_started': {
        const agent = state.agents[event.agent];
        if (!agent) break;
        state.coordinatorQueue = state.coordinatorQueue.filter((c) => c.taskId !== event.taskId);
        clearQueue(agent.queue);
        pushClip(agent.queue, clipForWorking(event.taskId));
        agent.currentTaskId = event.taskId;
        agent.progress = 0;
        agent.waitingReason = undefined;
        break;
      }
      case 'task_progress': {
        const agent = state.agents[event.agent];
        if (agent) agent.progress = event.progress;
        const task = state.tasks[event.taskId];
        if (task) task.progress = event.progress;
        break;
      }
      case 'handoff': {
        const fromAgent = state.agents[event.from];
        const toAgent = state.agents[event.to];
        let task = state.tasks[event.taskId];
        if (!task) {
          task = { id: event.taskId, title: event.note || 'Handoff', owner: event.to, history: [event.from], progress: 0, done: false };
          state.tasks[event.taskId] = task;
        }
        task.owner = event.to;
        task.history.push(event.to);

        if (fromAgent) {
          const plan = planHandoff(
            layout.grid,
            event.from,
            event.to,
            event.taskId,
            fromAgent.cellPos,
            layout.deskByAgent,
            layout.width,
            layout.depth,
          );
          clearQueue(fromAgent.queue);
          for (const clip of plan.giverClips) pushClip(fromAgent.queue, clip);
          fromAgent.currentTaskId = null;
          fromAgent.progress = 0;
          if (toAgent) {
            clearQueue(toAgent.queue);
            pushClip(toAgent.queue, clipForWaiting(plan.preHandoffMs));
            pushClip(toAgent.queue, clipForReceiving(event.from, event.taskId));
          }
        } else if (toAgent) {
          // giver has no desk in this office (e.g. the Coordinator itself) — receiver still gets the folder.
          clearQueue(toAgent.queue);
          pushClip(toAgent.queue, clipForReceiving(event.from, event.taskId));
        }
        log(`${label(event.from)} handed "${task.title}" to ${label(event.to)}${event.note ? ` — ${event.note}` : ''}`);
        break;
      }
      case 'task_completed': {
        const agent = state.agents[event.agent];
        const task = state.tasks[event.taskId];
        if (task) task.done = true;
        if (agent) {
          clearQueue(agent.queue);
          pushClip(agent.queue, clipForIdle());
          agent.currentTaskId = null;
          agent.progress = 0;
        }
        log(`${label(event.agent)} completed "${task?.title ?? event.taskId}"`);
        break;
      }
      case 'agent_waiting': {
        const agent = state.agents[event.agent];
        if (agent && agent.waitingReason !== event.reason) {
          agent.waitingReason = event.reason ?? 'Waiting';
          log(`${label(event.agent)} is waiting${event.reason ? `: ${event.reason}` : ''}`);
        }
        break;
      }
    }
    set({ tick: state.tick + 1 });
  },

  stepFrame(deltaMs) {
    const state = get();
    if (!state.playing || !state.layout) return;
    const scaled = deltaMs * state.speed;
    for (const agent of Object.values(state.agents)) {
      tickQueue(agent.queue, scaled, {
        onStart: (clip) => {
          agent.phase = clip.data.phase;
          if (clip.data.phase !== 'waiting') agent.waitingReason = undefined;
        },
        onEnd: (clip) => {
          if (clip.data.phase === 'walking') agent.cellPos = { ...clip.data.toCell };
        },
      });
      if (!agent.queue.current && !agent.queue.pending.length && agent.phase !== 'idle' && !agent.currentTaskId) {
        pushClip(agent.queue, clipForIdle());
      }
    }
    const now0 = Date.now();
    if (state.assignBeams.some((b) => now0 - b.bornAt >= 900)) {
      state.assignBeams = state.assignBeams.filter((b) => now0 - b.bornAt < 900);
    }

    const now = Date.now();
    if (now - lastBump > 140) {
      lastBump = now;
      set({ tick: state.tick + 1 });
    }
  },

  setPlaying(playing) {
    set({ playing });
  },
  setSpeed(speed) {
    set({ speed });
    if (source instanceof MockSimulator) source.setSpeed(speed);
  },
  selectAgent(id) {
    set({ selectedAgent: id });
  },
  connect(mode, ids) {
    source?.stop();
    const state = get();
    if (mode === 'live') {
      const live = new LiveSource(ids ? new Set(ids) : undefined);
      source = live;
      set({ sourceMode: 'live', connectionStatus: 'live' });
      live.start((event) => get().applyEvent(event));
    } else {
      if (!state.coordinatorId) return;
      const sim = new MockSimulator(state.coordinatorId, state.workerIds, state.speed);
      source = sim;
      set({ sourceMode: 'demo', connectionStatus: 'demo' });
      sim.start((event) => get().applyEvent(event));
    }
  },
}));

export function connectWebSocket(url: string) {
  source?.stop();
  const ws = new WebSocketSource(url);
  ws.onStatusChange = (status) => useOfficeStore.setState({ connectionStatus: status, sourceMode: 'ws' });
  source = ws;
  ws.start((event) => useOfficeStore.getState().applyEvent(event));
}
