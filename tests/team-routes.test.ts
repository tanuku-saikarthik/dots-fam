import { afterEach, expect, it } from 'vitest';
import { Store } from '../src/server/store.js';
import { WorkspaceStore } from '../src/server/workspace.js';
import { Platform } from '../src/server/platform.js';
import { Runner } from '../src/server/runner.js';
import { createApp } from '../src/server/app.js';
import type { TeamState } from '../src/shared/types.js';

const cleanup: (() => void)[] = [];
afterEach(() => cleanup.splice(0).forEach((fn) => fn()));
function fixture() {
  const store = new Store(':memory:');
  const workspace = new WorkspaceStore(':memory:', 'owner');
  cleanup.push(() => {
    store.close();
    workspace.close();
  });
  const platform = new Platform(store, workspace, {
    baseUrl: 'https://example.com',
    voiceName: 'marin',
    slackUsers: [],
    runtimeUrl: '',
    timezone: 'Asia/Kolkata',
    publicUrl: 'https://dots.example.com/',
  });
  const config = { mode: 'live' as const, baseUrl: 'https://example.com' };
  const app = createApp({
    store,
    runner: new Runner(store, config),
    config,
    platform,
  });
  const call = async (path: string, method = 'GET', body?: unknown) => {
    const response = await app.request(`/api${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- route bodies vary per assertion
    return { status: response.status, body: (await response.json()) as any };
  };
  return { store, workspace, call };
}

it('installs the team with chosen models and edits team settings per Dot', async () => {
  const { workspace, call } = fixture();
  expect(
    (await call('/team/install', 'POST', { chiefModel: 'not a model' })).status,
  ).toBe(400);
  const installed = await call('/team/install', 'POST', {
    chiefModel: 'anthropic:claude-opus-4-5',
    workerModel: 'openrouter:openai/gpt-5-mini',
  });
  expect(installed.status).toBe(201);
  const vance = workspace.dots().find((dot) => dot.name === 'Vance')!;
  expect(vance).toMatchObject({
    model: 'anthropic:claude-opus-4-5',
    canDelegate: true,
  });
  const update = await call(`/dots/${vance.id}`, 'PUT', {
    name: vance.name,
    instructions: vance.instructions,
    researchAllowed: true,
    memoryAllowed: true,
    model: '',
    approvalMode: 'autonomous',
    canDelegate: false,
  });
  expect(update.status).toBe(200);
  expect(workspace.dot(vance.id)).toMatchObject({
    model: null,
    approvalMode: 'autonomous',
    canDelegate: false,
  });
  expect(
    (
      await call(`/dots/${vance.id}`, 'PUT', {
        name: vance.name,
        instructions: vance.instructions,
        researchAllowed: true,
        memoryAllowed: true,
        model: 'gpt 5',
      })
    ).status,
  ).toBe(400);
  const team = await call('/team');
  expect(team.body.roster).toHaveLength(5);
  expect(team.body.blueprints.map((item: { id: string }) => item.id)).toEqual([
    'launch-coordinator',
    'lead-desk',
    'security-auditor',
    'meeting-followup',
  ]);
  expect(team.body.hookBase).toBe('https://dots.example.com/hooks');
});

it('delivers approval decisions to the conversation that asked', async () => {
  const { workspace, store, call } = fixture();
  const dot = workspace.dots()[0];
  workspace.bindThread('thread', dot.id, 'Chat');
  const approval = workspace.approvals.request({
    dotId: dot.id,
    threadId: 'thread',
    delegationId: null,
    kind: 'send_message',
    summary: 'Send the recap to Ana',
    details: 'Hi Ana — recap attached.',
  });
  const state = (await call('/team')).body as TeamState & {
    pendingApprovals: number;
  };
  expect(state.pendingApprovals).toBe(1);
  const decided = await call(`/approvals/${approval.id}/decision`, 'POST', {
    decision: 'approved',
  });
  expect(decided.body).toMatchObject({
    delivered: true,
    approval: { status: 'approved' },
  });
  const [task] = store.tasks();
  expect(workspace.taskThread(task.id)).toBe('thread');
  expect(task.prompt).toContain(`approval_id "${approval.id}"`);
  expect(task.prompt).toContain('Hi Ana');
  expect(
    (
      await call(`/approvals/${approval.id}/decision`, 'POST', {
        decision: 'declined',
      })
    ).status,
  ).toBe(409);
});

it('routes a specialist approval back through its Chief of Staff', async () => {
  const { workspace, store, call } = fixture();
  await call('/team/install', 'POST', {});
  const vance = workspace.dots().find((dot) => dot.name === 'Vance')!;
  const cole = workspace.dots().find((dot) => dot.name === 'Cole')!;
  workspace.bindThread('vance', vance.id, 'Leads');
  const approval = workspace.approvals.request({
    dotId: cole.id,
    threadId: 'vance',
    delegationId: 'delegation-1',
    kind: 'send_message',
    summary: 'Send intro email to Acme CTO',
    details: 'Draft v2',
  });
  await call(`/approvals/${approval.id}/decision`, 'POST', {
    decision: 'declined',
  });
  const [task] = store.tasks();
  expect(workspace.taskThread(task.id)).toBe('vance');
  expect(task.prompt).toContain("DECLINED Cole's request");
});

it('creates routines with cron and time zone, and manages triggers', async () => {
  const { workspace, store, call } = fixture();
  const dot = workspace.dots()[0];
  workspace.bindThread('thread', dot.id, 'Chat');
  // Missing setup still blocks scheduling in this template.
  expect(
    (
      await call('/tasks', 'POST', {
        prompt: 'Morning brief',
        threadId: 'thread',
        cron: '30 8 * * *',
      })
    ).status,
  ).toBe(503);
  const task = store.createTask('Morning brief', null, {
    cron: '30 8 * * *',
    timezone: 'Asia/Kolkata',
  });
  expect(
    (
      await call(`/tasks/${task.id}/schedule`, 'PUT', {
        cron: '0 9 * * 1-5',
        timezone: 'Europe/Berlin',
      })
    ).body,
  ).toMatchObject({ cron: '0 9 * * 1-5', timezone: 'Europe/Berlin' });
  expect(
    (
      await call(`/tasks/${task.id}/schedule`, 'PUT', {
        cron: '0 9 * *',
      })
    ).status,
  ).toBe(400);
  expect(
    (await call(`/tasks/${task.id}/actions`, 'POST', { action: 'pause' })).body
      .status,
  ).toBe('paused');
  expect(
    (await call(`/tasks/${task.id}/actions`, 'POST', { action: 'resume' })).body
      .status,
  ).toBe('scheduled');

  expect(
    (
      await call('/triggers', 'POST', {
        name: 'X',
        threadId: 'nope',
        prompt: 'Go now.',
      })
    ).status,
  ).toBe(403);
  const created = await call('/triggers', 'POST', {
    name: 'PRs',
    threadId: 'thread',
    prompt: 'Audit the PR.',
  });
  expect(created.status).toBe(201);
  expect(created.body.url).toBe(
    `https://dots.example.com/hooks/${created.body.trigger.id}`,
  );
  expect(created.body.secret).toHaveLength(32);
  const rotated = await call(
    `/triggers/${created.body.trigger.id}/rotate`,
    'POST',
    {},
  );
  expect(rotated.body.secret).not.toBe(created.body.secret);
  expect(
    (
      await call(`/triggers/${created.body.trigger.id}`, 'PATCH', {
        enabled: false,
      })
    ).body.enabled,
  ).toBe(false);
  expect(
    (await call(`/triggers/${created.body.trigger.id}`, 'DELETE')).status,
  ).toBe(200);
  expect((await call('/delegations/unknown/cancel', 'POST', {})).status).toBe(
    409,
  );
});

it('requires the team before installing a blueprint', async () => {
  const { call } = fixture();
  expect((await call('/blueprints/lead-desk/install', 'POST', {})).status).toBe(
    409,
  );
  expect((await call('/blueprints/nope/install', 'POST', {})).status).toBe(404);
});
