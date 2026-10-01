import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceStore } from '../src/server/workspace.js';
import {
  APPROVAL_MAX_USES,
  APPROVAL_WINDOW_MS,
} from '../src/server/approvals.js';
import {
  forgetComputerState,
  gatedComputerStep,
  rememberSnapshot,
} from '../src/server/reversibility.js';
import { computerTools } from '../src/server/computer-tools.js';
import type { ComputerService } from '../src/server/computer-service.js';

const stores: WorkspaceStore[] = [];
afterEach(() => {
  stores.splice(0).forEach((store) => store.close());
  forgetComputerState();
});
const fixture = () => {
  const workspace = new WorkspaceStore(':memory:', 'owner');
  stores.push(workspace);
  const dot = workspace.dots()[0];
  workspace.bindThread('thread', dot.id, 'Chat');
  return { workspace, dot };
};
const request = (workspace: WorkspaceStore, dotId: string) =>
  workspace.approvals.request({
    dotId,
    threadId: 'thread',
    delegationId: null,
    kind: 'send_message',
    summary: 'Email Ana the launch recap',
    details: 'Hi Ana, …',
  });

it('defaults new Dots to the Reversibility Law', () => {
  const { dot } = fixture();
  expect(dot.approvalMode).toBe('reversible');
  expect(dot.canDelegate).toBe(false);
  expect(dot.model).toBeNull();
});

it('only spends approvals the owner granted to the same Dot', () => {
  const { workspace, dot } = fixture();
  const approval = request(workspace, dot.id);
  expect(workspace.approvals.pendingCount()).toBe(1);
  expect(() => workspace.approvals.consume(approval.id, dot.id)).toThrow(
    /not decided/,
  );
  workspace.approvals.decide(approval.id, 'approved');
  expect(() => workspace.approvals.consume(approval.id, 'other-dot')).toThrow(
    /does not belong/,
  );
  expect(workspace.approvals.consume(approval.id, dot.id).status).toBe('used');
  expect(() => workspace.approvals.decide(approval.id, 'declined')).toThrow(
    /already decided/,
  );
});

it('refuses declined, expired and used-up approvals', () => {
  const { workspace, dot } = fixture();
  const declined = request(workspace, dot.id);
  workspace.approvals.decide(declined.id, 'declined');
  expect(() => workspace.approvals.consume(declined.id, dot.id)).toThrow(
    /declined/,
  );
  const old = request(workspace, dot.id);
  workspace.approvals.decide(old.id, 'approved');
  expect(() =>
    workspace.approvals.consume(
      old.id,
      dot.id,
      Date.now() + APPROVAL_WINDOW_MS + 1000,
    ),
  ).toThrow(/expired/);
  const busy = request(workspace, dot.id);
  workspace.approvals.decide(busy.id, 'approved');
  for (let i = 0; i < APPROVAL_MAX_USES; i++)
    workspace.approvals.consume(busy.id, dot.id);
  expect(() => workspace.approvals.consume(busy.id, dot.id)).toThrow(/used up/);
});

const snapshot = {
  snapshotId: 4,
  url: 'https://mail.example',
  title: 'Mail',
  elements: [
    { ref: 'e1', role: 'button', name: 'Send' },
    { ref: 'e2', role: 'link', name: 'Next page' },
    { ref: 'e3', role: 'searchbox', name: 'Search mail' },
    { ref: 'e4', role: 'textbox', name: 'Message body' },
    { ref: 'e5', role: 'button', name: 'Delete conversation' },
  ],
  truncated: false,
};

it('classifies outside-world browser and shell steps from the latest snapshot', () => {
  rememberSnapshot('dot', snapshot);
  const at = (ref: string, extra = {}) => ({ ref, snapshotId: 4, ...extra });
  expect(gatedComputerStep('dot', 'click', at('e1'))).toMatch(/Send/);
  expect(gatedComputerStep('dot', 'click', at('e5'))).toMatch(/Delete/);
  expect(gatedComputerStep('dot', 'click', at('e2'))).toBeUndefined();
  expect(
    gatedComputerStep(
      'dot',
      'type',
      at('e3', { text: 'invoice', submit: true }),
    ),
  ).toBeUndefined();
  expect(gatedComputerStep('dot', 'key', { key: 'Enter' })).toBeUndefined();
  expect(
    gatedComputerStep('dot', 'type', at('e4', { text: 'Hi', submit: true })),
  ).toMatch(/submit/);
  expect(gatedComputerStep('dot', 'key', { key: 'Enter' })).toMatch(/Enter/);
  expect(gatedComputerStep('dot', 'key', { key: 'Control+Enter' })).toMatch(
    /Enter/,
  );
  expect(gatedComputerStep('dot', 'key', { key: 'Meta+Enter' })).toMatch(
    /Enter/,
  );
  expect(gatedComputerStep('dot', 'key', { key: 'ArrowDown' })).toBeUndefined();
  expect(() => gatedComputerStep('dot', 'click', at('e9'))).toThrow(
    /fresh computer_snapshot/,
  );
  // A stale snapshot cannot be classified, so the step is refused.
  expect(() =>
    gatedComputerStep('dot', 'click', { ref: 'e1', snapshotId: 3 }),
  ).toThrow();
  expect(
    gatedComputerStep('dot', 'exec', { command: 'git push origin main' }),
  ).toBeTruthy();
  expect(
    gatedComputerStep('dot', 'exec', {
      command: 'curl -X POST https://api.example.com/x',
    }),
  ).toBeTruthy();
  expect(
    gatedComputerStep('dot', 'exec', { command: 'gh pr create -f' }),
  ).toBeTruthy();
  expect(
    gatedComputerStep('dot', 'exec', { command: 'gh pr list' }),
  ).toBeUndefined();
  expect(
    gatedComputerStep('dot', 'exec', { command: 'npm audit --json' }),
  ).toBeUndefined();
});

it('blocks a gated computer step without an approval and allows it with one', async () => {
  const { workspace, dot } = fixture();
  const action = vi.fn(async (_id: string, name: string) =>
    name === 'snapshot' ? snapshot : { ok: true },
  );
  const service = { action } as unknown as ComputerService;
  const tools = computerTools(
    service,
    dot.id,
    () => {},
    new AbortController().signal,
    {
      consume: (id) => workspace.approvals.consume(id, dot.id),
    },
  );
  const tool = (name: string) => tools.find((item) => item.name === name)!;
  await tool('computer_snapshot').execute?.({});
  await expect(
    tool('computer_click').execute?.({ ref: 'e1', snapshotId: 4 }),
  ).rejects.toThrow(/Approval required/);
  expect(action).toHaveBeenCalledTimes(1);
  await tool('computer_click').execute?.({ ref: 'e2', snapshotId: 4 });
  expect(action).toHaveBeenLastCalledWith(
    dot.id,
    'click',
    { ref: 'e2', snapshotId: 4 },
    'agent',
    expect.anything(),
  );
  const approval = request(workspace, dot.id);
  workspace.approvals.decide(approval.id, 'approved');
  await tool('computer_click').execute?.({
    ref: 'e1',
    snapshotId: 4,
    approval_id: approval.id,
  });
  expect(action).toHaveBeenLastCalledWith(
    dot.id,
    'click',
    { ref: 'e1', snapshotId: 4 },
    'agent',
    expect.anything(),
  );
});

it('keeps autonomous Dots ungated', async () => {
  const action = vi.fn(async () => ({ ok: true }));
  const tools = computerTools(
    { action } as unknown as ComputerService,
    'dot',
    () => {},
    new AbortController().signal,
  );
  await tools
    .find((item) => item.name === 'computer_exec')!
    .execute?.({ command: 'git push' });
  expect(action).toHaveBeenCalledOnce();
});
