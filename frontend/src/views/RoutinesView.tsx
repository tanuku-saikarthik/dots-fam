import { useState } from 'react';
import { Pause, Play, RotateCw, Trash2 } from 'lucide-react';
import { api, relative, when, type Activity, type AppState, type Task } from '../api';
import { browserZone, CRON_PRESETS, describeCron } from '../cron';
import { DotMark } from '../components/DotMark';

function threadName(state: AppState, threadId: string, fallback?: string) {
  const thread = state.threads.find((t) => t.id === threadId);
  const dot = state.dots.find((d) => d.id === thread?.dot_id);
  return { dot, title: thread?.title ?? fallback ?? 'Conversation' };
}

function TaskRow({ task, state, reload }: { task: Task; state: AppState; reload: () => void }) {
  const { dot, title } = threadName(state, task.thread_id, task.thread_title);
  const act = (action: string) => void api(`/tasks/${task.id}/action`, 'POST', { action }).then(reload);
  const label = task.cron
    ? `${describeCron(task.cron)} (${task.timezone})`
    : task.origin === 'trigger'
      ? 'From a webhook'
      : task.origin === 'team'
        ? 'Team update'
        : task.interval_seconds
          ? `Every ${Math.round(task.interval_seconds / 60)} min`
          : 'One time';
  return (
    <div className="list-item">
      <DotMark dot={dot} size="s" />
      <div className="grow">
        <strong>{task.prompt.split('\n')[0].slice(0, 110)}</strong>
        <p className="muted small">
          {label} in “{title}”
          {task.next_run_at ? `. Next run ${when(task.next_run_at)}` : ''}
        </p>
        {task.error && <p className="error small">{task.error}</p>}
      </div>
      <span className={`status ${task.status}`}>{task.status}</span>
      {task.cron && task.status !== 'paused' && task.status !== 'cancelled' && (
        <button className="button ghost" onClick={() => act('pause')} aria-label="Pause routine">
          <Pause size={15} />
        </button>
      )}
      {task.cron && (task.status === 'paused' || task.status === 'cancelled') && (
        <button className="button ghost" onClick={() => act('resume')} aria-label="Resume routine">
          <Play size={15} />
        </button>
      )}
      <button className="button ghost" onClick={() => act('run')} aria-label="Run now" disabled={task.status === 'running'}>
        <RotateCw size={15} />
      </button>
      {task.status !== 'cancelled' && (
        <button className="button ghost danger" onClick={() => act('cancel')} aria-label="Cancel">
          <Trash2 size={15} />
        </button>
      )}
    </div>
  );
}

export function NewRoutine({
  state,
  threadId,
  onCreated,
}: {
  state: AppState;
  threadId?: string;
  onCreated: () => void;
}) {
  const [thread, setThread] = useState(threadId ?? state.threads[0]?.id ?? '');
  const [prompt, setPrompt] = useState('');
  const [cron, setCron] = useState(CRON_PRESETS[0].cron);
  const [zone, setZone] = useState(state.setup.timezone !== 'UTC' ? state.setup.timezone : browserZone());
  const [error, setError] = useState('');
  return (
    <form
      className="panel stack"
      onSubmit={async (e) => {
        e.preventDefault();
        setError('');
        try {
          await api('/tasks', 'POST', { thread_id: thread, prompt, cron, timezone: zone });
          setPrompt('');
          onCreated();
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Could not create the routine.');
        }
      }}
    >
      <h2>New routine</h2>
      <label className="field">
        <span>Conversation</span>
        <select value={thread} onChange={(e) => setThread(e.target.value)} required>
          <option value="" disabled>
            Choose a conversation
          </option>
          {state.threads.map((t) => (
            <option key={t.id} value={t.id}>
              {state.dots.find((d) => d.id === t.dot_id)?.name}: {t.title}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>What should happen each time</span>
        <textarea
          rows={3}
          value={prompt}
          required
          minLength={3}
          placeholder="Read the Launch Brief, check new signals with Mara, and post a briefing page."
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <div className="form-grid">
        <label className="field">
          <span>When</span>
          <select value={CRON_PRESETS.some((p) => p.cron === cron) ? cron : 'custom'} onChange={(e) => e.target.value !== 'custom' && setCron(e.target.value)}>
            {CRON_PRESETS.map((p) => (
              <option key={p.cron} value={p.cron}>
                {p.label}
              </option>
            ))}
            <option value="custom">Custom (cron)</option>
          </select>
        </label>
        <label className="field">
          <span>Cron</span>
          <input value={cron} onChange={(e) => setCron(e.target.value)} required />
          <small>minute hour day month weekday</small>
        </label>
        <label className="field full">
          <span>Time zone</span>
          <input value={zone} onChange={(e) => setZone(e.target.value)} required />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div>
        <button className="button primary" disabled={!thread}>
          Create routine
        </button>
      </div>
    </form>
  );
}

function Webhooks({ state, activity, reload }: { state: AppState; activity: Activity; reload: () => void }) {
  const [name, setName] = useState('');
  const [thread, setThread] = useState(state.threads[0]?.id ?? '');
  const [prompt, setPrompt] = useState('');
  const [secret, setSecret] = useState<{ url: string; secret: string; name: string }>();
  const [error, setError] = useState('');
  return (
    <div className="stack">
      <form
        className="panel stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setError('');
          try {
            const created = await api<{ url: string; secret: string; trigger: { name: string } }>('/triggers', 'POST', {
              name,
              thread_id: thread,
              prompt,
            });
            setSecret({ url: created.url, secret: created.secret, name: created.trigger.name });
            setName('');
            setPrompt('');
            reload();
          } catch (err) {
            setError(err instanceof Error ? err.message : 'Could not create the webhook.');
          }
        }}
      >
        <h2>New webhook</h2>
        <p className="muted small">
          GitHub, Linear or any script can POST to it. Each event becomes a turn in the conversation you pick. Use the
          secret as a Bearer token, or as the GitHub or Linear signing secret.
        </p>
        <div className="form-grid">
          <label className="field">
            <span>Name</span>
            <input value={name} required maxLength={80} placeholder="New Linear blocker" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Conversation</span>
            <select value={thread} required onChange={(e) => setThread(e.target.value)}>
              <option value="" disabled>
                Choose a conversation
              </option>
              {state.threads.map((t) => (
                <option key={t.id} value={t.id}>
                  {state.dots.find((d) => d.id === t.dot_id)?.name}: {t.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field full">
            <span>What to do with each event</span>
            <textarea rows={2} value={prompt} required minLength={3} onChange={(e) => setPrompt(e.target.value)} />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <div>
          <button className="button primary" disabled={!thread}>
            Create webhook
          </button>
        </div>
        {secret && (
          <div className="secret" role="status">
            <strong>{secret.name}: copy the secret now, it is shown once.</strong>
            <code>{secret.url}</code>
            <code>{secret.secret}</code>
          </div>
        )}
      </form>
      {!!activity.triggers.length && (
        <div className="list">
          {activity.triggers.map((trigger) => {
            const { dot, title } = threadName(state, trigger.thread_id);
            return (
              <div className="list-item" key={trigger.id}>
                <DotMark dot={dot} size="s" />
                <div className="grow">
                  <strong>{trigger.name}</strong>
                  <p className="muted small">
                    Starts a turn in “{title}”. Fired {trigger.fire_count} times
                    {trigger.last_fired_at ? `, last ${relative(trigger.last_fired_at)}` : ''}.
                  </p>
                  <code className="small">/hooks/{trigger.id}</code>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={trigger.enabled}
                    onChange={(e) => void api(`/triggers/${trigger.id}`, 'PATCH', { enabled: e.target.checked }).then(reload)}
                  />
                  On
                </label>
                <button
                  className="button ghost"
                  onClick={async () => {
                    const { secret: next } = await api<{ secret: string }>(`/triggers/${trigger.id}/rotate`, 'POST');
                    setSecret({ url: `${location.origin}/hooks/${trigger.id}`, secret: next, name: trigger.name });
                  }}
                >
                  New secret
                </button>
                <button
                  className="button ghost danger"
                  aria-label={`Delete ${trigger.name}`}
                  onClick={() => window.confirm(`Delete ${trigger.name}?`) && void api(`/triggers/${trigger.id}`, 'DELETE').then(reload)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function RoutinesView({
  state,
  activity,
  reload,
  threadId,
}: {
  state: AppState;
  activity?: Activity;
  reload: () => void;
  threadId?: string;
}) {
  const [tab, setTab] = useState<'routines' | 'webhooks'>('routines');
  const routines = activity?.tasks.filter((t) => t.status !== 'cancelled' || t.cron) ?? [];
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Routines</h1>
          <p className="muted">Work that runs on a clock or when something happens, with your laptop closed.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'routines'} onClick={() => setTab('routines')}>
          Schedules and queue
        </button>
        <button role="tab" aria-selected={tab === 'webhooks'} onClick={() => setTab('webhooks')}>
          Webhooks
        </button>
      </div>
      {!activity ? (
        <p className="muted">Loading…</p>
      ) : tab === 'routines' ? (
        <div className="stack">
          {state.threads.length ? (
            <NewRoutine state={state} threadId={threadId} onCreated={reload} />
          ) : (
            <p className="muted">Start a conversation first; routines run inside one.</p>
          )}
          {routines.length ? (
            <div className="list">
              {routines.map((task) => (
                <TaskRow key={task.id} task={task} state={state} reload={reload} />
              ))}
            </div>
          ) : (
            <div className="empty">
              <h2>No routines yet</h2>
              <p>Try “Every day at 08:30: brief me on the launch” in a conversation with Vance.</p>
            </div>
          )}
        </div>
      ) : (
        <Webhooks state={state} activity={activity} reload={reload} />
      )}
    </div>
  );
}
