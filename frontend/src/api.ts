export interface Dot {
  id: string;
  name: string;
  title: string;
  instructions: string;
  model: string | null;
  can_delegate: boolean;
  approval_mode: 'reversible' | 'autonomous';
  research_allowed: boolean;
  memory_allowed: boolean;
  space_id: string;
  space_ids: string[];
  color: string;
  computer: Record<string, boolean>;
}
export interface Space {
  id: string;
  name: string;
  description: string;
}
export interface Thread {
  id: string;
  dot_id: string;
  title: string;
  kind: string;
  updated_at: number;
  running?: boolean;
  pending_approvals?: number;
}
export interface Setup {
  missing: string[];
  providers: string[];
  default_model: string | null;
  worker_model: string | null;
  timezone: string;
  computer_driver: string;
  web_search: boolean;
  slack: boolean;
  voice: string;
}
export interface AppState {
  flags: { paused: boolean; research_allowed: boolean; memory_allowed: boolean };
  setup: Setup;
  dots: Dot[];
  spaces: Space[];
  threads: Thread[];
  memories: { id: string; text: string }[];
  pending_approvals: number;
  working_dots: string[];
  waiting_by_dot: Record<string, number>;
  models: string[];
}
export interface Approval {
  id: string;
  batch_id: string;
  thread_id: string;
  dot_id: string;
  tool_call_id: string;
  tool: string;
  args: Record<string, unknown>;
  reason: string;
  status: 'pending' | 'approved' | 'declined' | 'expired';
  note: string | null;
  created_at: number;
  decided_at: number | null;
}
export interface Message {
  id: string | null;
  role: 'user' | 'assistant' | 'tool' | 'system';
  text: string;
  source?: string;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
  ok?: boolean;
}
export interface RunEvent {
  type: string;
  run_id?: string;
  [key: string]: unknown;
}
export interface ThreadDetail {
  thread: Thread;
  messages: Message[];
  approvals: Approval[];
  running: boolean;
  run_events: RunEvent[];
}
export interface Delegation {
  id: string;
  group_id: string;
  parent_thread_id: string | null;
  worker_thread_id: string;
  from_dot_id: string;
  to_dot_id: string;
  brief: string;
  expected_output: string;
  status: string;
  result: string | null;
  error: string | null;
  model: string | null;
  created_at: number;
  finished_at: number | null;
}
export interface Task {
  id: string;
  thread_id: string;
  thread_title?: string;
  dot_id?: string;
  prompt: string;
  status: string;
  cron: string | null;
  timezone: string | null;
  interval_seconds: number | null;
  next_run_at: number | null;
  trigger_id: string | null;
  origin: string;
  error: string | null;
  updated_at: number;
}
export interface Trigger {
  id: string;
  name: string;
  thread_id: string;
  prompt: string;
  enabled: boolean;
  fire_count: number;
  last_fired_at: number | null;
}
export interface Activity {
  approvals: Approval[];
  delegations: Delegation[];
  delegation_events: { id: number; thread_id: string; kind: string; text: string; created_at: number }[];
  tasks: Task[];
  triggers: Trigger[];
  running: { thread_id: string; run_id: string; source: string }[];
}
export interface ComputerStatus {
  available: boolean;
  driver: string;
  running?: boolean;
  permissions?: { enabled: boolean; browser: boolean; files: boolean; shell: boolean };
  url?: string | null;
  holder?: 'agent' | 'human';
  browser?: boolean;
  activity?: { id: number; text: string; created_at: number }[];
}
export interface FileEntry {
  name: string;
  dir: boolean;
  size: number | null;
}
export interface Page {
  id: string;
  space_id: string;
  parent_id: string | null;
  title: string;
  content: string;
  revision: number;
  author: string;
  updated_at: number;
  preview?: string;
  revisions?: { revision: number; author: string; created_at: number }[];
}

const TOKEN_KEY = 'dotsfam-token';
let token = (() => {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
})();

export function setToken(value: string) {
  token = value;
  try {
    sessionStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* storage unavailable: keep it in memory */
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (data as { error?: string; detail?: unknown }).error ?? (data as { detail?: unknown }).detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join(' ')
          : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  return data as T;
}

/** Binary GET (screenshots). Resolves to undefined when there is nothing to show. */
export async function apiBlob(path: string): Promise<Blob | undefined> {
  const response = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) return undefined;
  return response.blob();
}

export function streamUrl(threadId: string) {
  return `/api/threads/${threadId}/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`;
}

export const relative = (ms: number | null | undefined) => {
  if (!ms) return '';
  const minutes = Math.round((Date.now() - ms) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return new Date(ms).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
};

export const when = (ms: number | null | undefined) =>
  ms
    ? new Date(ms).toLocaleString(undefined, {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';
