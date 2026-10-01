export type Status =
  | 'queued'
  | 'scheduled'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';
export interface Settings {
  name: string;
  paused: boolean;
  researchAllowed: boolean;
  memoryAllowed: boolean;
}
export interface Task {
  id: string;
  prompt: string;
  status: Status;
  intervalSeconds: number | null;
  /** Five-field cron expression; when set it takes precedence over intervalSeconds. */
  cron?: string | null;
  /** IANA time zone used to evaluate `cron`. */
  timezone?: string | null;
  /** Webhook trigger that created this one-off task, if any. */
  triggerId?: string | null;
  nextRunAt: number | null;
  createdAt: number;
  updatedAt: number;
  error: string | null;
  lease: string | null;
  leaseUntil: number | null;
}
export interface Source {
  title: string;
  url: string;
  excerpt: string;
}
export interface Result {
  text: string;
  sources: Source[];
  sample: boolean;
  screenshot?: string;
}
export interface Run {
  id: string;
  taskId: string;
  status: string;
  startedAt: number;
  finishedAt: number | null;
  result: Result | null;
  error: string | null;
}
export interface TaskEvent {
  id: number;
  taskId: string;
  runId: string | null;
  text: string;
  createdAt: number;
}
export interface Memory {
  id: string;
  text: string;
  createdAt: number;
}
export interface Detail {
  task: Task;
  runs: Run[];
  events: TaskEvent[];
}
export interface State {
  settings: Settings;
  tasks: Task[];
  memories: Memory[];
  mode: 'sample' | 'live';
  configured: boolean;
}
export type Action = 'run' | 'pause' | 'cancel' | 'resume';
/**
 * reversible: the Reversibility Law. Read, research and draft freely; anything that
 * changes the outside world needs an owner approval first.
 * autonomous: no approval gate (other permissions still apply).
 */
export type ApprovalMode = 'reversible' | 'autonomous';
export interface Space {
  id: string;
  name: string;
  description: string;
  createdAt: number;
}
export interface Dot {
  id: string;
  /** Default destination for saved pages, not ownership. */
  spaceId: string;
  spaceIds: string[];
  name: string;
  instructions: string;
  researchAllowed: boolean;
  memoryAllowed: boolean;
  createdAt: number;
  learningContainerId?: string | null;
  skillDeliveryEnabled?: boolean;
  /** `provider:model`, e.g. `anthropic:claude-sonnet-4-5`. Null uses the default model. */
  model: string | null;
  /** Chief of Staff: may hand scoped briefs to the other Dots. */
  canDelegate: boolean;
  approvalMode: ApprovalMode;
}
export type DelegationStatus = 'running' | 'completed' | 'failed' | 'cancelled';
export interface Delegation {
  id: string;
  groupId: string;
  /** Conversation of the delegating Dot; follow-ups are delivered there. */
  threadId: string | null;
  fromDotId: string;
  toDotId: string;
  brief: string;
  expectedOutput: string;
  status: DelegationStatus;
  result: string | null;
  error: string | null;
  model: string | null;
  createdAt: number;
  finishedAt: number | null;
}
export interface DelegationEvent {
  id: number;
  delegationId: string;
  text: string;
  createdAt: number;
}
export type ApprovalStatus =
  'pending' | 'approved' | 'declined' | 'used' | 'expired';
export interface Approval {
  id: string;
  dotId: string;
  threadId: string | null;
  delegationId: string | null;
  kind: string;
  summary: string;
  details: string;
  status: ApprovalStatus;
  createdAt: number;
  decidedAt: number | null;
  usedAt: number | null;
}
export interface Trigger {
  id: string;
  name: string;
  threadId: string;
  prompt: string;
  enabled: boolean;
  createdAt: number;
  lastFiredAt: number | null;
  fireCount: number;
}
export interface TeamState {
  delegations: Delegation[];
  events: DelegationEvent[];
  approvals: Approval[];
  triggers: Trigger[];
}
export interface Conversation {
  id: string;
  dotId: string;
  ownerId: string;
  title: string;
  createdAt: number;
  /** Frozen at creation; null means this conversation does not participate. */
  learningContainerId?: string | null;
}
export interface CallReceipt {
  anchorMessageId?: string | null;
  id: string;
  threadId: string;
  startedAt: number;
  endedAt: number | null;
  status: 'connecting' | 'active' | 'ended' | 'failed';
  transcript: string;
  error: string | null;
}
export interface SetupStatus {
  intelligence: boolean;
  model: boolean;
  browser: boolean;
  voice: boolean;
  slack: string;
  missing: string[];
  /** Model providers with a configured key. */
  providers?: string[];
  /** Default `provider:model` for Dots without their own model. */
  defaultModel?: string | null;
  /** Suggested model for specialist (worker) Dots. */
  workerModel?: string | null;
  timezone?: string;
}
export interface WorkspaceState {
  spaces: Space[];
  dots: Dot[];
  conversations: Conversation[];
  setup: SetupStatus;
  calls: CallReceipt[];
}
