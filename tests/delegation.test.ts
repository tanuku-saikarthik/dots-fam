import { afterEach, expect, it, vi } from 'vitest';
import { EventType, type RunAgentInput } from '@ag-ui/core';
import { lastValueFrom, toArray } from 'rxjs';
import { Store } from '../src/server/store.js';
import { WorkspaceStore } from '../src/server/workspace.js';
import { Platform } from '../src/server/platform.js';
import { DotAgent } from '../src/server/dot-agent.js';
import { DelegationManager } from '../src/server/delegation.js';
import { installTeam } from '../src/server/team.js';
import { validateRuntimeScope } from '../src/server/runtime-scope.js';
import type { PlatformConfig } from '../src/server/platform-config.js';
import { completion } from './fixtures/model-stream.js';

const cleanup: (() => void)[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  cleanup.splice(0).forEach((fn) => fn());
});
const config: PlatformConfig = {
  intelligenceKey: 'fixture',
  apiKey: 'fixture',
  model: 'custom-model',
  baseUrl: 'https://unused.invalid/v1',
  runtimeUrl: '',
  voiceName: 'marin',
  slackUsers: [],
};
function fixture() {
  const store = new Store(':memory:');
  const workspace = new WorkspaceStore(':memory:', 'owner');
  cleanup.push(() => {
    store.close();
    workspace.close();
  });
  const team = installTeam(workspace, { worker: 'openai:worker-model' });
  const dot = (name: string) =>
    workspace.dots().find((item) => item.name === name)!;
  return { store, workspace, team, dot };
}
const toolCall = (name: string, args: unknown, id = name) =>
  completion(
    {
      role: 'assistant',
      tool_calls: [
        {
          index: 0,
          id,
          type: 'function',
          function: { name, arguments: JSON.stringify(args) },
        },
      ],
    },
    'tool_calls',
  );
const say = (content: string) => completion({ role: 'assistant', content });

it('installs Team HQ, starter pages and five role cards once', () => {
  const { workspace, team, dot } = fixture();
  expect(team.created.map((item) => item.name)).toEqual([
    'Vance',
    'Mara',
    'Cole',
    'Rina',
    'Owen',
  ]);
  expect(dot('Vance')).toMatchObject({
    canDelegate: true,
    approvalMode: 'reversible',
    model: null,
  });
  expect(dot('Mara')).toMatchObject({
    canDelegate: false,
    model: 'openai:worker-model',
  });
  expect(workspace.pages.list(team.space.id).map((page) => page.title)).toEqual(
    expect.arrayContaining([
      'Launch Brief',
      'Approved Messaging',
      'Lead Staging',
    ]),
  );
  const again = installTeam(workspace);
  expect(again.created).toEqual([]);
  expect(again.existing).toHaveLength(5);
  expect(workspace.spaces().filter((s) => s.name === 'Team HQ')).toHaveLength(
    1,
  );
});

function manager(
  f: ReturnType<typeof fixture>,
  runWorker: ConstructorParameters<typeof DelegationManager>[0]['runWorker'],
  deliver = vi.fn(),
) {
  return new DelegationManager({
    delegations: f.workspace.delegations,
    dots: () => f.workspace.dots(),
    paused: () => f.store.settings().paused,
    modelFor: (dot) => dot.model,
    runWorker,
    deliver,
    maxConcurrent: 4,
  });
}

it('runs assignments in parallel and returns each deliverable', async () => {
  const f = fixture();
  let concurrent = 0;
  let peak = 0;
  const m = manager(f, async ({ to, delegation }) => {
    concurrent++;
    peak = Math.max(peak, concurrent);
    await new Promise((resolve) => setTimeout(resolve, 20));
    concurrent--;
    return `${to.name} did: ${delegation.brief}`;
  });
  const result = await m.dispatch(f.dot('Vance'), null, {
    assignments: [
      { dot: 'mara', brief: 'Find hiring signals at Acme.' },
      { dot: 'Owen', brief: 'Summarize pipeline risk this week.' },
    ],
  });
  expect(peak).toBe(2);
  expect(result.still_running).toEqual([]);
  expect(result.results.map((item) => [item.dot, item.status])).toEqual([
    ['Mara', 'completed'],
    ['Owen', 'completed'],
  ]);
  expect(result.results[0].result).toContain('Find hiring signals');
  const records = f.workspace.delegations.group(result.group_id);
  expect(records.every((item) => item.fromDotId === f.dot('Vance').id)).toBe(
    true,
  );
  expect(
    f.workspace.delegations
      .events(records.map((item) => item.id))
      .map((event) => event.text),
  ).toEqual(expect.arrayContaining(['Mara started.', 'Mara delivered.']));
});

it('refuses non-delegating Dots, unknown names and paused teams', async () => {
  const f = fixture();
  const m = manager(f, async () => 'ok');
  await expect(
    m.dispatch(f.dot('Mara'), null, {
      assignments: [{ dot: 'Cole', brief: 'Draft an email to Ana.' }],
    }),
  ).rejects.toThrow(/not allowed/);
  await expect(
    m.dispatch(f.dot('Vance'), null, {
      assignments: [{ dot: 'Nobody', brief: 'Draft an email to Ana.' }],
    }),
  ).rejects.toThrow(/Roster: Dot, Mara, Cole, Rina, Owen/);
  await expect(
    m.dispatch(f.dot('Vance'), null, {
      assignments: [{ dot: 'Vance', brief: 'Delegate to yourself.' }],
    }),
  ).rejects.toThrow(/No specialist/);
  f.store.updateSettings({ paused: true });
  await expect(
    m.dispatch(f.dot('Vance'), null, {
      assignments: [{ dot: 'Mara', brief: 'Find hiring signals.' }],
    }),
  ).rejects.toThrow(/paused/);
});

it('delivers work that outlives the wait to the delegating conversation', async () => {
  const f = fixture();
  f.workspace.bindThread('vance-thread', f.dot('Vance').id, 'Chat');
  let finish!: () => void;
  const deliver = vi.fn();
  const m = manager(
    f,
    () =>
      new Promise((resolve) => {
        finish = () => resolve('Prospect table with 3 rows.');
      }),
    deliver,
  );
  const result = await m.dispatch(
    f.dot('Vance'),
    'vance-thread',
    {
      assignments: [{ dot: 'Mara', brief: 'Scan career pages at Acme.' }],
      wait_seconds: 5,
    },
    AbortSignal.timeout(20),
  );
  expect(result.still_running).toEqual(['Mara']);
  expect(deliver).not.toHaveBeenCalled();
  finish();
  await vi.waitFor(() => expect(deliver).toHaveBeenCalledOnce());
  expect(deliver.mock.calls[0][0]).toBe('vance-thread');
  expect(deliver.mock.calls[0][1]).toContain('Prospect table with 3 rows.');
});

it('cancels running work from Activity and on pause', async () => {
  const f = fixture();
  const m = manager(
    f,
    ({ signal }) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason)),
      ),
  );
  const pending = m.dispatch(f.dot('Vance'), null, {
    assignments: [
      { dot: 'Mara', brief: 'Long research job one.' },
      { dot: 'Rina', brief: 'Long drafting job two.' },
    ],
    wait_seconds: 10,
  });
  await vi.waitFor(() => expect(m.activeCount).toBe(2));
  const [first] = f.workspace.delegations.list();
  expect(m.cancel(first.id)).toBe(true);
  m.cancelAll();
  const result = await pending;
  expect(result.results.map((item) => item.status)).toEqual([
    'cancelled',
    'cancelled',
  ]);
});

it('runs a Chief of Staff turn that delegates to an isolated specialist on its own model', async () => {
  const f = fixture();
  const platform = new Platform(f.store, f.workspace, config);
  const vance = f.dot('Vance');
  f.workspace.bindThread('chat', vance.id, 'Launch');
  const replies = [
    toolCall('delegate_tasks', {
      assignments: [
        {
          dot: 'Mara',
          brief: 'List two companies hiring AI engineers in Bengaluru.',
          expected_output: 'Markdown table with evidence URLs.',
        },
      ],
      wait_seconds: 30,
    }),
    say('| Acme | hiring | https://acme.example/jobs |'),
    say('Merged: Acme is hiring (source linked).'),
  ];
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async (url) =>
      String(url).includes('/chat/completions')
        ? replies.shift()!
        : new Response('{}'),
    );
  const modelCalls = () =>
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes('/chat/completions'),
    );
  const agent = new DotAgent(
    f.store,
    f.workspace,
    config,
    vance.id,
    false,
    platform.team,
  );
  const input: RunAgentInput = {
    threadId: 'chat',
    runId: 'run',
    state: {},
    context: [],
    messages: [
      {
        id: 'user',
        role: 'user',
        content: 'PRIVATE-CONTEXT: find prospects for our launch.',
      },
    ],
    tools: [],
    forwardedProps: {},
  };
  const events = await lastValueFrom(agent.run(input).pipe(toArray()));
  expect(events).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        type: EventType.TOOL_CALL_START,
        toolCallName: 'delegate_tasks',
      }),
      expect.objectContaining({
        type: EventType.TEXT_MESSAGE_CHUNK,
        delta: 'Merged: Acme is hiring (source linked).',
      }),
    ]),
  );
  const network = { mock: { calls: modelCalls() } };
  expect(network.mock.calls).toHaveLength(3);
  const chief = JSON.parse(String(network.mock.calls[0][1]?.body));
  expect(chief.model).toBe('custom-model');
  expect(JSON.stringify(chief.tools)).toContain('delegate_tasks');
  expect(JSON.stringify(chief.tools)).toContain('request_approval');
  expect(JSON.stringify(chief.messages)).toContain('Chief of Staff');
  const worker = JSON.parse(String(network.mock.calls[1][1]?.body));
  expect(worker.model).toBe('worker-model');
  expect(JSON.stringify(worker.messages)).toContain(
    'List two companies hiring AI engineers',
  );
  // Isolation: the specialist never sees the owner's conversation.
  expect(JSON.stringify(worker.messages)).not.toContain('PRIVATE-CONTEXT');
  expect(JSON.stringify(worker.tools)).not.toContain('delegate_tasks');
  const merged = JSON.parse(String(network.mock.calls[2][1]?.body));
  expect(JSON.stringify(merged.messages)).toContain('acme.example/jobs');
  const [record] = f.workspace.delegations.list();
  expect(record).toMatchObject({
    status: 'completed',
    threadId: 'chat',
    model: 'openai:worker-model',
    result: '| Acme | hiring | https://acme.example/jobs |',
  });
  // Delegated threads stay out of chat lists and the browser runtime.
  expect(
    f.workspace
      .conversations()
      .some((item) => item.id.startsWith('delegation:')),
  ).toBe(false);
  expect(f.workspace.isInternalThread(`delegation:${record.id}`)).toBe(true);
});

it('denies browser runtime access to internal delegation threads', () => {
  const f = fixture();
  const mara = f.dot('Mara');
  f.workspace.bindInternalThread('delegation:x', mara.id, 'Delegated');
  const request = new Request(
    `http://127.0.0.1/api/copilotkit/agent/${mara.id}/connect`,
    { method: 'POST' },
  );
  expect(() =>
    validateRuntimeScope(request, f.workspace, { threadId: 'delegation:x' }),
  ).toThrow();
});
