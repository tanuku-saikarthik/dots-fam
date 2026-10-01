import { afterEach, expect, it } from 'vitest';
import { Store } from '../src/server/store.js';
import { nextCronRun } from '../src/server/schedule.js';

const stores: Store[] = [];
afterEach(() => stores.splice(0).forEach((store) => store.close()));
const fixture = () => {
  const store = new Store(':memory:');
  stores.push(store);
  return store;
};

it('evaluates cron expressions in the requested time zone', () => {
  // 01:30 IST on 2 Oct → next 08:30 IST is 03:00 UTC the same day.
  const from = Date.parse('2026-10-01T20:00:00Z');
  expect(
    new Date(nextCronRun('30 8 * * *', 'Asia/Kolkata', from)).toISOString(),
  ).toBe('2026-10-02T03:00:00.000Z');
  expect(() => nextCronRun('* * * * * *', 'UTC')).toThrow(/five-field/);
  expect(() => nextCronRun('30 8 * * *', 'Mars/Base')).toThrow(/time zone/);
});

it('waits for the first occurrence instead of running a new routine at once', () => {
  const store = fixture();
  const task = store.createTask('Morning briefing', null, {
    cron: '30 8 * * *',
    timezone: 'Asia/Kolkata',
  });
  expect(task).toMatchObject({
    status: 'scheduled',
    cron: '30 8 * * *',
    timezone: 'Asia/Kolkata',
  });
  expect(store.claim(task.nextRunAt! - 1000)).toBeNull();
  const claim = store.claim(task.nextRunAt!)!;
  expect(claim.id).toBe(task.id);
  store.finish(
    claim,
    { text: 'done', sources: [], sample: false },
    task.nextRunAt! + 5000,
  );
  const next = store.task(task.id)!;
  expect(next.status).toBe('scheduled');
  expect(next.nextRunAt).toBe(task.nextRunAt! + 24 * 3600 * 1000);
});

it('keeps a routine on schedule after a failed run', () => {
  const store = fixture();
  const task = store.createTask('Digest', null, {
    cron: '0 9 * * 1',
    timezone: 'UTC',
  });
  const claim = store.claim(task.nextRunAt!)!;
  store.fail(claim, 'Model timed out.', task.nextRunAt! + 20_000);
  const failed = store.task(task.id)!;
  expect(failed.status).toBe('failed');
  expect(failed.nextRunAt).toBeGreaterThan(task.nextRunAt!);
  expect(store.claim(failed.nextRunAt!)?.id).toBe(task.id);
});

it('pauses, resumes, converts and removes routines', () => {
  const store = fixture();
  const task = store.createTask('Check', null, { cron: '0 * * * *' });
  expect(store.action(task.id, 'pause')).toMatchObject({
    status: 'paused',
    nextRunAt: null,
  });
  expect(store.claim(Date.now() + 10 * 3600 * 1000)).toBeNull();
  const resumed = store.action(task.id, 'resume')!;
  expect(resumed.status).toBe('scheduled');
  expect(resumed.nextRunAt).toBeGreaterThan(Date.now());
  const interval = store.schedule(task.id, 3600)!;
  expect(interval).toMatchObject({ cron: null, intervalSeconds: 3600 });
  const cron = store.scheduleCron(task.id, '15 10 * * 1-5', 'Europe/London')!;
  expect(cron).toMatchObject({
    cron: '15 10 * * 1-5',
    timezone: 'Europe/London',
    intervalSeconds: null,
  });
  expect(store.scheduleCron(task.id, null, null)).toMatchObject({
    cron: null,
    status: 'completed',
    nextRunAt: null,
  });
});

it('still runs plain queued tasks first and records trigger origin', () => {
  const store = fixture();
  store.createTask('Later', null, { cron: '* * * * *' });
  const queued = store.createTask('Now', null, { triggerId: 'trigger-1' });
  expect(queued.triggerId).toBe('trigger-1');
  expect(store.claim(Date.now() + 120_000)?.id).toBe(queued.id);
});
