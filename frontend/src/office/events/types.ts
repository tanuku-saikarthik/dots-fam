/** A real Dots Fam Dot id — the roster is loaded at runtime from /api/state, not hardcoded. */
export type AgentId = string;

export interface DotMeta {
  id: AgentId;
  name: string;
  title: string;
  /** Matches the hex map in components/DotMark.tsx so the office uses the same per-Dot color everywhere. */
  color: string;
}

export const DOT_HEX: Record<string, string> = {
  purple: '7a4dff',
  mint: '0e9f8e',
  orange: 'f26a21',
  blue: '2c6bed',
  ochre: 'c08f00',
  rose: 'e0457b',
};

export function dotColorHex(color: string): string {
  return `#${DOT_HEX[color] ?? DOT_HEX.blue}`;
}

export type AgentStatus = 'idle' | 'working' | 'waiting' | 'handing_off';

export type OfficeEvent =
  | { type: 'task_assigned'; taskId: string; title: string; to: AgentId }
  | { type: 'task_started'; taskId: string; agent: AgentId }
  | { type: 'task_progress'; taskId: string; agent: AgentId; progress: number }
  | { type: 'handoff'; taskId: string; from: AgentId; to: AgentId; note?: string }
  | { type: 'task_completed'; taskId: string; agent: AgentId }
  | { type: 'agent_waiting'; agent: AgentId; reason?: string };

export interface Task {
  id: string;
  title: string;
  /** The agent currently holding this task. */
  owner: AgentId;
  /** Ordered history of agents that have touched this task. */
  history: AgentId[];
  progress: number;
  done: boolean;
}

/** A source of OfficeEvents the scene can subscribe to — a mock run or the live Dots Fam feed. */
export interface EventSource {
  start(onEvent: (event: OfficeEvent) => void): void;
  stop(): void;
}
